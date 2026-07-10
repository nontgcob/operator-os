# OperatorOS — Project Development History

**Repository:** [github.com/nontgcob/operator-os](https://github.com/nontgcob/operator-os)  
**Document purpose:** step-by-step record of how OperatorOS was built, mapped to git commits and development phases  
**Last updated:** 2026-07-10

---

## How to read this document

Each phase below corresponds to a meaningful slice of work. Where work was pushed to GitHub, the **commit hash** and **date** are listed so you can inspect the exact diff:

```bash
git show <commit-hash>
```

Phases are ordered chronologically. The final section covers work that exists locally but has **not been committed yet**.

---

## Project goal (constant across all phases)

OperatorOS is a **video-first AI assistant** for industrial machine training. It helps beginners who have never used a manufacturing machine before get a **heads-up introduction** from tutorial videos — without replacing professionally trained instructors.

Core user flow:

1. Load a machine tutorial (MP4 upload or YouTube link)
2. Watch and pause at a confusing moment
3. Draw on the video to highlight what you mean
4. Ask a question and get a grounded answer (text + visual overlays)
5. Optionally track a highlighted object forward in the video

---

## Timeline at a glance


| Phase | Date       | Commit                 | Summary                                                      |
| ----- | ---------- | ---------------------- | ------------------------------------------------------------ |
| 1     | 2026-07-02 | `ff389e6`              | Foundation — scaffold entire platform                        |
| 2     | 2026-07-07 | `ac9ea5a`              | Core features — video ingest, RAG, SAM3 backend, annotations |
| 3     | 2026-07-07 | `08a0f10`              | Orchestrator streaming hardening                             |
| 4     | 2026-07-07 | `1781f46`              | RAGVLM annotation tools + retrieval port                     |
| 5     | 2026-07-07 | `3133bea`              | Annotation toolbar polish                                    |
| 6     | 2026-07-07 | `39f98d5`              | Chat UI, document RAG, SketchVLM, transcripts                |
| 7     | 2026-07-10 | `1ece871`              | Real SAM3 (Ultralytics) + UI redesign                        |
| 8     | 2026-07-10 | *(local, uncommitted)* | Video title context + presentation materials                 |


---

## Phase 1 — Foundation and platform scaffold

**Commit:** `ff389e6` — *commit from terminal*  
**Date:** 2026-07-02  
**GitHub:** first commit on `main`

### What we did

Started OperatorOS from the product blueprint in `build_spec.md` and stood up the full microservices skeleton in one pass.

### Deliverables


| Area               | What was created                                                                        |
| ------------------ | --------------------------------------------------------------------------------------- |
| **Spec**           | `build_spec.md` — full product blueprint (928 lines)                                    |
| **Infrastructure** | `docker-compose.yml` — Redis, orchestrator, RAGVLM, video, SAM3, worker, frontend       |
| **Frontend**       | Next.js app with basic video player page, API client, types                             |
| **Orchestrator**   | FastAPI gateway — chat stream proxy, media ingest proxy, tracking start, session memory |
| **Video service**  | Basic MP4 upload, frame extraction stub, transcript stub, playback                      |
| **RAGVLM service** | Basic infer endpoint, prompts skeleton                                                  |
| **SAM3 service**   | Tracking job API skeleton                                                               |
| **Worker**         | RQ worker for optional background tracking jobs                                         |
| **Tests**          | Initial orchestrator memory + video service tests                                       |
| **Dev tooling**    | `Makefile`, `.env.example`, `.gitignore`, Dockerfiles for every service                 |


### Architecture established

```
Browser (Next.js) → Orchestrator → Video / RAGVLM / SAM3 services
                                  → Redis (tracking state)
```

### Outcome

The repo had a runnable Docker Compose stack and a clear separation of concerns, but most features were stubs or minimal implementations. The frontend could load and the services could talk to each other, but the full “pause → annotate → ask” experience was not yet complete.

---

## Phase 2 — Core feature build-out

**Commit:** `ac9ea5a` — *development in progress, saving code to switch to another PC*  
**Date:** 2026-07-07 (morning)  
**Size:** +3,696 / −237 lines across 34 files

### What we did

The largest single development push. Turned stubs into working backends and wired up the first real annotation and tracking paths.

### Deliverables

#### Video service (major expansion)

- **YouTube ingest** via `yt-dlp` with ffmpeg normalization
- **Whisper transcription** with fallback segments when Whisper is unavailable
- **Frame extraction** for downstream SAM3 use
- **Transcript window** endpoint (speech near a timestamp)
- **Media playback** proxy support
- Extensive test coverage (`tests/test_video_service.py` — ~496 new lines)

#### RAGVLM service

- **Document ingestion** — PDF, DOCX, markdown, text
- **Embedded retrieval** — local JSON index with OpenRouter embeddings
- **Annotation normalization** — structured 0–1000 coordinate system
- **Model families** — support for multiple VLM providers via OpenRouter
- **Response parsing** — JSON extraction from model output

#### SAM3 service

- `tracking_backend.py` — full tracking pipeline (518 lines)
- Simulation fallback for development when real weights are unavailable
- Redis-backed job progress
- Tests in `tests/test_sam3_service.py`

#### Frontend

- `AnnotationOverlay.tsx` — SVG drawing layer on the video
- `AnnotationControls.tsx` — first toolbar version
- Expanded `page.tsx` — ingest, playback, ask flow wiring
- API client updates for documents, tracking, chat

#### Orchestrator

- Document upload/retrieve proxy routes
- Media ingest with long timeout for YouTube downloads
- Tracking start + SSE event streaming
- Chat stream proxy to RAGVLM

#### Config

- Expanded `.env.example` with Whisper, yt-dlp, SAM3, RAG, and timeout settings

### Outcome

OperatorOS could ingest videos, transcribe them, accept annotations, run chat inference, and start tracking jobs. Quality and UX were still rough in places, but the end-to-end path existed.

---

## Phase 3 — Orchestrator streaming hardening

**Commit:** `08a0f10` — *Ensure the orchestrator closes the media source HTTP client when upstream streaming setup fails*  
**Date:** 2026-07-07  
**Size:** +52 / −19 lines

### What we did

Fixed a resource leak in the orchestrator’s media source streaming path. When upstream setup failed, the HTTP client was not always closed.

### Deliverables

- Proper `try/finally` cleanup in `services/orchestrator/app/main.py`
- Regression tests in `tests/test_orchestrator_media.py`

### Outcome

More reliable video playback proxying, especially when ingest or streaming errors occur mid-request.

---

## Phase 4 — RAGVLM annotation tools and retrieval port

**Commit:** `1781f46` — *Port RAGVLM annotation tools and embedded retrieval into OperatorOS*  
**Date:** 2026-07-07 (afternoon)  
**Size:** +1,128 / −310 lines across 15 files

### What we did

Ported the richer annotation and document retrieval experience from the upstream RAGVLM reference implementation into OperatorOS.

### Deliverables

#### Annotation overlay (major rewrite)

- Tools: cursor, select, pen, arrow, rectangle, circle, eraser, text
- Selection, drag, resize handles
- Normalized coordinates for VLM consumption
- `AnnotationControls.tsx` expanded with tool modes

#### Document retrieval (major rewrite)

- Improved chunking and embedding pipeline in `services/ragvlm-service/app/rag/retrieval.py`
- Better index management
- Updated tests in `tests/test_ragvlm_retrieval.py`

#### RAGVLM prompts and annotations

- SketchVLM-oriented prompt structure
- Annotation schema alignment with model families

#### SAM3 backend

- Additional tracking backend improvements (+107 lines)

### Outcome

Annotation tools felt like a real drawing app on top of the video. Document search was production-shaped even if the UI was not fully wired yet.

---

## Phase 5 — Annotation toolbar polish

**Commit:** `3133bea` — *annotation feature polished and perfected*  
**Date:** 2026-07-07  
**Size:** +85 / −13 lines (single file)

### What we did

Focused polish pass on `AnnotationControls.tsx` only.

### Deliverables

- Icon-based toolbar with active state styling
- Undo and clear actions
- Color and stroke width controls
- Cursor mode passes clicks through to video controls (play/pause/seek works again)

### Outcome

Annotations were usable during a live demo without fighting the video player.

---

## Phase 6 — Chat, document RAG, transcripts, and SketchVLM integration

**Commit:** `39f98d5` — *Integrate RAGVLM chat, document RAG, transcripts, and visual annotations*  
**Date:** 2026-07-07 (evening)  
**Size:** +922 / −105 lines across 12 files

### What we did

The main “product comes together” commit. Connected all backend capabilities to a cohesive frontend experience.

### Deliverables

#### Frontend (`page.tsx` — major rewrite)

- **Chat history** replacing a single answer box
- **Model selector** dropdown (default: `qwen/qwen3-vl-8b-instruct`)
- **Document upload UI** — attach/detach manuals per question
- **Transcript display** with source label (Whisper / fallback / empty)
- **SketchVLM overlay rendering** — model-drawn shapes on the video
- **Optional annotated snapshot** checkbox — sends composited frame as extra image
- Visible streaming and error messages in chat

#### Backend

- Orchestrator chat stream improvements
- RAGVLM infer endpoint enhancements for streaming + document context
- Video service transcript reliability improvements
- `parseResponse.ts` — robust JSON extraction from model output

#### Documentation

- Created `development-records/ragvlm-video-rag-progress.md` — detailed slice progress notes

### Outcome

OperatorOS felt like a real product: load video → pause → draw → attach manual → ask → get answer with overlays. This commit is the baseline for “everything except SAM3 polish and UI redesign works.”

---

## Phase 7 — Real SAM3 tracking and UI redesign

**Commit:** `1ece871` — *Add Ultralytics SAM3 tracking and redesign OperatorOS UI*  
**Date:** 2026-07-10  
**Size:** +1,212 / −464 lines across 12 files  
**GitHub:** latest pushed commit as of this document

### What we did

Replaced the SAM3 simulation-oriented path with a real Ultralytics backend and redesigned the frontend to match a new light-theme mockup.

### Deliverables

#### SAM3 — real backend

- Switched from `facebookresearch/sam3` approach to `ultralytics` SAM3 predictors
- Dependencies: `ultralytics`, `timm`, `huggingface_hub`, `safetensors`
- `tracking_backend.py` rewrite:
  - `SAM3VideoPredictor` / semantic predictor support
  - Clip from paused timestamp
  - Mask → polygon overlays for frontend
  - AV1 fallback via pre-extracted JPEG frames
  - `TORCHDYNAMO_DISABLE` / `compile: false` for CPU Docker stability
  - `SAM3_MAX_POLYGON_POINTS` cap
- `models/README.md` — instructions for placing local `sam3.pt` weights
- `docker-compose.yml` — mount `./models:/app/models:ro`
- `.env.example` — `SAM3_CHECKPOINT_PATH`, polygon limits
- `.gitignore` — ignore `models/*` except README

#### Frontend UI redesign

- `globals.css` — full light-theme design system (~525 lines)
- `page.tsx` — 2-column layout:
  - Left: upload, annotation tools, video player
  - Right sidebar: Vision & Tracking, RAG docs, VLM model, Conversation
- `AnnotationControls.tsx` — icon toolbar, status pill, color/width controls aligned to new design

### Outcome

The app looked modern and SAM3 could run against real local weights. Tracking UX and CPU performance still needed polish, but the integration path was real rather than simulated.

---

## Phase 8 — Video title context and presentation *(local, not yet pushed)*

**Status:** uncommitted as of 2026-07-10  
**Files changed:** 10 modified + `presentation/` folder (untracked)

### What we did

After Phase 7, continued work on contextual grounding and presentation materials for stakeholders.

### Deliverables

#### Video title feature (uncommitted)


| File                                                    | Change                                                                                        |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `services/video-service/app/main.py`                    | `metadata.json` per video; title from filename or YouTube `.info.json`; `GET /media/metadata` |
| `services/orchestrator/app/main.py`                     | Metadata proxy; `video_title` in chat stream request                                          |
| `services/ragvlm-service/app/main.py`                   | `video_title` on infer request                                                                |
| `services/ragvlm-service/app/prompts.py`                | Video title section in VLM prompt                                                             |
| `frontend/src/app/page.tsx`                             | “Now playing” bar; pass title to chat                                                         |
| `frontend/src/lib/api.ts`, `types.ts`                   | Metadata + title types                                                                        |
| `tests/test_video_service.py`, `test_ragvlm_adapter.py` | Updated coverage                                                                              |


#### Presentation materials (uncommitted)


| File                                      | Purpose                                          |
| ----------------------------------------- | ------------------------------------------------ |
| `presentation/index.html`                 | 4-slide deck for stakeholder demos               |
| `presentation/OPERATOROS_PRESENTATION.md` | Beginner-friendly project overview with diagrams |
| `presentation/Machine Introduction.png`   | Hero slide image (lathe workshop)                |


### Outcome

Video titles from YouTube now ground the AI better. Presentation assets exist for onboarding people who have never seen the codebase. **This work still needs a git commit and push.**

---

## What exists today (end state)

### Working

- Docker Compose stack (frontend + 4 backend services + Redis + worker)
- MP4 upload and YouTube URL ingest
- Browser playback with pause/seek
- Whisper transcription (re-ingest older videos if they used fallback)
- Annotation tools (rect, arrow, pen, text, select, eraser, undo, clear)
- Chat with streaming answers via OpenRouter
- SketchVLM — model returns text + drawable overlays
- Document upload and RAG retrieval (PDF, DOCX, MD, TXT)
- Optional annotated snapshot sent as second image to VLM
- Video title in UI and VLM prompt *(local only until committed)*
- SAM3 Ultralytics integration with local `models/sam3.pt`

### In progress / needs validation

- SAM3 tracking reliability and speed in full UI (CPU Docker is slow)
- Document RAG end-to-end verification
- Object tracking UX polish
- Presentation deployment for public sharing

### Not yet built

- Live token streaming into chat bubbles (currently shows final parsed response)
- Markdown rendering in chat
- Document delete / persist selected docs across reloads
- Citation snippets in RAG answers
- Production GPU path for SAM3
- Transcript as background job during ingest

---

## Repository structure (after all phases)

```
operator-os/
├── frontend/                    # Next.js UI
├── services/
│   ├── orchestrator/            # API gateway, SSE, session
│   ├── video-service/           # Ingest, transcript, frames, playback
│   ├── ragvlm-service/          # RAG + VLM / SketchVLM
│   └── sam3-service/            # SAM3 tracking
├── workers/                     # Optional RQ worker
├── data/                        # Runtime video storage (gitignored)
├── models/                      # Local SAM3 weights (gitignored)
├── presentation/                # Slides + overview (uncommitted)
├── development-records/         # Build history and progress notes
│   ├── project-development-history.md   ← this file
│   └── ragvlm-video-rag-progress.md
├── docker-compose.yml
├── build_spec.md                # Product blueprint
└── README.md                    # Developer quick start
```

---

## How to inspect any phase in git

```bash
# List all commits oldest-first
git log --oneline --reverse

# See what changed in a specific phase
git show 39f98d5 --stat

# Compare two phases
git diff 3133bea..39f98d5 --stat

# Check out code at a specific point (read-only exploration)
git checkout 39f98d5
```

---

## Related documents


| Document              | Location                                             | Contents                                  |
| --------------------- | ---------------------------------------------------- | ----------------------------------------- |
| Product blueprint     | `build_spec.md`                                      | Full vision, research goals, feature spec |
| Developer quick start | `README.md`                                          | Environment variables, Docker commands    |
| RAGVLM slice progress | `development-records/ragvlm-video-rag-progress.md`   | Detailed notes from Phase 6               |
| **This file**         | `development-records/project-development-history.md` | Full phased build history                 |
| Stakeholder overview  | `presentation/OPERATOROS_PRESENTATION.md`            | Non-technical diagrams and explanations   |
| Live slides           | `presentation/index.html`                            | 4-slide HTML presentation                 |


---

## Suggested next commit

When ready to push Phase 8:

```
Add video title context and stakeholder presentation materials

Persist video metadata (title, source) during ingest, surface title
in the UI and VLM prompt, and add HTML/Markdown presentation assets
for demos.
```

