from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "video-service"
        / "app"
        / "video_index.py"
    )
    spec = importlib.util.spec_from_file_location("operatoros_video_index", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_timeline_uses_two_second_segments_and_searches_transcript(tmp_path: Path) -> None:
    module = _load_module()
    frames = [
        {"timestamp": float(second), "path": str(tmp_path / f"frame-{second}.jpg")}
        for second in range(7)
    ]
    timeline = module.build_timeline(
        video_id="short-video",
        video_dir=tmp_path,
        transcript=[
            {"start": 0.0, "end": 1.8, "text": "Open the service panel."},
            {"start": 4.0, "end": 5.8, "text": "Press the red emergency stop."},
        ],
        frames=frames,
    )

    assert timeline["segment_seconds"] == 2.0
    assert [segment["start"] for segment in timeline["segments"]] == [0.0, 2.0, 4.0, 6.0]
    matches = module.search_timeline(tmp_path, "red emergency stop", top_k=1)
    assert matches[0]["start"] == 4.0
    assert "emergency stop" in matches[0]["transcript"]


def test_status_reports_prepared_index(tmp_path: Path) -> None:
    module = _load_module()
    module.build_timeline(
        video_id="v1",
        video_dir=tmp_path,
        transcript=[],
        frames=[{"timestamp": 0.0, "path": "frame.jpg"}],
    )
    status = module.read_status(tmp_path)
    assert status["state"] == "prepared"
    assert status["progress"] == 10

