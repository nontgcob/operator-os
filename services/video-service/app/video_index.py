from __future__ import annotations

import base64
import json
import math
import mimetypes
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

import httpx

INDEX_VERSION = 1
DEFAULT_SEGMENT_SECONDS = 2.0
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]+", re.IGNORECASE)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def index_path(video_dir: Path) -> Path:
    return video_dir / "timeline_index.json"


def status_path(video_dir: Path) -> Path:
    return video_dir / "timeline_status.json"


def search_path(video_dir: Path) -> Path:
    return video_dir / "timeline_search.sqlite3"


def write_status(video_dir: Path, **fields: Any) -> dict[str, Any]:
    current: dict[str, Any] = {"state": "not_started", "progress": 0, "error": None}
    path = status_path(video_dir)
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    current.update(fields)
    current["index_version"] = INDEX_VERSION
    _atomic_json(path, current)
    return current


def read_status(video_dir: Path) -> dict[str, Any]:
    path = status_path(video_dir)
    if not path.is_file():
        return {"state": "not_started", "progress": 0, "error": None, "index_version": INDEX_VERSION}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "error", "progress": 0, "error": "Timeline status is unreadable.", "index_version": INDEX_VERSION}
    return payload if isinstance(payload, dict) else {"state": "error", "progress": 0, "error": "Timeline status is invalid."}


def _overlaps(segment: dict[str, Any], start: float, end: float) -> bool:
    return float(segment.get("end", 0.0)) > start and float(segment.get("start", 0.0)) < end


def build_timeline(
    *,
    video_id: str,
    video_dir: Path,
    transcript: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    segment_seconds: float = DEFAULT_SEGMENT_SECONDS,
) -> dict[str, Any]:
    """Create a deterministic, rebuildable timeline from existing transcript and frame artifacts."""
    segment_seconds = max(1.0, float(segment_seconds))
    duration = max(
        [float(item.get("timestamp", 0.0)) for item in frames]
        + [float(item.get("end", 0.0)) for item in transcript]
        + [0.0]
    )
    segment_count = max(1, math.ceil((duration + 0.001) / segment_seconds))
    segments: list[dict[str, Any]] = []
    ordered_frames = sorted(frames, key=lambda item: float(item.get("timestamp", 0.0)))

    for index in range(segment_count):
        start = index * segment_seconds
        end = min(duration, start + segment_seconds) if duration else start + segment_seconds
        midpoint = start + ((end - start) / 2)
        matching_frames = [
            item for item in ordered_frames if start <= float(item.get("timestamp", 0.0)) < start + segment_seconds
        ]
        representative = min(
            matching_frames or ordered_frames or [{"timestamp": start, "path": ""}],
            key=lambda item: abs(float(item.get("timestamp", 0.0)) - midpoint),
        )
        matching_transcript = [item for item in transcript if _overlaps(item, start, end)]
        previous = [item for item in transcript if float(item.get("end", 0.0)) < start][-1:]
        following = [item for item in transcript if float(item.get("start", 0.0)) > end][:1]
        transcript_text = " ".join(str(item.get("text", "")).strip() for item in matching_transcript).strip()
        segments.append(
            {
                "segment_id": f"segment-{index + 1:05d}",
                "start": round(start, 3),
                "end": round(end, 3),
                "representative_timestamp": round(float(representative.get("timestamp", start)), 3),
                "representative_frame": str(representative.get("path", "")),
                "frame_timestamps": [round(float(item.get("timestamp", 0.0)), 3) for item in matching_frames],
                "transcript": transcript_text,
                "neighboring_transcript": {
                    "before": " ".join(str(item.get("text", "")).strip() for item in previous).strip(),
                    "after": " ".join(str(item.get("text", "")).strip() for item in following).strip(),
                },
                "summary": transcript_text or f"Video moment around {start:.1f} seconds.",
                "objects": [],
                "actions": [],
                "visible_text": [],
                "materials": [],
                "safety_warnings": [],
                "procedure_phase": "",
                "confidence": "transcript_only" if transcript_text else "unreviewed",
            }
        )

    payload = {
        "index_version": INDEX_VERSION,
        "video_id": video_id,
        "segment_seconds": segment_seconds,
        "duration": round(duration, 3),
        "overview": {
            "purpose": "",
            "summary": " ".join(segment["summary"] for segment in segments if segment["transcript"])[:4000],
            "chapters": [],
            "components": [],
            "materials": [],
            "safety_warnings": [],
        },
        "segments": segments,
    }
    _atomic_json(index_path(video_dir), payload)
    _write_search_index(video_dir, payload)
    write_status(video_dir, state="prepared", progress=10, error=None, segment_count=len(segments))
    return payload


def load_timeline(video_dir: Path) -> dict[str, Any]:
    path = index_path(video_dir)
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("Timeline index is invalid.")
    return payload


def _searchable_text(segment: dict[str, Any]) -> str:
    values = [
        segment.get("summary", ""),
        segment.get("transcript", ""),
        segment.get("procedure_phase", ""),
        " ".join(segment.get("objects", [])),
        " ".join(segment.get("actions", [])),
        " ".join(segment.get("visible_text", [])),
        " ".join(segment.get("materials", [])),
        " ".join(segment.get("safety_warnings", [])),
    ]
    return "\n".join(str(value) for value in values if value)


def _write_search_index(video_dir: Path, payload: dict[str, Any]) -> None:
    path = search_path(video_dir)
    temporary = path.with_suffix(".sqlite3.tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.execute("CREATE VIRTUAL TABLE timeline USING fts5(segment_id UNINDEXED, content)")
        connection.executemany(
            "INSERT INTO timeline(segment_id, content) VALUES (?, ?)",
            [(segment["segment_id"], _searchable_text(segment)) for segment in payload.get("segments", [])],
        )
        connection.commit()
    finally:
        connection.close()
    temporary.replace(path)


def _fallback_scores(query: str, segments: list[dict[str, Any]]) -> list[tuple[str, float]]:
    tokens = [token.lower() for token in TOKEN_RE.findall(query)]
    scores: list[tuple[str, float]] = []
    for segment in segments:
        haystack = _searchable_text(segment).lower()
        score = sum(haystack.count(token) for token in tokens)
        if score:
            scores.append((str(segment["segment_id"]), float(score)))
    return sorted(scores, key=lambda item: item[1], reverse=True)


def search_timeline(video_dir: Path, query: str, top_k: int = 4) -> list[dict[str, Any]]:
    payload = load_timeline(video_dir)
    segments = payload.get("segments", [])
    by_id = {str(segment.get("segment_id")): segment for segment in segments}
    tokens = list(dict.fromkeys(token.lower() for token in TOKEN_RE.findall(query)))
    ranked: list[tuple[str, float]] = []
    if tokens and search_path(video_dir).is_file():
        expression = " OR ".join(f'"{token}"' for token in tokens)
        try:
            connection = sqlite3.connect(search_path(video_dir))
            try:
                ranked = [
                    (str(segment_id), -float(score))
                    for segment_id, score in connection.execute(
                        "SELECT segment_id, bm25(timeline) FROM timeline WHERE timeline MATCH ? ORDER BY bm25(timeline) LIMIT ?",
                        (expression, max(top_k * 3, top_k)),
                    ).fetchall()
                ]
            finally:
                connection.close()
        except sqlite3.Error:
            ranked = []
    if not ranked:
        ranked = _fallback_scores(query, segments)
    if not ranked and segments:
        ranked = [(str(segment["segment_id"]), 0.0) for segment in segments[:top_k]]

    selected_ids: list[str] = []
    for segment_id, _score in ranked:
        if segment_id in by_id and segment_id not in selected_ids:
            selected_ids.append(segment_id)
        if len(selected_ids) >= top_k:
            break

    results: list[dict[str, Any]] = []
    for segment_id in selected_ids:
        segment = dict(by_id[segment_id])
        segment_index = segments.index(by_id[segment_id])
        neighbors = []
        for neighbor_index in (segment_index - 1, segment_index + 1):
            if 0 <= neighbor_index < len(segments):
                neighbor = segments[neighbor_index]
                neighbors.append(
                    {
                        "segment_id": neighbor.get("segment_id"),
                        "start": neighbor.get("start"),
                        "end": neighbor.get("end"),
                        "summary": neighbor.get("summary"),
                        "transcript": neighbor.get("transcript"),
                    }
                )
        segment["neighbors"] = neighbors
        results.append(segment)
    return results


def _frame_data_url(video_dir: Path, frame_value: str) -> str | None:
    if not frame_value:
        return None
    frame_path = Path(frame_value).expanduser().resolve()
    frames_root = (video_dir / "frames").resolve()
    if frames_root not in frame_path.parents or not frame_path.is_file():
        return None
    mime_type = mimetypes.guess_type(frame_path.name)[0] or "image/jpeg"
    return f"data:{mime_type};base64,{base64.b64encode(frame_path.read_bytes()).decode('ascii')}"


def add_frame_data(video_dir: Path, moments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for moment in moments:
        data_url = _frame_data_url(video_dir, str(moment.get("representative_frame", "")))
        enriched.append({**moment, **({"frame_data_url": data_url} if data_url else {})})
    return enriched


def _json_from_content(content: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
    candidate = fenced.group(1) if fenced else content
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        candidate = candidate[start : end + 1]
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("Video analysis response was not a JSON object.")
    return parsed


def _openrouter_json(
    *,
    api_key: str,
    model: str,
    prompt: str,
    images: list[tuple[float, str]],
    http_referer: str,
    app_title: str,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for timestamp, data_url in images:
        content.extend(
            [
                {"type": "text", "text": f"Frame timestamp: {timestamp:.2f} seconds"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        )
    request_body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "reasoning": {"effort": "low"},
    }
    with httpx.Client(timeout=180) as client:
        response = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": http_referer,
                "X-Title": app_title,
            },
            json=request_body,
        )
    response.raise_for_status()
    body = response.json()
    content_value = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content_value, str):
        raise ValueError("Video analysis model returned no text content.")
    return _json_from_content(content_value)


def enrich_timeline(
    *,
    video_dir: Path,
    api_key: str,
    model: str,
    http_referer: str,
    app_title: str,
    cancelled: Callable[[], bool] | None = None,
    batch_size: int = 6,
) -> dict[str, Any]:
    payload = load_timeline(video_dir)
    segments = payload.get("segments", [])
    if not api_key:
        write_status(
            video_dir,
            state="partial",
            progress=100,
            error=None,
            warning="OPENROUTER_API_KEY is missing; the timeline is searchable using transcript evidence only.",
        )
        return payload

    write_status(video_dir, state="analyzing", progress=10, error=None, warning=None)
    completed = 0
    for offset in range(0, len(segments), max(1, batch_size)):
        if cancelled and cancelled():
            write_status(video_dir, state="cancelled", progress=round((completed / max(1, len(segments))) * 100))
            return payload
        batch = segments[offset : offset + batch_size]
        images: list[tuple[float, str]] = []
        for segment in batch:
            data_url = _frame_data_url(video_dir, str(segment.get("representative_frame", "")))
            if data_url:
                images.append((float(segment.get("representative_timestamp", segment.get("start", 0.0))), data_url))
        requested = [
            {
                "segment_id": segment["segment_id"],
                "start": segment["start"],
                "end": segment["end"],
                "transcript": segment.get("transcript", ""),
                "neighboring_transcript": segment.get("neighboring_transcript", {}),
            }
            for segment in batch
        ]
        prompt = (
            "Analyze these timestamped frames from an industrial training video. Return ONLY JSON as "
            "{\"segments\":[{\"segment_id\":string,\"summary\":string,\"objects\":[string],"
            "\"actions\":[string],\"visible_text\":[string],\"materials\":[string],"
            "\"safety_warnings\":[string],\"procedure_phase\":string,\"confidence\":\"high|medium|low\"}]}. "
            "Describe only visible or transcript-supported evidence, keep each summary concise, preserve exact labels, "
            "and do not invent procedure steps. Segment metadata follows:\n" + json.dumps(requested, ensure_ascii=False)
        )
        analysis = _openrouter_json(
            api_key=api_key,
            model=model,
            prompt=prompt,
            images=images,
            http_referer=http_referer,
            app_title=app_title,
        )
        returned = {
            str(item.get("segment_id")): item
            for item in analysis.get("segments", [])
            if isinstance(item, dict) and item.get("segment_id")
        }
        for segment in batch:
            update = returned.get(str(segment["segment_id"]))
            if not update:
                continue
            for key in (
                "summary",
                "objects",
                "actions",
                "visible_text",
                "materials",
                "safety_warnings",
                "procedure_phase",
                "confidence",
            ):
                if key in update:
                    segment[key] = update[key]
        completed += len(batch)
        _atomic_json(index_path(video_dir), payload)
        _write_search_index(video_dir, payload)
        write_status(video_dir, state="analyzing", progress=10 + round((completed / max(1, len(segments))) * 80))

    compact_segments = [
        {
            "segment_id": segment["segment_id"],
            "start": segment["start"],
            "end": segment["end"],
            "summary": segment.get("summary", ""),
            "procedure_phase": segment.get("procedure_phase", ""),
            "objects": segment.get("objects", []),
            "materials": segment.get("materials", []),
            "safety_warnings": segment.get("safety_warnings", []),
        }
        for segment in segments
    ]
    overview_prompt = (
        "Create a grounded overview of this industrial training video from its analyzed timeline. Return ONLY JSON as "
        "{\"overview\":{\"purpose\":string,\"summary\":string,\"chapters\":[{\"title\":string,"
        "\"start\":number,\"end\":number}],\"components\":[string],\"materials\":[string],"
        "\"safety_warnings\":[string]}}. Do not invent unsupported operations. Timeline:\n"
        + json.dumps(compact_segments, ensure_ascii=False)
    )
    overview = _openrouter_json(
        api_key=api_key,
        model=model,
        prompt=overview_prompt,
        images=[],
        http_referer=http_referer,
        app_title=app_title,
    ).get("overview")
    if isinstance(overview, dict):
        payload["overview"] = overview
    _atomic_json(index_path(video_dir), payload)
    _write_search_index(video_dir, payload)
    write_status(video_dir, state="ready", progress=100, error=None, warning=None, segment_count=len(segments))
    return payload
