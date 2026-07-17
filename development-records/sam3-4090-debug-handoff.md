# SAM3 RTX 4090 Debug Handoff

## Snapshot

- Date: 2026-07-17 16:51 HKT
- Current commit: `d6d984c`
- Goal: validate and debug real SAM3 tracking on the RTX 4090 machine.
- Local Mac status: implementation is wired and tests/build pass, but real SAM3 has not been validated on GPU.

## What Was Just Implemented

- No-Docker local development workflow:
  - root `npm run setup`
  - root `npm run setup:sam3`
  - root `npm run dev`
  - local `.env` loading from repo root
  - repo-relative defaults in `.env.local.example`
- Redis is optional for local development:
  - orchestrator uses in-memory state by default
  - SAM3 service uses in-memory tracking state by default
  - Redis/RQ remain available only when explicitly enabled
- VLM-guided tracking flow:
  - frontend waits for VLM response before starting tracking
  - parser accepts `tracking_prompt` and `tracking_annotations`
  - frontend sends VLM tracking target/prompt to SAM3
  - tracking falls back to model `annotations` only if no `tracking_annotations` are returned
- Stale overlay protection:
  - frontend tracks `activeTrackingJobId`
  - old EventSource connections are closed on new video/question
  - tracking events from non-active jobs are ignored
  - overlays are cleared when new tracking starts
- SAM3 service changes:
  - owns local tracking job state
  - exposes `GET /tracking/events/{tracking_job_id}`
  - exposes `GET /tracking/status/{tracking_job_id}`
  - `/health` reports checkpoint path, checkpoint existence, CUDA availability, selected device, backend readiness, and simulation status

## Important Files To Read First

- `development-records/no-docker-sam3-tracking-plan.md`
- `development-records/no-docker-sam3-tracking-implementation-log.md`
- `development-records/development-logging-guide.md`
- `services/sam3-service/app/main.py`
- `services/sam3-service/app/tracking_backend.py`
- `frontend/src/app/page.tsx`
- `frontend/src/lib/parseResponse.ts`
- `services/ragvlm-service/app/prompts.py`

## Expected 4090 Setup

1. Clone or pull the repo at commit `d6d984c` or newer.
2. Put SAM3 weights at:

   ```text
   models/sam3.pt
   ```

3. Copy local env:

   ```bash
   cp .env.local.example .env
   ```

4. Fill at least:

   ```text
   OPENROUTER_API_KEY=...
   SAM3_CHECKPOINT_PATH=./models/sam3.pt
   SAM3_TRACKING_BACKEND=sam3
   SAM3_ALLOW_SIMULATION_FALLBACK=false
   USE_REDIS_STATE=false
   USE_WORKER_QUEUE=false
   ```

5. Install dependencies:

   ```bash
   npm run setup
   npm run setup:sam3
   ```

6. Start the app:

   ```bash
   npm run dev
   ```

7. Open:

   ```text
   http://localhost:3000
   ```

## First Health Checks On The 4090 Machine

Run these after `npm run dev` is up:

```bash
curl http://localhost:8003/health
```

Expected for real SAM3:

- `status` should be `ok`
- `backend` should be `sam3`
- `backend_ready` should be `true`
- `checkpoint_exists` should be `true`
- `checkpoint_path` should point to `./models/sam3.pt` or the resolved equivalent
- `cuda_available` should be `true`
- `simulation_enabled` should be `false`

If `backend_ready=false`, debug in this order:

1. Missing `models/sam3.pt`
2. Missing/incompatible `ultralytics`
3. Missing PyTorch/CUDA install
4. Wrong `SAM3_DEVICE`
5. Model load error from Ultralytics SAM3 predictor

## Manual Tracking Validation Flow

1. Open the app.
2. Upload or ingest a short video with a clear object.
3. Pause on a frame where the target object is visible.
4. Enable `SAM3 Tracking`.
5. Enable `SAM3 Overlay`.
6. Ask a question that causes the VLM to identify one visible object, for example:

   ```text
   What is this part and track it?
   ```

7. Confirm the VLM response includes a visible model annotation around the object.
8. Confirm the frontend starts a tracking job only after the answer is parsed.
9. Confirm the UI shows an active tracking job ID and progress/status.
10. Confirm overlays appear from the paused timestamp onward and do not show stale objects from old sessions.

## Debugging Tracking Jobs

When the frontend starts a tracking job, copy the tracking job ID shown in the UI.

Check status:

```bash
curl http://localhost:8003/tracking/status/<tracking_job_id>
```

Stream events:

```bash
curl -N http://localhost:8003/tracking/events/<tracking_job_id>
```

Useful fields:

- `tracking_job_id`
- `done`
- `progress`
- `backend`
- `overlays`
- `error.code`
- `error.message`

If the frontend shows no overlays but status has overlays, debug frontend rendering.

If status has no overlays and no error, debug SAM3 result conversion in:

```text
services/sam3-service/app/tracking_backend.py
```

Focus functions:

- `_run_sam3_sync`
- `_clip_from_timestamp`
- `annotation_boxes_xywh`
- `ultralytics_result_to_overlays`

## Known Remaining Risk

- Real SAM3 has not been run in this Mac environment.
- The current SAM3 implementation assumes Ultralytics SAM3 predictor APIs work with `sam3.pt`.
- Box-prompted tracking should be preferred when VLM returns target annotations, but this still needs real predictor validation.
- Text-prompted SAM3 is a fallback and may be less reliable.
- If SAM3 works but tracks the wrong object, inspect VLM `tracking_annotations` and `tracking_prompt` first.

## Commands Already Verified On Mac

```bash
python3 -m pytest tests
npm --prefix frontend run build
```

Result:

- Python tests: 42 passed
- Frontend build: passed

## Suggested Prompt For New Codex Session

Use this in a new Codex session on the 4090 machine:

```text
Continue debugging OperatorOS SAM3 tracking on this RTX 4090 machine.
Read development-records/sam3-4090-debug-handoff.md first, then read
development-records/no-docker-sam3-tracking-implementation-log.md.

The goal is to validate real SAM3 using models/sam3.pt, confirm /health,
run a manual tracking job from the paused frame, and fix any SAM3 runtime,
CUDA, checkpoint, predictor API, or overlay conversion issues.
Do not use Docker unless explicitly necessary.
```
