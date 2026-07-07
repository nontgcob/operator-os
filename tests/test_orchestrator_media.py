from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def _load_orchestrator_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("orchestrator_main_media", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict[str, str]:
        return {"video_id": "video-1"}


class _FakeAsyncClient:
    instances: list["_FakeAsyncClient"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.requests: list[dict[str, Any]] = []
        self.timeout = kwargs.get("timeout", args[0] if args else None)
        self.instances.append(self)

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append({"url": url, **kwargs})
        return _FakeResponse()


def test_media_ingest_accepts_youtube_json(monkeypatch) -> None:
    module = _load_orchestrator_module()
    _FakeAsyncClient.instances = []
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    client = TestClient(module.app)

    response = client.post("/media/ingest", json={"youtube_url": "https://youtu.be/example"})

    assert response.status_code == 200
    assert response.json() == {"video_id": "video-1"}
    assert _FakeAsyncClient.instances[0].requests == [
        {
            "url": f"{module.VIDEO_SERVICE_URL}/media/ingest",
            "json": {"youtube_url": "https://youtu.be/example"},
        }
    ]


def test_media_ingest_preserves_file_upload(monkeypatch) -> None:
    module = _load_orchestrator_module()
    _FakeAsyncClient.instances = []
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    client = TestClient(module.app)

    response = client.post(
        "/media/ingest",
        files={"file": ("clip.mp4", b"fake mp4", "video/mp4")},
    )

    assert response.status_code == 200
    request = _FakeAsyncClient.instances[0].requests[0]
    assert request["url"] == f"{module.VIDEO_SERVICE_URL}/media/ingest"
    assert request["files"]["file"] == ("clip.mp4", b"fake mp4", "video/mp4")


def test_media_ingest_uses_configured_timeout(monkeypatch) -> None:
    monkeypatch.setenv("MEDIA_INGEST_TIMEOUT_SECONDS", "321.5")
    module = _load_orchestrator_module()
    _FakeAsyncClient.instances = []
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)
    client = TestClient(module.app)

    response = client.post("/media/ingest", json={"youtube_url": "https://youtu.be/example"})

    assert response.status_code == 200
    assert _FakeAsyncClient.instances[0].timeout == 321.5


def test_media_ingest_timeout_returns_actionable_504(monkeypatch) -> None:
    monkeypatch.setenv("MEDIA_INGEST_TIMEOUT_SECONDS", "45")
    module = _load_orchestrator_module()

    class TimeoutAsyncClient(_FakeAsyncClient):
        async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            raise module.httpx.ReadTimeout("video-service still processing")

    monkeypatch.setattr(module.httpx, "AsyncClient", TimeoutAsyncClient)
    client = TestClient(module.app)

    response = client.post("/media/ingest", json={"youtube_url": "https://youtu.be/example"})

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert "timed out after 45s" in detail
    assert "MEDIA_INGEST_TIMEOUT_SECONDS" in detail
    assert "WHISPER_ENABLED=false" in detail
