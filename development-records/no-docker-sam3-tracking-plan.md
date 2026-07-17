# OperatorOS No-Docker Runtime + Reliable VLM-Guided SAM3 Tracking Plan

## Summary

Make OperatorOS run locally without Docker as the default workflow, then fix SAM3 tracking so it starts from the paused frame and follows the object selected by the VLM, not stale state, simulation output, or a generic question prompt.

Default decisions:

- Run app with one root command: `npm run dev`
- Keep Docker files available, but no longer make Docker the default path
- No Redis required for local development
- Keep the frontend "Enable SAM3 Tracking" toggle
- When tracking is enabled, start SAM3 only after the VLM returns a trackable target

## Key Implementation Changes

### Local No-Docker Runtime

- Add a root `package.json` with:
  - `npm run dev`: starts frontend, orchestrator, RAGVLM service, video service, and SAM3 service
  - `npm run setup`: installs frontend deps and documents/executes Python env setup
- Use repo-relative local paths by default:
  - `VIDEO_DATA_DIR=./data/video`
  - `RAGVLM_DOCUMENT_DIR=./data/ragvlm/documents`
  - `SAM3_VIDEO_ROOT=./data/video`
  - `SAM3_CHECKPOINT_PATH=./models/sam3.pt`
  - `RAGVLM_SERVICE_URL=http://localhost:8001`
  - `VIDEO_SERVICE_URL=http://localhost:8002`
  - `SAM3_SERVICE_URL=http://localhost:8003`
  - `NEXT_PUBLIC_ORCHESTRATOR_URL=http://localhost:8000`
- Add a local `.env` loading path for Python services so the same config works outside Docker.
- Update `README.md`, `.env.example`, and `Makefile` so the primary quick start is no-Docker.

### Remove Redis as a Required Local Dependency

- Add a small state abstraction for conversation and tracking jobs.
- Local default: in-memory state with job/session keys and cleanup.
- Optional production/worker mode: Redis remains supported only when explicitly enabled.
- Make Redis imports lazy/optional so local app and tests do not fail if the `redis` package or server is missing.
- Disable RQ worker path by default; keep it available only for explicit worker mode.

### SAM3 Tracking Event Architecture

- Move local tracking job state ownership into `sam3-service`.
- Add SAM3 service endpoints:
  - `POST /tracking/start`: creates a job and starts backend work
  - `GET /tracking/events/{tracking_job_id}`: streams progress and overlays
  - `GET /tracking/status/{tracking_job_id}`: returns latest job state for debugging
- Update orchestrator:
  - `POST /tracking/start` still creates/proxies the tracking request
  - `GET /tracking/events/{tracking_job_id}` proxies SAM3 service events in local mode
  - Redis polling is used only in explicit Redis/worker mode
- Every tracking payload must include `tracking_job_id`, `done`, `progress`, `backend`, `overlays`, and optional `error`.

### VLM-Guided Tracking Flow

- Extend VLM response format to support:
  - `answer`
  - `annotations`
  - `tracking_prompt`
  - `tracking_annotations`
- Update RAGVLM prompt so the model returns:
  - tight visual annotations for the answer
  - a concise SAM3 tracking prompt for the object of interest
  - a tracking annotation around the exact object to follow when possible
- Update frontend flow:
  - capture paused frame
  - call VLM
  - parse final VLM response
  - render answer/model annotations
  - if tracking toggle is enabled, start SAM3 using `tracking_prompt` and `tracking_annotations`
- Fallback logic:
  - if `tracking_annotations` exists, use it
  - else use valid model `annotations`
  - else do not start tracking and show "No trackable target returned by VLM"
- Remove the current behavior where tracking starts before the VLM answer using only the generic question/user annotations.

### Real SAM3 Backend Fixes

- Default SAM3 checkpoint path to `./models/sam3.pt` for local mode.
- Keep `SAM3_ALLOW_SIMULATION_FALLBACK=false` by default.
- Add clearer SAM3 `/health` output:
  - backend mode
  - checkpoint path
  - checkpoint exists
  - selected device
  - CUDA availability if detectable
  - simulation enabled/disabled
  - last backend error if any
- Make simulation unmistakable:
  - label overlays as simulation
  - expose simulation mode in health/status
  - show frontend warning if simulation is active
- Ensure clip extraction starts exactly at the requested paused timestamp.
- Prefer box-prompted SAM3 when a valid VLM target annotation exists.
- Use text-prompted SAM3 only when no valid box annotation is available.
- Ensure each job uses isolated temp files and isolated job state.

### Stale Overlay Prevention

- Add `activeTrackingJobId` in the frontend.
- Clear old tracking state when:
  - a new video loads
  - a new question starts
  - a new tracking job starts
- Ignore EventSource messages whose `tracking_job_id` does not match the active job.
- Render only overlays for the active tracking job.
- Close old EventSource connections before opening a new one.
- Do not reuse old SAM3 overlays after errors, reloads, or session changes.

## Test Plan

- Python unit tests:
  - app imports without Redis installed
  - in-memory conversation state works
  - in-memory tracking state stores and streams updates
  - SAM3 defaults to `./models/sam3.pt`
  - missing checkpoint returns clear degraded health
  - tracking payload includes `tracking_job_id`
  - stale job updates are ignored or isolated
  - VLM tracking annotations convert into SAM3 box prompts
- Frontend tests:
  - parser accepts `tracking_prompt` and `tracking_annotations`
  - invalid tracking annotations are rejected
  - tracking starts only after VLM response parsing
  - no tracking starts when no valid target exists
- Local Mac acceptance:
  - `npm run setup`
  - `npm run dev`
  - app starts without Docker and without Redis
  - upload/play/pause/chat works
  - SAM3 reports missing GPU/checkpoint/dependency clearly instead of showing stale overlays
- RTX 4090 acceptance:
  - clone repo
  - place `models/sam3.pt`
  - install deps
  - run `npm run dev`
  - pause on object
  - ask question
  - VLM marks target
  - SAM3 tracks that target from the current frame onward
  - no old scissors/old-session masks appear

## Deferred Work

- Improve RAG for diagrams/manual images after SAM3 and no-Docker runtime are stable.
- Evaluate YOLO or other object trackers only after the SAM3 flow is correctly wired and tested.
- Keep Docker support as optional infrastructure, but do not optimize it before local workflow is stable.

## Assumptions

- `models/sam3.pt` will exist on the GPU machine.
- The MacBook may not run real SAM3 successfully, but it must fail clearly and never show misleading stale overlays.
- OpenRouter remains the VLM provider for now.
- Local development should prioritize simple clone/setup/run behavior over production queue architecture.

## Current Project Status

### Already Done

- Product direction is defined: OperatorOS is a video-first multimodal assistant for industrial training, not a generic chatbot.
- Docker Compose stack exists for frontend, orchestrator, RAGVLM service, video service, SAM3 service, Redis, and optional worker.
- Frontend exists as a usable Next.js app with video upload, YouTube ingest, playback, pause state, annotation tools, document upload, model selector, transcript panel, chat, model overlays, and SAM3 toggles.
- Orchestrator exists as the API gateway for media ingest, video playback proxying, transcript windows, document ingestion/retrieval, chat streaming, and tracking start/events.
- Video service supports local MP4 ingest, YouTube ingest with `yt-dlp`, ffmpeg validation, video title metadata, Whisper transcription with fallback segments, 1 FPS frame extraction, byte-range video streaming, and transcript window retrieval.
- RAGVLM service supports OpenRouter VLM streaming, prompt assembly from video frame/transcript/docs/title/annotations, annotation normalization, document text extraction, document chunking, embeddings, and local JSON-backed retrieval.
- SAM3 service has a real Ultralytics SAM3 backend path, explicit simulation fallback, checkpoint configuration, video clipping from timestamp, annotation-to-box conversion, mask/box-to-overlay conversion, progress updates, and health reporting.
- Basic worker path exists for optional queue-based tracking delegation.
- Tests exist for video service behavior, RAGVLM prompts/retrieval/parsing, orchestrator proxy behavior, SAM3 helper behavior, worker delegation, and conversation memory.
- Presentation and development-history documentation exist under `presentation/` and `development-records/`.

### Yet To Be Completed, Ranked By Urgency And Importance

1. **Critical - Make no-Docker local development the default path.** Add root `npm run setup` and `npm run dev`, repo-relative env defaults, local `.env` loading, and updated docs so a new developer can clone, install, and run the app without Docker.

2. **Critical - Remove Redis as a required local dependency.** Add in-memory local state for conversations and tracking jobs, make Redis imports optional/lazy, and keep Redis/RQ only for explicit production or worker mode.

3. **Critical - Fix SAM3 tracking flow to use VLM-generated targets.** Stop starting tracking before the VLM response; parse VLM `tracking_prompt` and `tracking_annotations`, then start SAM3 from the paused frame only when tracking is enabled and a valid target exists.

4. **Critical - Prevent stale or cross-session tracking overlays.** Add active tracking job IDs, isolate SAM3 job state, clear old overlays on new questions/videos, close old EventSource connections, and ignore events from non-active jobs.

5. **Critical - Make real SAM3 usable on the RTX 4090 machine.** Default to `./models/sam3.pt`, improve health diagnostics, verify checkpoint/dependency/device readiness, ensure timestamp-based clip extraction is correct, and prefer box-prompted SAM3 from VLM target annotations.

6. **High - Add SAM3 service-owned local event/status endpoints.** Implement `GET /tracking/events/{tracking_job_id}` and `GET /tracking/status/{tracking_job_id}` in SAM3 service, then make orchestrator proxy those endpoints in local mode instead of polling Redis.

7. **High - Make simulation mode impossible to mistake for real SAM3.** Keep simulation disabled by default, label simulation overlays clearly, expose simulation mode in health/status, and show a frontend warning if simulation is active.

8. **High - Improve frontend tracking status and failure visibility.** Show backend mode, progress, active job, and clear error messages for missing checkpoint, missing GPU/CUDA, missing dependency, or no VLM target.

9. **High - Expand tests around the new runtime and tracking flow.** Cover no-Redis imports, in-memory state, VLM tracking response parsing, SAM3 target selection, stale job isolation, and frontend start-after-VLM behavior.

10. **Medium - Run full acceptance tests on both Mac and RTX 4090 machine.** On Mac, verify the app runs locally and fails clearly for real SAM3 limitations; on RTX 4090, verify `models/sam3.pt` tracks the VLM-selected object from the current frame onward.

11. **Medium - Improve RAG for manuals with diagrams and arrows.** After tracking is stable, evaluate document OCR/layout extraction, page image retrieval, diagram-aware chunking, or multimodal document retrieval.

12. **Medium - Improve chat and RAG UX.** Add live token rendering, markdown rendering, auto-scroll, document listing after reload, document deletion, and citation snippets.

13. **Low - Evaluate alternate object trackers.** Only after the SAM3 path is correctly wired and tested, compare YOLO or other trackers for box/text-prompted object tracking.

14. **Low - Keep Docker support maintained but secondary.** Docker can remain for deployment or reproducibility, but it should not block or define the default development workflow.
