from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

SAM3_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "sam3-service"
if str(SAM3_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SAM3_SERVICE_ROOT))

tracking_backend = importlib.import_module("app.tracking_backend")


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[int, str]] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = (ttl, value)

    def get(self, key: str) -> str | None:
        item = self.values.get(key)
        return item[1] if item else None


def _load_sam3_main() -> Any:
    module_path = SAM3_SERVICE_ROOT / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("sam3_service_main", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["sam3_service_main"] = module
    spec.loader.exec_module(module)
    return module


def _tracking_request() -> dict[str, Any]:
    return {
        "tracking_job_id": "job-1",
        "session_id": "session-1",
        "video_id": "video-1",
        "timestamp": 12.0,
        "frame_data_url": "data:image/png;base64,stub",
        "question": "Where is the lever?",
        "segmentation_prompt": "Track the lever.",
        "annotations": [{"type": "rect", "x": 10, "y": 20, "width": 30, "height": 40}],
    }


def test_tracking_start_writes_unavailable_backend_error() -> None:
    module = _load_sam3_main()
    fake_redis = FakeRedis()
    module.state_client = fake_redis
    module._tracking_backend = tracking_backend.UnavailableTrackingBackend(
        backend="sam3",
        code="sam3_dependency_missing",
        message="SAM3 is not installed.",
    )

    response = TestClient(module.app).post("/tracking/start", json=_tracking_request())

    assert response.status_code == 200
    assert response.json() == {"status": "started", "tracking_job_id": "job-1"}
    _, raw_payload = fake_redis.values["tracking:job-1"]
    payload = json.loads(raw_payload)
    assert payload["done"] is True
    assert payload["overlays"] == []
    assert payload["error"]["code"] == "sam3_dependency_missing"


def test_clean_tracking_video_serves_the_untracked_slice(monkeypatch, tmp_path: Path) -> None:
    module = _load_sam3_main()
    clean_clip = tmp_path / "job-1.clean.mp4"
    clean_clip.write_bytes(b"clean-video-slice")
    monkeypatch.setenv("SAM3_RENDERED_VIDEO_ROOT", str(tmp_path))

    response = TestClient(module.app).get("/tracking/clean-video/job-1")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == b"clean-video-slice"


def test_completed_tracking_persists_a_fetchable_overlay_manifest(monkeypatch, tmp_path: Path) -> None:
    module = _load_sam3_main()
    module.state_client = FakeRedis()
    module._tracking_backend = tracking_backend.SimulationTrackingBackend(steps=2, delay_seconds=0)
    monkeypatch.setenv("SAM3_RENDERED_VIDEO_ROOT", str(tmp_path))
    client = TestClient(module.app)

    response = client.post("/tracking/start", json=_tracking_request())
    manifest_response = client.get("/tracking/overlays/job-1")

    assert response.status_code == 200
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["tracking_job_id"] == "job-1"
    assert manifest["overlay_count"] == 2
    assert len(manifest["overlays"]) == 2


def test_tracking_cancel_sets_signal_and_persists_cancelled_status() -> None:
    module = _load_sam3_main()
    fake_redis = FakeRedis()
    module.state_client = fake_redis
    cancel_event = threading.Event()
    module._tracking_cancel_events["job-1"] = cancel_event
    fake_redis.setex(
        "tracking:job-1",
        3600,
        json.dumps(
            {
                "tracking_job_id": "job-1",
                "done": False,
                "backend": "sam3",
                "target_progress": [
                    {
                        "target_id": "target-1",
                        "label": "Lever",
                        "progress": 42,
                        "stage": "tracking",
                        "color": "#facc15",
                    }
                ],
            }
        ),
    )

    response = TestClient(module.app).post("/tracking/cancel/job-1")

    assert response.status_code == 200
    assert cancel_event.is_set()
    payload = response.json()
    assert payload["done"] is True
    assert payload["cancelled"] is True
    assert payload["stage"] == "cancelled"


def test_simulation_backend_stops_when_cancelled() -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    job = tracking_backend.TrackingJob(**_tracking_request(), cancel_event=cancel_event)
    runner = tracking_backend.SimulationTrackingBackend(steps=3, delay_seconds=0)

    async def collect() -> list[dict[str, Any]]:
        return [update async for update in runner.track(job)]

    updates = asyncio.run(collect())

    assert len(updates) == 1
    assert updates[0]["cancelled"] is True
    assert updates[0]["done"] is True


def test_simulation_backend_requires_explicit_fallback_flag() -> None:
    disabled = tracking_backend.build_tracking_backend(
        tracking_backend.TrackingBackendConfig(
            backend="simulation",
            allow_simulation_fallback=False,
        )
    )
    assert disabled.status().ready is False
    assert disabled.status().code == "simulation_fallback_disabled"

    enabled = tracking_backend.build_tracking_backend(
        tracking_backend.TrackingBackendConfig(
            backend="simulation",
            allow_simulation_fallback=True,
            simulation_steps=1,
            simulation_delay_seconds=0,
        )
    )
    assert enabled.status().ready is True
    assert enabled.status().backend == "simulation"


def test_simulation_overlay_payload_shape() -> None:
    job = tracking_backend.TrackingJob(**_tracking_request())
    runner = tracking_backend.SimulationTrackingBackend(steps=1, delay_seconds=0)

    async def collect_updates() -> list[dict[str, Any]]:
        return [update async for update in runner.track(job)]

    updates = asyncio.run(collect_updates())

    final_payload = updates[-1]
    assert final_payload["done"] is True
    assert final_payload["progress"] == 100
    overlay = final_payload["overlays"][0]
    assert {"track_id", "label", "color", "timestamp", "points"}.issubset(overlay)
    assert overlay["target_id"] == "target-1"
    assert len(overlay["points"]) == 4
    assert all({"x", "y"} == set(point) for point in overlay["points"])


def test_sam3_outputs_are_converted_to_frontend_overlay_shape() -> None:
    overlays = tracking_backend.outputs_to_overlays(
        {
            "out_obj_ids": [7],
            "out_probs": [0.91],
            "out_boxes_xywh": [[0.1, 0.2, 0.3, 0.4]],
        },
        timestamp=3.5,
    )

    assert len(overlays) == 1
    overlay = overlays[0]
    assert overlay["track_id"] == "sam3-7"
    assert overlay["label"] == "SAM3 Track 7 (0.91)"
    assert overlay["color"] == "#67A552"
    assert overlay["timestamp"] == 3.5
    assert overlay["points"][0] == {"x": 10.0, "y": 20.0}
    assert overlay["points"][1] == {"x": pytest.approx(40.0), "y": 20.0}
    assert overlay["points"][2] == {"x": pytest.approx(40.0), "y": pytest.approx(60.0)}
    assert overlay["points"][3] == {"x": 10.0, "y": pytest.approx(60.0)}


def test_cached_video_predictor_state_is_reset_between_tracking_rounds() -> None:
    class FakePredictor:
        def __init__(self) -> None:
            self.inference_state = {"text_ids": [0], "text_prompt": ["first target"]}
            self.tracker = SimpleNamespace(inference_state={"obj_ids": [1]})
            self.prompts = {"text": "first target"}
            self.model = SimpleNamespace(text_embeddings={"first target": object()})

        def reset_prompts(self) -> None:
            self.prompts = {}
            self.model.text_embeddings = {}

    predictor = FakePredictor()

    tracking_backend.reset_video_predictor_state(predictor)

    assert predictor.inference_state == {}
    assert predictor.tracker.inference_state == {}
    assert predictor.prompts == {}
    assert set(predictor.model.text_embeddings) == {"first target"}


def test_disconnected_mask_regions_become_separate_polygons() -> None:
    mask = np.zeros((20, 20), dtype=np.float32)
    mask[2:8, 2:8] = 1
    mask[12:18, 12:18] = 1
    result = SimpleNamespace(
        orig_shape=(20, 20),
        masks=SimpleNamespace(data=np.asarray([mask])),
        boxes=SimpleNamespace(conf=np.asarray([0.9]), id=np.asarray([4]), xyxy=None),
    )

    overlays = tracking_backend.ultralytics_result_to_overlays(result, timestamp=1.25)

    assert len(overlays) == 2
    assert {overlay["track_id"] for overlay in overlays} == {"sam3-4"}
    assert all(len(overlay["points"]) >= 3 for overlay in overlays)
    assert all(
        0 <= point[axis] <= 100
        for overlay in overlays
        for point in overlay["points"]
        for axis in ("x", "y")
    )


def test_semantic_class_metadata_keeps_target_identity_stable() -> None:
    mask = np.ones((8, 8), dtype=np.float32)
    targets = [
        {"id": "person", "label": "Seated man", "prompt": "man on stool", "annotations": []},
        {"id": "ams", "label": "AMS unit", "prompt": "black AMS unit", "annotations": []},
    ]
    result = SimpleNamespace(
        orig_shape=(8, 8),
        masks=SimpleNamespace(data=np.asarray([mask, mask])),
        boxes=SimpleNamespace(
            conf=np.asarray([0.9, 0.8]),
            id=np.asarray([17, 3]),
            cls=np.asarray([1, 0]),
            xyxy=None,
        ),
    )

    overlays = tracking_backend.ultralytics_result_to_overlays(
        result, timestamp=2.0, targets=targets
    )

    assert {overlay["target_id"] for overlay in overlays} == {"person", "ams"}
    ams_overlay = next(overlay for overlay in overlays if overlay["target_id"] == "ams")
    assert ams_overlay["label"] == "AMS unit"
    assert ams_overlay["class_id"] == 1
    assert ams_overlay["track_id"] == "ams:sam3-17"
    assert ams_overlay["target_color"] == "#67A552"
