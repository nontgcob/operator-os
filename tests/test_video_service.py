from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient


def _load_video_module():
    module_path = (
        Path(__file__).resolve().parents[1] / "services" / "video-service" / "app" / "main.py"
    )
    spec = importlib.util.spec_from_file_location("video_service_main", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_transcript_window_filters_segments(tmp_path: Path) -> None:
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)
    client = TestClient(module.app)

    video_id = "v1"
    video_dir = module._video_dir(video_id)
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "a"},
        {"start": 5.0, "end": 10.0, "text": "b"},
        {"start": 10.0, "end": 15.0, "text": "c"},
    ]
    (video_dir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")

    response = client.get(
        "/transcript/window",
        params={"video_id": video_id, "timestamp": 10.0, "before": 3.0, "after": 2.0},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["start"] == 7.0
    assert payload["end"] == 12.0
    assert len(payload["segments"]) == 2
