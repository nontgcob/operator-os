# No-Docker Runtime + VLM-Guided SAM3 Tracking Implementation Log

## 1. Establish Development Logging System

### Progress Updates

[2026-07-17 13:39 HKT]
- Created this implementation log for the no-Docker runtime and SAM3 tracking workstream.
- Created `development-records/development-logging-guide.md` to define the logging format for future work.
- Adopted numbered tasks with newest progress updates first inside each task, without checkbox-style tracking.

### Challenges

[2026-07-17 13:39 HKT]
- The project already had development history files, but no standard format for live implementation logging.
- The requested format needs to make current status visible immediately, so chronological order inside each task must be newest first.

### Solution

[2026-07-17 13:39 HKT]
- Added a reusable guide and a focused workstream log under `development-records/`, matching the existing documentation directory.

## 2. Implement No-Docker Local Runtime

### Progress Updates

[2026-07-17 15:49 HKT]
- Added root `package.json` with `npm run setup`, `npm run setup:sam3`, `npm run dev`, and `npm run test`.
- Added `requirements-local.txt` for lightweight local backend dependencies and kept heavy SAM3/GPU dependencies behind `npm run setup:sam3`.
- Added `.env.local.example` with repo-relative local defaults and updated README/Makefile to make no-Docker development the primary path.
- Added `services/common/env.py` and wired Python services to load `.env` from the repo root.
- Generated root `package-lock.json` for the new root dev runner.

[2026-07-17 13:39 HKT]
- Starting implementation from the documented plan: root setup/dev commands, repo-relative environment defaults, and local service startup without Docker.

### Challenges

[2026-07-17 15:49 HKT]
- `npm install --package-lock-only` initially failed in the sandbox with `ENOTFOUND registry.npmjs.org`.
- Adding a root lockfile caused Next.js to warn about multiple lockfiles and inferred workspace roots.

[2026-07-17 13:39 HKT]
- The current primary run path is Docker Compose, and several default environment values point to Docker hostnames or `/app/...` paths.

### Solution

[2026-07-17 15:49 HKT]
- Retried npm registry access with approval and generated the root lockfile successfully.
- Set `turbopack.root` in `frontend/next.config.ts` so Next.js uses the intended frontend root despite the root package lockfile.

[2026-07-17 13:39 HKT]
- Use local defaults for the no-Docker workflow while keeping Docker configuration available as optional infrastructure.

## 3. Make Redis Optional For Local Development

### Progress Updates

[2026-07-17 15:49 HKT]
- Replaced import-time Redis usage in orchestrator and SAM3 service with local in-memory state by default.
- Added optional Redis initialization only when `USE_REDIS_STATE=true`.
- Updated worker task imports so the module can load without the `redis` package installed.
- Updated SAM3 tests to patch the new state abstraction instead of the old `redis_client`.

[2026-07-17 13:39 HKT]
- Starting implementation to prevent local imports and tests from failing when Redis is not installed or not running.

### Challenges

[2026-07-17 15:49 HKT]
- The Python suite previously failed during import when Redis was not installed, before tests could exercise the actual code.
- Tracking events were coupled to orchestrator-side Redis polling, which does not fit the local direct-service mode.

[2026-07-17 13:39 HKT]
- Orchestrator and SAM3 service currently import Redis at module load time and use Redis for conversation/tracking state.

### Solution

[2026-07-17 15:49 HKT]
- Added local `_MemoryState` implementations for orchestrator and SAM3 service.
- Made SAM3 service own local tracking job state and expose direct status/event endpoints, with orchestrator proxying those events in non-worker mode.

[2026-07-17 13:39 HKT]
- Add local in-memory state paths and make Redis usage explicit rather than mandatory.

## 4. Rewire Tracking To VLM-Generated Targets

### Progress Updates

[2026-07-17 15:49 HKT]
- Extended the VLM response parser to accept `tracking_prompt` and `tracking_annotations`.
- Updated the RAGVLM prompt to request tracking-specific target annotations and a concise SAM3 tracking prompt.
- Updated the frontend so tracking starts only after the VLM response is parsed.
- Updated tracking API payloads so the frontend can send the VLM-generated tracking prompt and annotations to the orchestrator/SAM3.
- Added parser test coverage for tracking prompt and tracking annotations.

[2026-07-17 13:39 HKT]
- Starting implementation so tracking starts after VLM response parsing and uses the VLM target prompt/annotations.

### Challenges

[2026-07-17 15:49 HKT]
- The old flow started SAM3 before the model response existed, so it could never use machine-generated target annotations.
- The parser originally discarded all fields except `answer` and `annotations`, so tracking-specific VLM output had nowhere to go.

[2026-07-17 13:39 HKT]
- Current frontend starts tracking before the VLM response exists, so SAM3 receives only the original question and user annotations.

### Solution

[2026-07-17 15:49 HKT]
- Made VLM-guided tracking a post-answer step: parse VLM output, render the answer, then start tracking only when the toggle is enabled and a valid target annotation exists.

[2026-07-17 13:39 HKT]
- Extend the VLM response shape and frontend parser, then start tracking from parsed VLM tracking fields when the tracking toggle is enabled.

## 5. Prevent Stale Tracking Overlays

### Progress Updates

[2026-07-17 15:49 HKT]
- Added `activeTrackingJobId`, tracking status, tracking error state, and an EventSource ref in the frontend.
- Clear old tracking overlays and close old EventSource connections when a new video or new question starts.
- Ignore tracking events whose `tracking_job_id` does not match the active job.
- Added SAM3 service `GET /tracking/events/{tracking_job_id}` and `GET /tracking/status/{tracking_job_id}` endpoints.
- Added richer SAM3 health output for checkpoint path, checkpoint existence, device, CUDA availability, and simulation status.

[2026-07-17 13:39 HKT]
- Starting implementation to isolate tracking jobs and prevent old overlays from appearing in later questions or sessions.

### Challenges

[2026-07-17 15:49 HKT]
- Stale overlays could remain visible because frontend overlay state was global and not tied to a specific tracking job.
- The initial EventSource cleanup change produced a TypeScript build error because the ref cleanup path needed more explicit typing.

[2026-07-17 13:39 HKT]
- Current frontend stores tracking overlays globally and does not track an active job ID, so stale EventSource messages can update the screen.

### Solution

[2026-07-17 15:49 HKT]
- Centralized EventSource cleanup in `closeTrackingEventSource()`, tracked active job IDs, and made every SAM3 update include `tracking_job_id`.
- Verified the frontend build after fixing the EventSource typing issue.

[2026-07-17 13:39 HKT]
- Add active tracking job IDs, clear overlays on new jobs/videos, and ignore events from non-active jobs.

## 6. Verify Implementation

### Progress Updates

[2026-07-17 15:49 HKT]
- Ran `python3 -m pytest tests`: 42 tests passed.
- Ran `npm --prefix frontend run build`: Next.js production build and TypeScript checks passed.
- Installed frontend dependencies locally to enable build verification.

### Challenges

[2026-07-17 15:49 HKT]
- Frontend dependencies were not installed in the workspace, so the build could not run until `npm --prefix frontend install` completed.
- `npm --prefix frontend install` reported two moderate audit findings; those were not auto-fixed because force fixes may introduce unrelated dependency churn.

### Solution

[2026-07-17 15:49 HKT]
- Installed dependencies with registry access approval, ran build/type checks, and left npm audit remediation as a separate dependency-maintenance task.
