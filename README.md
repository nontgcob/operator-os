# OperatorOS

OperatorOS is a video-first multimodal orchestration platform built around a reusable RAGVLM reasoning engine.

## Services

- `frontend/` - Next.js video player + chat + overlays
- `services/orchestrator/` - FastAPI orchestration API and event stream
- `services/ragvlm-service/` - FastAPI wrapper around multimodal RAGVLM inference
- `services/video-service/` - media ingestion, frame extraction, transcript indexing
- `services/sam3-service/` - async SAM3 segmentation/tracking API with explicit development simulation fallback
- `workers/` - background queue workers for tracking jobs

## Quick start

1. Copy `.env.example` to `.env` and fill required values.
2. Start infrastructure and services with Docker Compose:
   - `docker compose up --build`
3. Open `http://localhost:3000`.

## Demo configuration

- Set `OPENROUTER_API_KEY` before using contextual chat.
- RAGVLM document ingestion uses the upstream-style OpenRouter embeddings path with `RAGVLM_EMBEDDING_MODEL` (default `openai/text-embedding-3-small`) and stores a local JSON index under `RAGVLM_DOCUMENT_DIR`.
- Whisper transcription uses `WHISPER_MODEL` and falls back to deterministic timestamped segments if Whisper cannot run.
- YouTube URL ingestion is handled by `video-service` with `yt-dlp`, `ffmpeg`, yt-dlp EJS support, Deno, and the `curl-cffi` extra for optional yt-dlp-supported request impersonation. `YTDLP_JS_RUNTIME=deno:/usr/local/bin/deno` enables the installed Deno runtime explicitly, and `YTDLP_REMOTE_COMPONENTS=ejs:github` allows yt-dlp to fetch current EJS challenge solver scripts when the packaged components are stale. Check `http://localhost:8002/diagnostics/ytdlp` after rebuild to confirm runtime availability.
- Cookie-free public-video tuning is available through `YTDLP_EXTRACTOR_ARGS`, `YTDLP_YOUTUBE_PLAYER_CLIENTS` (for example `mweb,default`), `YTDLP_YOUTUBE_FETCH_PO_TOKEN` (`auto`, `always`, or `never`), and `YTDLP_PO_TOKEN_PROVIDER_ARGS` (for example `youtubepot-bgutilhttp:base_url=http://po-token-provider:4416`). PO Token Provider plugins are not bundled; install a maintained provider in a custom image or environment, then pass its provider-specific args with these env vars.
- Additional yt-dlp network tuning is available through `YTDLP_USER_AGENT`, `YTDLP_FORCE_IPV4`, `YTDLP_IMPERSONATE`, `YTDLP_RETRIES`, `YTDLP_FRAGMENT_RETRIES`, `YTDLP_EXTRACTOR_RETRIES`, and `YTDLP_SOCKET_TIMEOUT`. Leave `YTDLP_IMPERSONATE` blank unless you need yt-dlp's normal `--impersonate` support for public-video extraction.
- If YouTube asks to confirm you are not a bot, requires login, or returns HTTP 429/rate-limit errors, there is no reliable universal cookie-free fix. For login/account-gated cases, create `./data/ytdlp/`, export browser cookies to `./data/ytdlp/cookies.txt`, set `YTDLP_COOKIES_FILE=/app/data/ytdlp/cookies.txt`, then run `docker compose up --build video-service orchestrator frontend`. For rate limiting or poor IP reputation, retry later or use a different network. Do not commit exported cookies.
- Real SAM3 tracking requires the `sam3` package plus `SAM3_CHECKPOINT_PATH`, or `SAM3_ALLOW_HF_DOWNLOAD=true` for runtime weight download.
- Keep `SAM3_ALLOW_SIMULATION_FALLBACK=false` for real demos. Set it to `true` only for development when explicit simulated overlays are acceptable.
- Leave `USE_WORKER_QUEUE=false` for direct service calls, or set it to `true` to route tracking starts through the included RQ worker.

## Development

- API docs:
  - Orchestrator: `http://localhost:8000/docs`
  - RAGVLM service: `http://localhost:8001/docs`
  - Video service: `http://localhost:8002/docs`
  - SAM3 service: `http://localhost:8003/docs`
