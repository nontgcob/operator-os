from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

app = FastAPI(title="OperatorOS Video Service", version="0.1.0")

BASE_DIR = Path(os.getenv("VIDEO_DATA_DIR", "data/video"))
BASE_DIR.mkdir(parents=True, exist_ok=True)
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
_WHISPER_MODEL: Any | None = None
DEFAULT_YTDLP_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best"
DEFAULT_YTDLP_JS_RUNTIME = "deno:/usr/local/bin/deno"
COOKIE_SETUP_HELP = (
    "Export YouTube browser cookies to ./data/ytdlp/cookies.txt, set "
    "YTDLP_COOKIES_FILE=/app/data/ytdlp/cookies.txt, then rebuild/restart the Docker service."
)
YTDLP_ERROR_OUTPUT_LIMIT = 1600
YTDLP_TRUE_VALUES = {"1", "true", "yes", "on"}
YTDLP_FALSE_VALUES = {"", "0", "false", "no", "off"}
YTDLP_FETCH_PO_TOKEN_VALUES = {"auto", "always", "never"}
MEDIA_CHUNK_SIZE = 1024 * 1024


def _env_bool_config(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value in YTDLP_TRUE_VALUES:
        return True
    if value in YTDLP_FALSE_VALUES:
        return False
    raise RuntimeError(f"{name} must be true or false")


WHISPER_ENABLED = _env_bool_config("WHISPER_ENABLED", True)


def _video_dir(video_id: str) -> Path:
    path = BASE_DIR / video_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _source_path(video_id: str) -> Path:
    return _video_dir(video_id) / "source.mp4"


def _transcript_path(video_id: str) -> Path:
    return _video_dir(video_id) / "transcript.json"


def _frame_index_path(video_id: str) -> Path:
    return _video_dir(video_id) / "frame_index.json"


def _metadata_path(video_id: str) -> Path:
    return _video_dir(video_id) / "metadata.json"


def _title_from_upload_filename(filename: str | None) -> str:
    if not filename:
        return "Uploaded video"
    stem = Path(filename).stem.strip()
    return stem or "Uploaded video"


def _title_from_ytdlp_info(source_path: Path) -> str | None:
    info_path = source_path.with_suffix(".info.json")
    if not info_path.exists():
        return None
    try:
        payload = json.loads(info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def _write_video_metadata(
    video_id: str,
    *,
    title: str,
    source: str,
    source_label: str | None = None,
) -> dict[str, str]:
    metadata = {
        "video_id": video_id,
        "title": title.strip() or "Untitled video",
        "source": source,
    }
    if source_label:
        metadata["source_label"] = source_label
    _metadata_path(video_id).write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


def _read_video_metadata(video_id: str) -> dict[str, str] | None:
    path = _metadata_path(video_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return payload


def _load_whisper_model() -> Any:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        import whisper

        _WHISPER_MODEL = whisper.load_model(WHISPER_MODEL_NAME)
    return _WHISPER_MODEL


def _normalize_whisper_segments(raw_segments: list[dict[str, Any]]) -> list[dict[str, float | str]]:
    segments: list[dict[str, float | str]] = []
    for raw_segment in raw_segments:
        text = str(raw_segment.get("text", "")).strip()
        if not text:
            continue
        try:
            start = float(raw_segment["start"])
            end = float(raw_segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end < start:
            continue
        segments.append({"start": start, "end": end, "text": text})
    return segments


def _transcribe_with_whisper(video_path: Path) -> list[dict[str, float | str]]:
    if not WHISPER_ENABLED:
        return []
    try:
        model = _load_whisper_model()
        result = model.transcribe(str(video_path), fp16=False)
    except Exception:
        return []

    raw_segments = result.get("segments", []) if isinstance(result, dict) else []
    if not isinstance(raw_segments, list):
        return []
    return _normalize_whisper_segments(raw_segments)


def _fallback_transcript_segments(video_path: Path) -> list[dict[str, float | str]]:
    try:
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
    except OSError:
        return []
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
                "text": f"Fallback transcript segment from {t:.1f}s to {end:.1f}s.",
            }
        )
        t = end
    return segments


def _is_fallback_transcript(segments: list[dict[str, Any]]) -> bool:
    if not segments:
        return False
    return all(str(segment.get("text", "")).startswith("Fallback transcript segment") for segment in segments)


def _transcript_source(segments: list[dict[str, Any]]) -> str:
    if _is_fallback_transcript(segments):
        return "fallback"
    return "whisper" if segments else "empty"


def _extract_transcript(video_id: str, video_path: Path) -> list[dict[str, float | str]]:
    segments = _transcribe_with_whisper(video_path)
    if not segments:
        segments = _fallback_transcript_segments(video_path)
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
    out_path = _source_path(video_id)
    with out_path.open("wb") as handle:
        handle.write(file.file.read())
    return out_path


def _validate_youtube_url(youtube_url: str) -> str:
    parsed = urlparse(youtube_url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"} or not (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    ):
        raise HTTPException(status_code=400, detail="Provide a valid YouTube URL")
    return youtube_url


def _env_value(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _env_optional(name: str) -> str:
    return os.getenv(name, "").strip()


def _env_list(name: str, default: str = "") -> list[str]:
    raw_value = _env_value(name, default) if default else _env_optional(name)
    return [value.strip() for value in raw_value.split(",") if value.strip()]


def _configured_js_runtimes() -> list[str]:
    return _env_list("YTDLP_JS_RUNTIME", DEFAULT_YTDLP_JS_RUNTIME)


def _env_flag(name: str) -> bool:
    value = _env_optional(name).lower()
    if value in YTDLP_TRUE_VALUES:
        return True
    if value in YTDLP_FALSE_VALUES:
        return False
    raise HTTPException(status_code=400, detail=f"{name} must be true or false")


def _configured_cookies_file() -> Path | None:
    raw_path = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    if not raw_path:
        return None

    cookies_path = Path(raw_path)
    if not cookies_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=(
                f"YTDLP_COOKIES_FILE is configured but no cookies file was found at {cookies_path}. "
                f"{COOKIE_SETUP_HELP}"
            ),
        )
    return cookies_path


def _configured_extractor_args() -> list[str]:
    extractor_args: list[str] = []

    raw_extractor_args = _env_optional("YTDLP_EXTRACTOR_ARGS")
    if raw_extractor_args:
        extractor_args.append(raw_extractor_args)

    player_clients = _env_optional("YTDLP_YOUTUBE_PLAYER_CLIENTS")
    if player_clients:
        extractor_args.append(f"youtube:player_client={player_clients}")

    fetch_po_token = _env_optional("YTDLP_YOUTUBE_FETCH_PO_TOKEN").lower()
    if fetch_po_token:
        if fetch_po_token not in YTDLP_FETCH_PO_TOKEN_VALUES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "YTDLP_YOUTUBE_FETCH_PO_TOKEN must be one of "
                    f"{', '.join(sorted(YTDLP_FETCH_PO_TOKEN_VALUES))}"
                ),
            )
        extractor_args.append(f"youtube:fetch_pot={fetch_po_token}")

    po_token_provider_args = _env_optional("YTDLP_PO_TOKEN_PROVIDER_ARGS")
    if po_token_provider_args:
        if not po_token_provider_args.startswith("youtubepot-"):
            raise HTTPException(
                status_code=400,
                detail="YTDLP_PO_TOKEN_PROVIDER_ARGS must start with a youtubepot- provider prefix",
            )
        extractor_args.append(po_token_provider_args)

    return extractor_args


def _ytdlp_output_template(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.%(ext)s")


def _build_ytdlp_command(youtube_url: str, output_path: Path) -> list[str]:
    command = [
        "yt-dlp",
        "-f",
        _env_value("YTDLP_FORMAT", DEFAULT_YTDLP_FORMAT),
        "--merge-output-format",
        "mp4",
        "--no-playlist",
        "--no-progress",
        "--retries",
        _env_value("YTDLP_RETRIES", "5"),
        "--fragment-retries",
        _env_value("YTDLP_FRAGMENT_RETRIES", "5"),
        "--extractor-retries",
        _env_value("YTDLP_EXTRACTOR_RETRIES", "3"),
        "--socket-timeout",
        _env_value("YTDLP_SOCKET_TIMEOUT", "30"),
    ]

    if _env_flag("YTDLP_FORCE_IPV4"):
        command.append("--force-ipv4")

    for runtime in _configured_js_runtimes():
        command.extend(["--js-runtimes", runtime])

    for remote_component in _env_list("YTDLP_REMOTE_COMPONENTS"):
        command.extend(["--remote-components", remote_component])

    cookies_path = _configured_cookies_file()
    if cookies_path:
        command.extend(["--cookies", str(cookies_path)])

    user_agent = _env_optional("YTDLP_USER_AGENT")
    if user_agent:
        command.extend(["--user-agent", user_agent])

    impersonate = _env_optional("YTDLP_IMPERSONATE")
    if impersonate:
        command.extend(["--impersonate", impersonate])

    for extractor_args in _configured_extractor_args():
        command.extend(["--extractor-args", extractor_args])

    command.append("--write-info-json")
    command.extend(["-o", str(_ytdlp_output_template(output_path)), youtube_url])
    return command


def _normalize_downloaded_source(source_path: Path) -> None:
    if source_path.exists():
        return

    candidates = [
        candidate
        for candidate in sorted(source_path.parent.glob(f"{source_path.stem}*.mp4"))
        if candidate.is_file() and not candidate.name.endswith(".part")
    ]
    if len(candidates) == 1:
        candidates[0].replace(source_path)


def _ensure_playable_mp4(source_path: Path) -> None:
    if not source_path.exists():
        raise HTTPException(status_code=400, detail="yt-dlp completed but did not produce source.mp4")
    if source_path.stat().st_size <= 0:
        raise HTTPException(status_code=400, detail="yt-dlp produced an empty source.mp4")

    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(source_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="ffprobe is required to validate downloaded YouTube media before playback",
        ) from exc

    if probe.returncode != 0:
        detail = _trim_ytdlp_output(probe.stderr or probe.stdout)
        raise HTTPException(
            status_code=400,
            detail=f"yt-dlp produced source.mp4, but ffprobe could not read it as playable video. {detail}",
        )

    try:
        payload = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    streams = payload.get("streams", [])
    if not any(isinstance(stream, dict) and stream.get("codec_type") == "video" for stream in streams):
        raise HTTPException(status_code=400, detail="yt-dlp produced source.mp4 without a readable video stream")


def _trim_ytdlp_output(output: str) -> str:
    output = _dedupe_ytdlp_output(output)
    if len(output) <= YTDLP_ERROR_OUTPUT_LIMIT:
        return output
    return output[:YTDLP_ERROR_OUTPUT_LIMIT].rstrip() + "..."


def _dedupe_ytdlp_output(output: str) -> str:
    paragraphs: list[str] = []
    seen_paragraphs: set[str] = set()
    for paragraph in output.strip().split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph or paragraph in seen_paragraphs:
            continue
        seen_paragraphs.add(paragraph)
        paragraphs.append(paragraph)

    lines: list[str] = []
    seen_lines: set[str] = set()
    for line in "\n\n".join(paragraphs).splitlines():
        normalized = line.strip()
        if normalized and normalized in seen_lines:
            continue
        if normalized:
            seen_lines.add(normalized)
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _is_cookie_or_login_error(lower: str) -> bool:
    return any(
        phrase in lower
        for phrase in (
            "sign in to confirm",
            "not a bot",
            "login required",
            "private video",
            "members-only",
            "confirm your age",
            "age-restricted",
            "use --cookies",
            "use --cookies-from-browser",
        )
    )


def _is_rate_limit_error(lower: str) -> bool:
    return any(
        phrase in lower
        for phrase in (
            "http error 429",
            "too many requests",
            "rate limit",
            "rate-limit",
            "unusual traffic",
            "temporarily blocked",
            "ip address is blocked",
        )
    )


def _is_js_runtime_error(lower: str) -> bool:
    return any(
        phrase in lower
        for phrase in (
            "javascript runtime",
            "js runtime",
            "no supported javascript",
            "ejs",
            "jsinterp",
            "remote components",
        )
    )


def _is_po_token_error(lower: str) -> bool:
    return any(
        phrase in lower
        for phrase in (
            "po token",
            "po_token",
            "po-token",
            "proof of origin",
            "gvs",
            "pot provider",
        )
    )


def _format_ytdlp_error(output: str) -> str:
    trimmed = _trim_ytdlp_output(output) or "yt-dlp failed without error output."
    lower = trimmed.lower()
    guidance: list[str] = []
    has_js_runtime_error = _is_js_runtime_error(lower)

    if has_js_runtime_error:
        guidance.append(
            "Primary failure: yt-dlp could not execute a supported JavaScript/EJS challenge solver. "
            f"Rebuild the video-service image so Deno is installed at /usr/local/bin/deno, keep "
            f"YTDLP_JS_RUNTIME={DEFAULT_YTDLP_JS_RUNTIME}, and confirm with /diagnostics/ytdlp or "
            "`docker compose exec video-service deno --version`."
        )
    if _is_cookie_or_login_error(lower):
        cookie_prefix = "YouTube also requires" if has_js_runtime_error else "YouTube requires"
        guidance.append(
            f"{cookie_prefix} cookies or a signed-in browser session for this video/request. "
            f"{COOKIE_SETUP_HELP}"
        )
    if _is_rate_limit_error(lower):
        rate_limit_prefix = "YouTube also appears" if has_js_runtime_error else "YouTube appears"
        guidance.append(
            f"{rate_limit_prefix} to be rate limiting or distrusting this network/IP. "
            "Retry later, reduce request volume, or try a different network; cookies may still be required "
            "for account-gated videos."
        )
    if _is_po_token_error(lower):
        guidance.append(
            "YouTube public-video extraction appears blocked by Proof-of-Origin/GVS token requirements. "
            "Install a maintained yt-dlp PO Token Provider plugin in the image or environment, then configure "
            "YTDLP_PO_TOKEN_PROVIDER_ARGS and optionally YTDLP_YOUTUBE_PLAYER_CLIENTS/"
            "YTDLP_YOUTUBE_FETCH_PO_TOKEN. This does not bypass videos that require login."
        )

    if not guidance:
        guidance.append("YouTube download failed.")
    guidance.append(f"yt-dlp output:\n{trimmed}")
    return "\n\n".join(dict.fromkeys(guidance))


def _parse_js_runtime(runtime: str) -> tuple[str, str]:
    name, separator, configured_path = runtime.partition(":")
    executable = configured_path if separator else shutil.which(name) or name
    return name, executable


def _probe_executable(executable: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except OSError as exc:
        return {"executable": executable, "available": False, "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"executable": executable, "available": False, "error": "version probe timed out"}

    output = _trim_ytdlp_output(result.stdout or result.stderr)
    return {
        "executable": executable,
        "available": result.returncode == 0,
        "version": output,
        "returncode": result.returncode,
    }


def _ytdlp_diagnostics() -> dict[str, Any]:
    configured_runtimes = _configured_js_runtimes()
    runtime_checks = []
    for runtime in configured_runtimes:
        name, executable = _parse_js_runtime(runtime)
        runtime_checks.append(
            {
                "runtime": runtime,
                "name": name,
                **_probe_executable(executable),
            }
        )
    return {
        "yt_dlp": _probe_executable("yt-dlp"),
        "configured_js_runtimes": configured_runtimes,
        "js_runtimes": runtime_checks,
        "remote_components": _env_list("YTDLP_REMOTE_COMPONENTS"),
    }


async def _youtube_url_from_request(request: Request, form_value: str | None = None) -> str | None:
    if form_value and form_value.strip():
        return _validate_youtube_url(form_value.strip())

    query_value = request.query_params.get("youtube_url")
    if query_value and query_value.strip():
        return _validate_youtube_url(query_value.strip())

    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        return None

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None

    youtube_url = payload.get("youtube_url") if isinstance(payload, dict) else None
    if isinstance(youtube_url, str) and youtube_url.strip():
        return _validate_youtube_url(youtube_url.strip())
    return None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/diagnostics/ytdlp")
async def ytdlp_diagnostics() -> dict[str, Any]:
    return _ytdlp_diagnostics()


@app.post("/media/ingest")
async def ingest_media(
    request: Request,
    file: UploadFile = File(default=None),
    youtube_url: str | None = Form(default=None),
):
    video_id = str(uuid4())
    youtube_url = await _youtube_url_from_request(request, youtube_url)
    title = "Untitled video"
    source = "unknown"
    source_label: str | None = None
    if file:
        source_path = _save_upload(video_id, file)
        title = _title_from_upload_filename(file.filename)
        source = "upload"
        source_label = file.filename
    elif youtube_url:
        source_path = _source_path(video_id)
        result = subprocess.run(
            _build_ytdlp_command(youtube_url, source_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail=_format_ytdlp_error(result.stderr or result.stdout),
            )
        _normalize_downloaded_source(source_path)
        _ensure_playable_mp4(source_path)
        title = _title_from_ytdlp_info(source_path) or "YouTube video"
        source = "youtube"
        source_label = youtube_url
    else:
        raise HTTPException(status_code=400, detail="No input media provided")

    _extract_transcript(video_id, source_path)
    _extract_frames(video_id, source_path)
    metadata = _write_video_metadata(
        video_id,
        title=title,
        source=source,
        source_label=source_label,
    )
    return {"video_id": video_id, "title": metadata["title"], "source": metadata["source"]}


@app.get("/media/metadata")
async def media_metadata(video_id: str) -> dict[str, str]:
    metadata = _read_video_metadata(video_id)
    if metadata:
        return metadata
    if not _source_path(video_id).exists():
        raise HTTPException(status_code=404, detail="Video metadata not found")
    return {
        "video_id": video_id,
        "title": "Untitled video",
        "source": "unknown",
    }


def _parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    if not range_header:
        return None
    if not range_header.startswith("bytes=") or "," in range_header:
        raise HTTPException(
            status_code=416,
            detail="Unsupported byte range",
            headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{file_size}"},
        )

    raw_start, separator, raw_end = range_header[6:].partition("-")
    if separator != "-":
        raise HTTPException(
            status_code=416,
            detail="Invalid byte range",
            headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{file_size}"},
        )

    try:
        if raw_start:
            start = int(raw_start)
            end = int(raw_end) if raw_end else file_size - 1
        else:
            suffix_length = int(raw_end)
            if suffix_length <= 0:
                raise ValueError
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
    except ValueError:
        raise HTTPException(
            status_code=416,
            detail="Invalid byte range",
            headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{file_size}"},
        ) from None

    if start < 0 or start >= file_size or end < start:
        raise HTTPException(
            status_code=416,
            detail="Requested byte range is not satisfiable",
            headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{file_size}"},
        )

    return start, min(end, file_size - 1)


def _iter_file_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(MEDIA_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@app.get("/media/source")
async def media_source(video_id: str, request: Request):
    source_path = _source_path(video_id)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Video source not found")
    file_size = source_path.stat().st_size
    if file_size <= 0:
        raise HTTPException(status_code=422, detail="Video source is empty")

    common_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Type": "video/mp4",
    }
    byte_range = _parse_range_header(request.headers.get("range"), file_size)
    if byte_range:
        start, end = byte_range
        return StreamingResponse(
            _iter_file_range(source_path, start, end),
            status_code=206,
            media_type="video/mp4",
            headers={
                **common_headers,
                "Content-Length": str(end - start + 1),
                "Content-Range": f"bytes {start}-{end}/{file_size}",
            },
        )

    return StreamingResponse(
        _iter_file_range(source_path, 0, file_size - 1),
        media_type="video/mp4",
        headers={**common_headers, "Content-Length": str(file_size)},
    )


@app.get("/transcript/window")
async def transcript_window(video_id: str, timestamp: float, before: float = 30, after: float = 15):
    path = _transcript_path(video_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Transcript not found")
    segments = json.loads(path.read_text(encoding="utf-8"))
    start = max(0, timestamp - before)
    end = timestamp + after
    window = [segment for segment in segments if segment["end"] >= start and segment["start"] <= end]
    source = _transcript_source(segments)
    warning = None
    if source == "fallback":
        warning = (
            "This video has fallback timestamp transcript segments. "
            "Enable WHISPER_ENABLED=true and re-ingest the video for real speech transcription."
        )
    return {
        "timestamp": timestamp,
        "start": start,
        "end": end,
        "segments": window,
        "source": source,
        "whisper_enabled": WHISPER_ENABLED,
        "model": WHISPER_MODEL_NAME if WHISPER_ENABLED else None,
        "warning": warning,
    }


@app.get("/video/index")
async def video_index(video_id: str):
    path = _frame_index_path(video_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Frame index not found")
    return {"video_id": video_id, "frames": json.loads(path.read_text(encoding="utf-8"))}
