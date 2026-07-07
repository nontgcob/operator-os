from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


YTDLP_ENV_VARS = (
    "YTDLP_COOKIES_FILE",
    "YTDLP_USER_AGENT",
    "YTDLP_JS_RUNTIME",
    "YTDLP_REMOTE_COMPONENTS",
    "YTDLP_FORCE_IPV4",
    "YTDLP_IMPERSONATE",
    "YTDLP_RETRIES",
    "YTDLP_FRAGMENT_RETRIES",
    "YTDLP_EXTRACTOR_RETRIES",
    "YTDLP_SOCKET_TIMEOUT",
    "YTDLP_EXTRACTOR_ARGS",
    "YTDLP_YOUTUBE_PLAYER_CLIENTS",
    "YTDLP_YOUTUBE_FETCH_PO_TOKEN",
    "YTDLP_PO_TOKEN_PROVIDER_ARGS",
)


def _load_video_module():
    module_path = (
        Path(__file__).resolve().parents[1] / "services" / "video-service" / "app" / "main.py"
    )
    spec = importlib.util.spec_from_file_location("video_service_main", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _clear_ytdlp_env(monkeypatch) -> None:
    for name in YTDLP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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
    assert [segment["text"] for segment in payload["segments"]] == ["b", "c"]
    assert payload["source"] == "whisper"
    assert payload["warning"] is None


def test_transcript_window_reports_fallback_source(tmp_path: Path) -> None:
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)
    client = TestClient(module.app)

    video_id = "v-fallback"
    video_dir = module._video_dir(video_id)
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "Fallback transcript segment from 0.0s to 5.0s."},
    ]
    (video_dir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")

    response = client.get(
        "/transcript/window",
        params={"video_id": video_id, "timestamp": 2.0, "before": 1.0, "after": 1.0},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "fallback"
    assert "re-ingest" in payload["warning"]


def test_extract_transcript_uses_whisper_segments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("WHISPER_ENABLED", raising=False)
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"fake video")

    class DummyWhisperModel:
        def transcribe(self, path: str, fp16: bool = False):
            assert path == str(source_path)
            assert fp16 is False
            return {
                "segments": [
                    {"start": 1, "end": 2.5, "text": " first segment "},
                    {"start": 3, "end": 2, "text": "bad timing"},
                    {"start": 4, "end": 5, "text": " "},
                    {"start": 6, "end": 8, "text": "second segment"},
                ]
            }

    def fail_fallback(video_path: Path):
        raise AssertionError("fallback should not run when Whisper returns segments")

    monkeypatch.setattr(module, "_load_whisper_model", lambda: DummyWhisperModel())
    monkeypatch.setattr(module, "_fallback_transcript_segments", fail_fallback)

    segments = module._extract_transcript("v-whisper", source_path)

    assert segments == [
        {"start": 1.0, "end": 2.5, "text": "first segment"},
        {"start": 6.0, "end": 8.0, "text": "second segment"},
    ]
    written = json.loads((tmp_path / "v-whisper" / "transcript.json").read_text(encoding="utf-8"))
    assert written == segments


def test_extract_transcript_skips_whisper_when_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WHISPER_ENABLED", "false")
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"fake video")
    fallback_segments = [{"start": 0.0, "end": 5.0, "text": "Fallback transcript segment."}]

    def fail_load_model():
        raise AssertionError("Whisper model should not load when WHISPER_ENABLED=false")

    monkeypatch.setattr(module, "_load_whisper_model", fail_load_model)
    monkeypatch.setattr(module, "_fallback_transcript_segments", lambda video_path: fallback_segments)

    segments = module._extract_transcript("v-no-whisper", source_path)

    assert segments == fallback_segments
    written = json.loads((tmp_path / "v-no-whisper" / "transcript.json").read_text(encoding="utf-8"))
    assert written == fallback_segments


def test_extract_transcript_falls_back_when_whisper_unavailable(tmp_path: Path, monkeypatch) -> None:
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"fake video")

    class ProbeResult:
        stdout = "12.0"

    monkeypatch.setattr(module, "_transcribe_with_whisper", lambda video_path: [])
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: ProbeResult())

    segments = module._extract_transcript("v-fallback", source_path)

    assert segments == [
        {"start": 0.0, "end": 5.0, "text": "Fallback transcript segment from 0.0s to 5.0s."},
        {"start": 5.0, "end": 10.0, "text": "Fallback transcript segment from 5.0s to 10.0s."},
        {"start": 10.0, "end": 12.0, "text": "Fallback transcript segment from 10.0s to 12.0s."},
    ]
    written = json.loads((tmp_path / "v-fallback" / "transcript.json").read_text(encoding="utf-8"))
    assert written == segments


def test_ingest_media_accepts_youtube_json(tmp_path: Path, monkeypatch) -> None:
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setattr(module, "uuid4", lambda: "video-youtube")
    monkeypatch.setattr(module, "_extract_transcript", lambda video_id, source_path: [])
    monkeypatch.setattr(module, "_extract_frames", lambda video_id, source_path: [])
    captured = {}

    class DownloadResult:
        returncode = 0
        stdout = ""
        stderr = ""

    class ProbeResult:
        returncode = 0
        stdout = '{"streams": [{"codec_type": "video"}]}'
        stderr = ""

    def fake_run(args, **kwargs):
        if args[0] == "yt-dlp":
            captured["args"] = args
            captured["kwargs"] = kwargs
            Path(args[args.index("-o") + 1].replace("%(ext)s", "mp4")).write_bytes(b"fake mp4")
            return DownloadResult()
        if args[0] == "ffprobe":
            return ProbeResult()
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    client = TestClient(module.app)

    response = client.post(
        "/media/ingest",
        json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert response.status_code == 200
    assert response.json() == {"video_id": "video-youtube"}
    assert captured["args"][:5] == [
        "yt-dlp",
        "-f",
        module.DEFAULT_YTDLP_FORMAT,
        "--merge-output-format",
        "mp4",
    ]
    assert captured["args"][captured["args"].index("--js-runtimes") + 1] == module.DEFAULT_YTDLP_JS_RUNTIME
    assert captured["args"][captured["args"].index("--retries") + 1] == "5"
    assert captured["args"][captured["args"].index("--socket-timeout") + 1] == "30"
    assert "--cookies" not in captured["args"]
    assert captured["args"][-2:] == [
        str(tmp_path / "video-youtube" / "source.%(ext)s"),
        "https://www.youtube.com/watch?v=abc123",
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["check"] is False


def test_ingest_media_includes_configured_ytdlp_options(tmp_path: Path, monkeypatch) -> None:
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)
    _clear_ytdlp_env(monkeypatch)
    cookies_path = tmp_path / "cookies.txt"
    cookies_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("YTDLP_COOKIES_FILE", str(cookies_path))
    monkeypatch.setenv("YTDLP_USER_AGENT", "OperatorOS test agent")
    monkeypatch.setenv("YTDLP_JS_RUNTIME", "node:/usr/bin/node")
    monkeypatch.setenv("YTDLP_RETRIES", "7")
    monkeypatch.setenv("YTDLP_SOCKET_TIMEOUT", "45")
    monkeypatch.setenv("YTDLP_EXTRACTOR_ARGS", "youtube:player_client=default,ios")
    monkeypatch.setattr(module, "uuid4", lambda: "video-youtube-cookies")
    monkeypatch.setattr(module, "_extract_transcript", lambda video_id, source_path: [])
    monkeypatch.setattr(module, "_extract_frames", lambda video_id, source_path: [])
    captured = {}

    class DownloadResult:
        returncode = 0
        stdout = ""
        stderr = ""

    class ProbeResult:
        returncode = 0
        stdout = '{"streams": [{"codec_type": "video"}]}'
        stderr = ""

    def fake_run(args, **kwargs):
        if args[0] == "yt-dlp":
            captured["args"] = args
            Path(args[args.index("-o") + 1].replace("%(ext)s", "mp4")).write_bytes(b"fake mp4")
            return DownloadResult()
        if args[0] == "ffprobe":
            return ProbeResult()
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    client = TestClient(module.app)

    response = client.post(
        "/media/ingest",
        json={"youtube_url": "https://youtu.be/abc123"},
    )

    assert response.status_code == 200
    args = captured["args"]
    assert args[args.index("--cookies") + 1] == str(cookies_path)
    assert args[args.index("--user-agent") + 1] == "OperatorOS test agent"
    assert args[args.index("--js-runtimes") + 1] == "node:/usr/bin/node"
    assert args[args.index("--retries") + 1] == "7"
    assert args[args.index("--socket-timeout") + 1] == "45"
    assert args[args.index("--extractor-args") + 1] == "youtube:player_client=default,ios"


def test_build_ytdlp_command_includes_cookie_free_youtube_options(tmp_path: Path, monkeypatch) -> None:
    module = _load_video_module()
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setenv("YTDLP_JS_RUNTIME", "deno:/usr/local/bin/deno,node:/usr/bin/node")
    monkeypatch.setenv("YTDLP_REMOTE_COMPONENTS", "ejs:github,ejs:npm")
    monkeypatch.setenv("YTDLP_FORCE_IPV4", "true")
    monkeypatch.setenv("YTDLP_IMPERSONATE", "chrome")
    monkeypatch.setenv("YTDLP_EXTRACTOR_ARGS", "youtube:skip=dash")
    monkeypatch.setenv("YTDLP_YOUTUBE_PLAYER_CLIENTS", "mweb,default")
    monkeypatch.setenv("YTDLP_YOUTUBE_FETCH_PO_TOKEN", "always")
    monkeypatch.setenv(
        "YTDLP_PO_TOKEN_PROVIDER_ARGS",
        "youtubepot-bgutilhttp:base_url=http://po-token-provider:4416",
    )

    command = module._build_ytdlp_command(
        "https://www.youtube.com/watch?v=abc123",
        tmp_path / "source.mp4",
    )

    assert command.count("--js-runtimes") == 2
    assert command[command.index("--js-runtimes") + 1] == "deno:/usr/local/bin/deno"
    assert "node:/usr/bin/node" in command
    remote_components = [
        command[index + 1] for index, value in enumerate(command) if value == "--remote-components"
    ]
    assert remote_components == ["ejs:github", "ejs:npm"]
    assert "--force-ipv4" in command
    assert command[command.index("--impersonate") + 1] == "chrome"
    assert command[command.index("-o") + 1] == str(tmp_path / "source.%(ext)s")
    extractor_args = [
        command[index + 1] for index, value in enumerate(command) if value == "--extractor-args"
    ]
    assert extractor_args == [
        "youtube:skip=dash",
        "youtube:player_client=mweb,default",
        "youtube:fetch_pot=always",
        "youtubepot-bgutilhttp:base_url=http://po-token-provider:4416",
    ]


def test_build_ytdlp_command_rejects_invalid_po_token_options(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_video_module()
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setenv("YTDLP_YOUTUBE_FETCH_PO_TOKEN", "sometimes")

    with pytest.raises(module.HTTPException) as exc_info:
        module._build_ytdlp_command("https://youtu.be/abc123", tmp_path / "source.mp4")

    assert exc_info.value.status_code == 400
    assert "YTDLP_YOUTUBE_FETCH_PO_TOKEN" in exc_info.value.detail


def test_ingest_media_explains_cookie_required_error(tmp_path: Path, monkeypatch) -> None:
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)
    _clear_ytdlp_env(monkeypatch)

    class DownloadResult:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies"

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: DownloadResult())
    client = TestClient(module.app)

    response = client.post(
        "/media/ingest",
        json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert response.status_code == 400
    assert "requires cookies" in response.json()["detail"]
    assert "./data/ytdlp/cookies.txt" in response.json()["detail"]


def test_format_ytdlp_error_explains_js_runtime() -> None:
    module = _load_video_module()

    detail = module._format_ytdlp_error("ERROR: No supported JavaScript runtime found for EJS")

    assert detail.startswith("Primary failure:")
    assert "JavaScript/EJS challenge solver" in detail
    assert "YTDLP_JS_RUNTIME" in detail
    assert "requires a signed-in browser session" not in detail


def test_format_ytdlp_error_prioritizes_js_runtime_and_dedupes_output() -> None:
    module = _load_video_module()
    output = "\n".join(
        [
            "ERROR: No supported JavaScript runtime could be found.",
            "ERROR: No supported JavaScript runtime could be found.",
            "ERROR: HTTP Error 429: Too Many Requests",
            "ERROR: Sign in to confirm you're not a bot. Use --cookies",
        ]
    )

    detail = module._format_ytdlp_error(output)

    assert detail.startswith("Primary failure:")
    assert "YouTube also appears to be rate limiting" in detail
    assert "YouTube also requires cookies" in detail
    assert detail.count("ERROR: No supported JavaScript runtime could be found.") == 1


def test_format_ytdlp_error_explains_po_token_limitation() -> None:
    module = _load_video_module()

    detail = module._format_ytdlp_error("ERROR: Missing PO Token for GVS request")

    assert "Proof-of-Origin/GVS token requirements" in detail
    assert "YTDLP_PO_TOKEN_PROVIDER_ARGS" in detail
    assert "does not bypass videos that require login" in detail


def test_format_ytdlp_error_explains_rate_limiting() -> None:
    module = _load_video_module()

    detail = module._format_ytdlp_error("ERROR: HTTP Error 429: Too Many Requests")

    assert "rate limiting or distrusting this network/IP" in detail
    assert "different network" in detail
    assert "requires a signed-in browser session" not in detail


def test_ytdlp_diagnostics_reports_configured_runtime(monkeypatch) -> None:
    module = _load_video_module()
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setenv("YTDLP_JS_RUNTIME", "deno:/usr/local/bin/deno,node:/usr/bin/node")

    class ProbeResult:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 5
        return ProbeResult(f"{args[0]} version")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    diagnostics = module._ytdlp_diagnostics()

    assert diagnostics["configured_js_runtimes"] == [
        "deno:/usr/local/bin/deno",
        "node:/usr/bin/node",
    ]
    assert diagnostics["yt_dlp"]["available"] is True
    assert diagnostics["js_runtimes"][0]["executable"] == "/usr/local/bin/deno"
    assert diagnostics["js_runtimes"][0]["available"] is True
    assert diagnostics["js_runtimes"][1]["executable"] == "/usr/bin/node"


def test_ytdlp_diagnostics_endpoint(monkeypatch) -> None:
    module = _load_video_module()
    monkeypatch.setattr(
        module,
        "_ytdlp_diagnostics",
        lambda: {
            "yt_dlp": {"available": True},
            "configured_js_runtimes": [module.DEFAULT_YTDLP_JS_RUNTIME],
            "js_runtimes": [{"runtime": module.DEFAULT_YTDLP_JS_RUNTIME, "available": True}],
            "remote_components": [],
        },
    )
    client = TestClient(module.app)

    response = client.get("/diagnostics/ytdlp")

    assert response.status_code == 200
    assert response.json()["configured_js_runtimes"] == [module.DEFAULT_YTDLP_JS_RUNTIME]


def test_media_source_serves_saved_video(tmp_path: Path) -> None:
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)
    video_dir = module._video_dir("video-source")
    (video_dir / "source.mp4").write_bytes(b"fake mp4")
    client = TestClient(module.app)

    response = client.get("/media/source", params={"video_id": "video-source"})

    assert response.status_code == 200
    assert response.content == b"fake mp4"
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == "8"


def test_media_source_serves_byte_ranges(tmp_path: Path) -> None:
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)
    video_dir = module._video_dir("video-range")
    (video_dir / "source.mp4").write_bytes(b"0123456789")
    client = TestClient(module.app)

    response = client.get(
        "/media/source",
        params={"video_id": "video-range"},
        headers={"Range": "bytes=2-5"},
    )

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == "4"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_media_source_rejects_unsatisfiable_ranges(tmp_path: Path) -> None:
    module = _load_video_module()
    module.BASE_DIR = tmp_path
    module.BASE_DIR.mkdir(parents=True, exist_ok=True)
    video_dir = module._video_dir("video-range-invalid")
    (video_dir / "source.mp4").write_bytes(b"0123456789")
    client = TestClient(module.app)

    response = client.get(
        "/media/source",
        params={"video_id": "video-range-invalid"},
        headers={"Range": "bytes=99-100"},
    )

    assert response.status_code == 416
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == "bytes */10"


def test_ensure_playable_mp4_rejects_empty_download(tmp_path: Path) -> None:
    module = _load_video_module()
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"")

    with pytest.raises(module.HTTPException) as exc_info:
        module._ensure_playable_mp4(source_path)

    assert exc_info.value.status_code == 400
    assert "empty source.mp4" in exc_info.value.detail


def test_ensure_playable_mp4_requires_video_stream(tmp_path: Path, monkeypatch) -> None:
    module = _load_video_module()
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"not really a video")

    class ProbeResult:
        returncode = 0
        stdout = '{"streams": []}'
        stderr = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: ProbeResult())

    with pytest.raises(module.HTTPException) as exc_info:
        module._ensure_playable_mp4(source_path)

    assert exc_info.value.status_code == 400
    assert "without a readable video stream" in exc_info.value.detail
