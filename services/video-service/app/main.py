from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

app = FastAPI(title="OperatorOS Video Service", version="0.1.0")

BASE_DIR = Path(os.getenv("VIDEO_DATA_DIR", "data/video"))
BASE_DIR.mkdir(parents=True, exist_ok=True)


class YoutubeIngestRequest(BaseModel):
    youtube_url: str


def _video_dir(video_id: str) -> Path:
    path = BASE_DIR / video_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _transcript_path(video_id: str) -> Path:
    return _video_dir(video_id) / "transcript.json"


def _frame_index_path(video_id: str) -> Path:
    return _video_dir(video_id) / "frame_index.json"


def _extract_transcript(video_id: str, video_path: Path) -> list[dict[str, float | str]]:
    # Placeholder transcript extraction with deterministic pseudo segments.
    # This is intentionally lightweight and can be replaced with Whisper jobs.
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        duration = 0.0
    segments: list[dict[str, float | str]] = []
    t = 0.0
    while t < duration:
        end = min(duration, t + 5.0)
        segments.append(
            {
                "start": t,
                "end": end,
                "text": f"Auto transcript placeholder segment from {t:.1f}s to {end:.1f}s.",
            }
        )
        t = end
    _transcript_path(video_id).write_text(json.dumps(segments), encoding="utf-8")
    return segments


def _extract_frames(video_id: str, video_path: Path) -> list[dict[str, str | float]]:
    frame_dir = _video_dir(video_id) / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            "fps=1",
            str(frame_dir / "frame_%05d.jpg"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    index: list[dict[str, str | float]] = []
    for idx, frame in enumerate(sorted(frame_dir.glob("frame_*.jpg"))):
        index.append({"timestamp": float(idx), "path": str(frame)})
    _frame_index_path(video_id).write_text(json.dumps(index), encoding="utf-8")
    return index


def _save_upload(video_id: str, file: UploadFile) -> Path:
    out_path = _video_dir(video_id) / "source.mp4"
    with out_path.open("wb") as handle:
        handle.write(file.file.read())
    return out_path


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/media/ingest")
async def ingest_media(file: UploadFile = File(default=None), payload: YoutubeIngestRequest | None = None):
    video_id = str(uuid4())
    if file:
        source_path = _save_upload(video_id, file)
    elif payload and payload.youtube_url:
        source_path = _video_dir(video_id) / "source.mp4"
        result = subprocess.run(
            ["yt-dlp", "-f", "mp4", "-o", str(source_path), payload.youtube_url],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=400, detail=result.stderr or "yt-dlp failed")
    else:
        raise HTTPException(status_code=400, detail="No input media provided")

    _extract_transcript(video_id, source_path)
    _extract_frames(video_id, source_path)
    return {"video_id": video_id}


@app.get("/transcript/window")
async def transcript_window(video_id: str, timestamp: float, before: float = 30, after: float = 15):
    path = _transcript_path(video_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Transcript not found")
    segments = json.loads(path.read_text(encoding="utf-8"))
    start = max(0, timestamp - before)
    end = timestamp + after
    window = [segment for segment in segments if segment["end"] >= start and segment["start"] <= end]
    return {"timestamp": timestamp, "start": start, "end": end, "segments": window}


@app.get("/video/index")
async def video_index(video_id: str):
    path = _frame_index_path(video_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Frame index not found")
    return {"video_id": video_id, "frames": json.loads(path.read_text(encoding="utf-8"))}
