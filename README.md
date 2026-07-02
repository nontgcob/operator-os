# OperatorOS

OperatorOS is a video-first multimodal orchestration platform built around a reusable RAGVLM reasoning engine.

## Services

- `frontend/` - Next.js video player + chat + overlays
- `services/orchestrator/` - FastAPI orchestration API and event stream
- `services/ragvlm-service/` - FastAPI wrapper around multimodal RAGVLM inference
- `services/video-service/` - media ingestion, frame extraction, transcript indexing
- `services/sam3-service/` - async segmentation/tracking simulation API
- `workers/` - background queue workers for tracking jobs

## Quick start

1. Copy `.env.example` to `.env` and fill required values.
2. Start infrastructure and services with Docker Compose:
   - `docker compose up --build`
3. Open `http://localhost:3000`.

## Development

- API docs:
  - Orchestrator: `http://localhost:8000/docs`
  - RAGVLM service: `http://localhost:8001/docs`
  - Video service: `http://localhost:8002/docs`
  - SAM3 service: `http://localhost:8003/docs`
