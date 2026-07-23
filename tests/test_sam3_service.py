from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
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
    assert set(overlay) == {"track_id", "label", "color", "timestamp", "points"}
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
    assert {overlay["track_id"] for overlay in overlays} == {
        "sam3-4-contour-1",
        "sam3-4-contour-2",
    }
    assert all(len(overlay["points"]) >= 3 for overlay in overlays)
    assert all(
        0 <= point[axis] <= 100
        for overlay in overlays
        for point in overlay["points"]
        for axis in ("x", "y")
    )
