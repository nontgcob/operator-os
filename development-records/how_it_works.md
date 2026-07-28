# How OperatorOS Works

## Purpose of This Document

This document explains the core architecture and behavior of OperatorOS as it exists today. It covers:

- The responsibilities of each service.
- How videos, transcripts, documents, chat, annotations, and SAM3 tracking flow through the system.
- Why the system was designed this way.
- Approaches that were attempted and later replaced.
- Problems discovered during RTX 4090 validation.
- The practical lessons that should guide future development.
- Current operational requirements and known limitations.

The emphasis is on how the product works, not merely on a chronological list of code changes.

## Product Intent

OperatorOS is a video-first multimodal assistant for industrial training and equipment understanding. The primary interaction is not a standalone chatbot. A user:

1. Loads a local or online video.
2. Pauses on a relevant frame.
3. Draws optional annotations or asks a natural-language question.
4. Receives an answer grounded in the visible frame, nearby transcript, conversation history, and selected manuals.
5. Can ask OperatorOS to track an object with SAM3.
6. Receives a newly generated video clip with the SAM3 segmentation highlight baked into each processed frame.

The core product decision is that visual context is primary. Transcript and document retrieval add supporting context, but they should not override what is visibly present in the current frame.

## High-Level Architecture

OperatorOS is split into five locally runnable services plus an optional worker:

| Component | Default port | Responsibility |
| --- | ---: | --- |
| Next.js frontend | 3000 | Video player, annotations, chat, document controls, tracking controls, processed-video playback |
| Orchestrator | 8000 | Public API gateway, service coordination, conversation state, streaming proxies |
| RAGVLM service | 8001 | Prompt construction, OpenRouter multimodal inference, document indexing and retrieval |
| Video service | 8002 | Upload/YouTube ingest, media validation, frame extraction, transcription, byte-range streaming |
| SAM3 service | 8003 | Real or simulated segmentation/tracking, progress events, rendered tracking videos |
| RQ worker | optional | Redis-backed tracking delegation when worker mode is explicitly enabled |

The frontend communicates primarily with the orchestrator. The orchestrator then calls the other services. This keeps service addresses, provider details, and backend error handling out of the browser.

The default development mode is local and does not require Docker, Redis, or RQ. Docker Compose remains available as an optional deployment or reproducibility path.

## Local Runtime

### Root Commands

The root `package.json` exposes the main workflow:

```text
npm run setup
npm run setup:sam3
npm run setup:sam3:cuda
npm run diagnose:sam3
npm run dev
npm test
```

`scripts/python-env.mjs` is the cross-platform launcher behind these commands.

It solves several practical problems:

- Windows virtual environments use `.venv/Scripts/python.exe`.
- Unix-like environments use `.venv/bin/python`.
- Python services require the repository root and their service directory on `PYTHONPATH`.
- Windows PowerShell does not provide the Unix `env KEY=value command` syntax used by the original scripts.
- VS Code terminals can retain stale environment variables after `.env` changes.

For service processes, the launcher reads the repository `.env` and deliberately applies those values over inherited terminal values. This became necessary after an old terminal value of:

```text
SAM3_MAX_PROPAGATION_FRAMES=20
```

continued to override the updated `.env` value of `0`, causing generated tracking videos to contain only 20 frames.

### Python and CUDA

The validated Windows GPU environment uses:

- Python 3.12.
- PyTorch with CUDA 12.6.
- An NVIDIA RTX 4090.
- Ultralytics SAM3.
- A local `models/sam3.pt` checkpoint.

The original Python 3.9 environment could not import the installed PyTorch build because `typing.TypeGuard` was unavailable. Rebuilding the virtual environment with Python 3.12 resolved that compatibility issue.

Installing Python packages from normal PyPI initially selected a CPU-only PyTorch wheel. The machine had an RTX 4090 and a working NVIDIA driver, but `torch.cuda.is_available()` still returned `False`. `npm run setup:sam3:cuda` explicitly installs the CUDA 12.6 PyTorch and torchvision wheels.

`npm run diagnose:sam3` reports:

- PyTorch version.
- CUDA build version.
- CUDA availability.
- Detected GPU.
- Ultralytics version.
- Checkpoint path and existence.

### Environment Loading

Python services load `.env` from the repository root through `services/common/env.py`.

The checked-in VS Code settings also configure:

```json
{
  "python.envFile": "${workspaceFolder}/.env",
  "python.terminal.useEnvFile": true,
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe"
}
```

The launcher remains the authoritative safeguard because editor terminals can retain values loaded before `.env` was changed.

## Frontend

The frontend is a Next.js application centered around `frontend/src/app/page.tsx`.

It owns the user-facing state for:

- The current video URL and source video ID.
- Video title and current playback time.
- The source-time offset of a processed tracking clip.
- Current transcript window.
- User annotations.
- Model-generated annotations.
- Tracking status and active job ID.
- Uploaded and selected documents.
- Chat history shown in the interface.
- VLM model selection.

### Media Input

Users can:

- Upload a local MP4 file.
- Provide a YouTube URL.

For local uploads, the frontend initially creates a browser object URL so the player can begin loading the selected file. It then sends the file to the orchestrator for persistent ingest.

For YouTube, the frontend sends the URL to the orchestrator and uses the returned video ID to construct the media-streaming URL.

### Annotation System

The annotation overlay is an SVG layer over the video. It supports:

- Freehand paths.
- Arrows.
- Rectangles.
- Circles.
- Text.
- Selection.
- Erasing.
- Undo.

User and model annotations use normalized RAGVLM coordinates from `0` to `1000`.

SAM3’s former browser polygon representation used percentages from `0` to `100`. Confusing those coordinate spaces caused masks to be compressed into the upper-left tenth of the player. The coordinate conversion was corrected, but the primary tracking output is now a rendered video rather than browser SVG polygons.

The optional annotated snapshot feature rasterizes the video frame and user annotations into a second image. The original frame remains the source image; the annotated copy is sent only as extra visual guidance.

### Chat Submission

When the user submits a question, the frontend:

1. Validates that a video and question exist.
2. Records the displayed user and placeholder assistant messages.
3. Clears stale tracking state and closes the previous EventSource connection.
4. Captures the current video frame as a data URL.
5. Optionally captures the annotated frame.
6. Loads the transcript window around the source timestamp.
7. Sends the question, frame, annotations, transcript, selected documents, model, and session ID to the orchestrator.
8. Reads the streamed response.
9. Parses SketchVLM JSON into:
   - `answer`
   - `annotations`
   - `tracking_prompt`
   - `tracking_annotations`
10. Displays the answer and model annotations.
11. Decides whether to start SAM3.

### Processed-Video Time Offsets

A SAM3 output video starts at the frame where the user requested tracking, not at the beginning of the source video. The frontend therefore stores `videoTimeOffset`.

If the original video was paused at 12.5 seconds and the processed clip is at 3 seconds, later questions are associated with:

```text
source timestamp = 12.5 + 3.0 = 15.5 seconds
```

This preserves correct transcript lookup and future source-video tracking requests even after the player switches to a processed clip.

## Orchestrator

The orchestrator is the frontend-facing API gateway.

It is responsible for:

- Proxying video ingest.
- Proxying seekable video playback.
- Fetching transcript windows and video metadata.
- Proxying document ingest and retrieval.
- Combining retrieved context with chat requests.
- Maintaining rolling conversation state.
- Starting tracking jobs.
- Proxying tracking progress events.
- Proxying generated tracking videos with HTTP range support.

### State

Local development uses an in-memory TTL store by default.

Redis is used only when:

```text
USE_REDIS_STATE=true
```

This was a deliberate simplification. Earlier code imported and contacted Redis at module load time, which made local development and tests fail before the relevant behavior could run.

Conversation history is kept as a rolling window of up to 12 messages. It is included in later RAGVLM requests so follow-up questions have conversational context.

### Streaming

The orchestrator uses Server-Sent Events for chat and tracking status.

For chat, it:

1. Retrieves relevant document chunks.
2. sends the enriched request to the RAGVLM service.
3. Proxies deltas to the browser.
4. Collects the complete answer.
5. Adds the user question and answer to conversation state.

For local tracking, it proxies the SAM3 service’s event stream. In worker mode, it can instead poll Redis-backed state.

### Seekable Video Proxies

Both original media and generated tracking videos are proxied through the orchestrator.

The proxy forwards the browser’s `Range` header and returns:

- `Accept-Ranges`
- `Content-Length`
- `Content-Range`
- The upstream status code, including `206 Partial Content`

This is required for normal video seeking and browser media controls.

## Video Service

The video service stores each ingested video under:

```text
data/video/<video_id>/source.mp4
```

It also creates metadata, transcripts, extracted frames, and a video index.

### Local Upload Ingest

For local files, the service:

1. Creates a video ID and directory.
2. Writes the uploaded content to `source.mp4`.
3. Validates the media.
4. Extracts transcript data.
5. Extracts indexed frames.
6. Stores metadata and returns the video ID and title.

### YouTube Ingest

YouTube ingest uses `yt-dlp`, FFmpeg, and ffprobe.

The system includes diagnostics and configuration for:

- JavaScript/EJS challenge solvers.
- Deno.
- Remote yt-dlp components.
- Player client selection.
- PO token provider plugins.
- Cookies.
- User agent selection.
- IPv4 forcing.
- Request impersonation.
- Retry and timeout values.

There is intentionally no promise of a universal cookie-free YouTube solution. YouTube may require authentication, reject an IP, apply rate limits, or change its challenge system. The service tries to return actionable error guidance instead of hiding the yt-dlp failure.

### Transcription

Whisper transcription is controlled by:

```text
WHISPER_ENABLED
WHISPER_MODEL
```

The model is loaded lazily and cached in process memory.

If Whisper is disabled or cannot produce usable transcript data, the service creates deterministic timestamped fallback segments. These preserve the temporal API shape but are not real speech transcription. The transcript endpoint clearly identifies fallback data and advises re-ingesting with Whisper enabled.

### Frame Extraction

FFmpeg extracts frames for indexing and fallback use. These frames support:

- Video indexing.
- Debugging.
- SAM3 fallback clip creation when OpenCV cannot decode the source directly.

### Media Streaming

`GET /media/source` supports byte ranges so the frontend can seek without downloading the entire file first.

## RAGVLM Service

The RAGVLM service combines:

- The visible video frame.
- Optional annotated frame.
- User annotations.
- Nearby transcript segments.
- Retrieved document chunks.
- Video title.
- Recent conversation messages.
- The selected model.

It then streams a multimodal request through OpenRouter.

### SketchVLM Response Contract

The system prompt requires exactly four logical fields:

```json
{
  "answer": "string",
  "annotations": [],
  "tracking_prompt": "string",
  "tracking_annotations": []
}
```

Coordinates are normalized to the `0–1000` RAGVLM space.

`annotations` visually support the answer.

`tracking_prompt` is a concise description suitable for SAM3.

`tracking_annotations` identify the specific object to follow when a reliable box or shape can be returned.

An explicit request to track, follow, trace, or monitor an object must produce a non-empty tracking prompt even if the VLM cannot create a box. This is important because SAM3 can run from text alone.

### Model Families

The service maps selected models to model families and configures requests appropriately, including whether a model supports reasoning-related options.

### Response Parsing

The frontend parser is defensive. It supports:

- Pure JSON.
- JSON inside Markdown fences.
- JSON embedded in surrounding text.
- Plain-text fallback.

Invalid annotations are discarded rather than rendered.

## Document RAG

### Ingest

The document pipeline:

1. Accepts document bytes and metadata.
2. Extracts text according to file type.
3. Normalizes the text.
4. Splits it into overlapping character-window chunks.
5. Creates embeddings.
6. Stores document metadata, chunks, and vectors in a local JSON index.

Document files and the index live under `RAGVLM_DOCUMENT_DIR`.

### Embeddings

When an OpenRouter API key is available, embeddings are requested from:

```text
https://openrouter.ai/api/v1/embeddings
```

The default model is:

```text
openai/text-embedding-3-small
```

For local tests or development without an API key, the system uses a deterministic local hash embedding. This preserves deterministic retrieval behavior but is not intended to match production semantic quality.

### Retrieval

At question time:

1. The orchestrator asks the RAGVLM service for relevant chunks from the selected document IDs.
2. The query is embedded.
3. Candidate chunks are ranked by cosine similarity.
4. The best chunks are included in the inference prompt with filename and chunk metadata.

The system instructs the model to cite filenames when document evidence is used and not to invent machine-specific instructions that are absent from retrieved material.

### Current RAG Limitation

The current pipeline is primarily text-based. Diagram-aware retrieval, page-image retrieval, OCR/layout reasoning, arrows, and multimodal manual pages remain future improvements.

## SAM3 Tracking

### Tracking Intent

Tracking may start when:

- The user explicitly says `track`, `tracking`, `follow`, `trace`, or `monitor`.
- The user uses phrases such as `keep an eye on`.
- Automatic SAM3 tracking is enabled.
- The VLM returns a tracking prompt or tracking annotation because it considers tracking useful.

Explicit user intent bypasses the automatic-tracking checkbox.

The frontend prefers:

1. VLM `tracking_annotations`, when available.
2. General VLM annotations only in automatic mode.
3. Text-prompted SAM3 using `tracking_prompt`.
4. The original user question as the final text prompt.

This means a VLM bounding box is helpful but not required.

### Predictor Selection

The SAM3 backend uses Ultralytics:

- `SAM3VideoPredictor` for box-prompted tracking.
- `SAM3VideoSemanticPredictor` for text-prompted tracking.

Predictors are loaded lazily and cached by type. Model construction is protected by an async lock so concurrent jobs do not race during initialization.

The validated predictor configuration includes:

- `task="segment"`
- `mode="predict"`
- `conf=0.50`
- `show_conf=True`
- `compile=False`
- FP16 when CUDA is available and the selected device is not CPU
- `save=False`
- Configurable `imgsz`, currently defaulting to 1024

Ultralytics adjusts 1024 to the nearest valid model stride internally when needed.

### CLIP Compatibility

Text-prompted SAM3 initially failed with:

```text
'SimpleTokenizer' object is not callable
```

The cause was the OpenAI CLIP package. The installed Ultralytics SAM3 version expects the Ultralytics CLIP fork, whose tokenizer has the callable interface used by SAM3’s text encoder.

The SAM3 requirements therefore install:

```text
git+https://github.com/ultralytics/CLIP.git
```

This is a critical dependency choice. Replacing it with the OpenAI CLIP repository breaks text-prompted tracking.

### Tracking Job Flow

When tracking starts:

1. The orchestrator creates a UUID tracking job ID.
2. The request includes the source video ID, source timestamp, frame, question, prompt, and optional annotations.
3. In local mode, the orchestrator posts directly to the SAM3 service.
4. The SAM3 service stores an initial in-memory job state.
5. A background task runs the selected backend.
6. The frontend subscribes to `/tracking/events/<job_id>`.
7. The original video is paused while processing occurs.
8. Progress events are streamed without large mask payloads.
9. The final event includes the rendered video path.
10. The frontend replaces the player source with `/tracking/video/<job_id>`.
11. The user plays the completed SAM3-rendered clip.

### Clip Creation

SAM3 begins at the exact source timestamp requested by the user.

OpenCV:

1. Opens `data/video/<video_id>/source.mp4`.
2. Reads the source FPS, width, and height.
3. Seeks to `round(timestamp * fps)`.
4. Writes consecutive frames at the source FPS.
5. Continues to the end when `SAM3_MAX_PROPAGATION_FRAMES=0`.
6. Stops at the configured positive frame limit when one is provided.

If OpenCV cannot decode the video, SAM3 can create a clip from the video service’s extracted JPEG frames. That fallback has lower temporal fidelity and uses the configured fallback interval.

### Why Native FPS Matters

An early implementation sampled one frame per second. The frontend only displayed a mask close to each timestamp, so the segmentation appeared briefly and then disappeared until the next second. This produced visible blinking.

Native-FPS processing fixed the temporal gap. A 59.972 FPS source now produces results approximately every 0.0167 seconds.

### Mask Rendering

The current authoritative output is a processed video with the SAM3 mask baked into its pixels.

For every result:

1. `result.orig_img` provides the original BGR frame.
2. `result.masks.data` provides binary mask tensors.
3. Each mask is thresholded at `0.5`.
4. The mask is resized to the frame resolution with nearest-neighbor interpolation when necessary.
5. A stable green highlight is alpha-blended into masked pixels.
6. The processed frame is written to the output video.

This mirrors the known-good standalone SAM3 script used during debugging.

### Browser-Compatible Encoding

OpenCV writes an intermediate `mp4v` file because that codec is broadly available through `VideoWriter`.

Chromium rejected that result with:

```text
FFmpegDemuxer: unsupported streams
```

ffprobe confirmed the intermediate codec was MPEG-4 Part 2. The final pipeline therefore transcodes with FFmpeg:

```text
-c:v libx264
-pix_fmt yuv420p
-movflags +faststart
```

The result is:

- H.264.
- Browser-compatible YUV420p.
- Fast-start enabled.
- Seekable through HTTP range requests.

The intermediate file is deleted only after a valid final output is produced.

### Generated Clip Semantics

The generated video begins at the user’s paused timestamp and continues to the end of the source video. It does not currently include the unchanged portion before that timestamp.

The frontend keeps the original source video ID and stores a time offset so later questions still map to the correct source time.

### Audio

The current generated tracking video is video-only. FFmpeg is invoked with `-an`, so source audio is not preserved in the processed clip.

Preserving audio would require clipping or remuxing the corresponding source audio segment and synchronizing it with the rendered frames.

### SAM3 State and Events

The SAM3 service owns local tracking state.

Endpoints:

```text
POST /tracking/start
GET /tracking/status/<job_id>
GET /tracking/events/<job_id>
GET /tracking/video/<job_id>
GET /health
```

Local state uses an in-memory TTL store. Redis is optional.

Progress events include:

- Tracking job ID.
- Completion state.
- Percentage.
- Backend.
- Error information.
- Final rendered video path.

The event polling window was extended because full-video processing at 1024 resolution can take significantly longer than the original one-minute timeout.

### SAM3 Health

`GET /health` reports:

- Backend name.
- Backend readiness.
- Backend error code and message.
- Resolved checkpoint path.
- Checkpoint existence.
- Selected device.
- PyTorch version.
- CUDA build version.
- CUDA availability.
- GPU name.
- Effective maximum propagation frames.
- Effective image size.
- Simulation status.

The effective configuration fields are important. They expose cases where the process environment differs from the `.env` file.

### Simulation Mode

Simulation is never intended to look like real SAM3.

It must be explicitly enabled:

```text
SAM3_TRACKING_BACKEND=simulation
SAM3_ALLOW_SIMULATION_FALLBACK=true
```

Otherwise a simulation backend is reported as unavailable. Simulated overlays have explicit simulation labels.

For real demonstrations:

```text
SAM3_TRACKING_BACKEND=sam3
SAM3_ALLOW_SIMULATION_FALLBACK=false
```

### Worker Mode

The default local path calls SAM3 directly.

When:

```text
USE_WORKER_QUEUE=true
```

the orchestrator can enqueue jobs through RQ and Redis. This path exists for background-processing deployment scenarios but is not the primary local development workflow.

## Approaches Tried, Findings, and Replacements

### 1. Redis as a Mandatory Local Dependency

**Tried:** Import Redis and use it for all conversation and tracking state.

**Problem:** Local imports and tests failed when Redis or its Python package was unavailable.

**Finding:** Most local development does not need distributed state.

**Decision:** Use in-memory TTL state by default and initialize Redis only when explicitly enabled.

### 2. Starting Tracking Before the VLM Answer

**Tried:** Start SAM3 immediately using the user’s general question and annotations.

**Problem:** The VLM had not yet identified the intended object, so SAM3 could not use a model-generated tracking target.

**Finding:** Tracking intent and target selection should be separated.

**Decision:** Parse the VLM response first, then use its tracking prompt or annotation. Explicit user tracking requests still work without a VLM box.

### 3. Requiring a VLM Bounding Box

**Tried:** Abort tracking unless the VLM returned tracking annotations.

**Problem:** Users explicitly asking to track an object received “No trackable target,” even though semantic SAM3 can track from text.

**Finding:** Bounding boxes improve specificity but are not a prerequisite.

**Decision:** Use box prompts when available and text prompts otherwise.

### 4. OpenAI CLIP with Ultralytics SAM3

**Tried:** Install `openai/CLIP`.

**Problem:** Text tracking failed because `SimpleTokenizer` was not callable.

**Finding:** Ultralytics SAM3 depends on behavior from the Ultralytics CLIP fork.

**Decision:** Pin the Git dependency to `ultralytics/CLIP`.

### 5. One-FPS Tracking

**Tried:** Extract one frame per second and replay browser overlays around each timestamp.

**Problem:** Masks blinked on and off and motion was not tracked smoothly.

**Finding:** Tracking output must have the same temporal grain as playback.

**Decision:** Process consecutive frames at the source FPS.

### 6. Browser Polygon Overlay as the Primary Output

**Tried:** Convert SAM3 masks into polygons, stream all coordinates, and render SVG overlays over the original video.

**Problems discovered:**

- Percent coordinates were incorrectly treated as RAGVLM coordinates and compressed into the upper-left corner.
- Disconnected mask regions were joined into one SVG polygon, creating diagonal crisscross lines.
- Downsampled contours reduced edge quality.
- Playback could outrun asynchronous inference.
- Partial state updates could replace or omit masks.
- Large full-video coordinate timelines were expensive to serialize and stream.

**Finding:** Browser-side polygon synchronization introduced complexity not present in the known-good standalone renderer.

**Decision:** Use the standalone-style raster mask pipeline and generate a processed video. Polygon utilities remain useful for tests, diagnostics, or future interactive overlays, but they are no longer the primary delivery path.

### 7. OpenCV `mp4v` as the Final Browser Video

**Tried:** Serve OpenCV’s MP4 output directly.

**Problem:** Chromium reported an unsupported stream.

**Finding:** A `.mp4` extension does not guarantee a browser-compatible codec.

**Decision:** Transcode the intermediate file to H.264/YUV420p and enable fast-start.

### 8. Fixed 20-Frame and 300-Frame Limits

**Tried:** Limit tracking to 20 frames, then 300 frames.

**Problems:**

- 20 frames at 60 FPS produced only 0.333 seconds.
- 300 frames at 60 FPS produced only about five seconds.
- A stale terminal environment kept the old value even after `.env` changed.

**Finding:** Frame limits must be interpreted in relation to source FPS, and effective process configuration must be observable.

**Decision:** `SAM3_MAX_PROPAGATION_FRAMES=0` means process the entire remaining video. The launcher overrides stale inherited values, and health reports the effective limit.

## Reliability and Stale-State Protection

The frontend:

- Tracks the active tracking job ID.
- Closes the previous EventSource when a new question or video begins.
- Clears old overlays and tracking errors.
- Ignores events from non-active job IDs.
- Adds a cache-busting query to generated video URLs.

The SAM3 service:

- Uses isolated job IDs.
- Uses job-specific temporary clips and output names.
- Stores state per job.
- Rejects invalid tracking video IDs.

These protections were added because stale overlays and old job events could otherwise appear in later sessions.

## Error Visibility

Several early failures were hidden behind generic messages such as:

```text
Tracking failed to start.
```

The frontend now includes the real API error message when a start request fails.

Important errors are designed to distinguish:

- Missing checkpoint.
- Missing Ultralytics dependency.
- CUDA unavailable.
- Model load failure.
- Missing source video.
- Tracking runtime failure.
- Rendered video encoding failure.
- Missing generated video.
- Event stream failure.

## Testing and Validation

The Python suite covers:

- Orchestrator media and document proxying.
- Conversation memory.
- RAGVLM parsing, prompts, and retrieval.
- Video-service behavior.
- SAM3 service behavior and overlay conversion.
- Disconnected mask contours.
- Worker delegation.

The validated suite currently contains 43 tests.

Frontend validation uses the Next.js production build and TypeScript checks.

Real GPU validation included:

- Loading `models/sam3.pt`.
- Constructing both Ultralytics predictor types.
- Box-prompted tracking.
- Text-prompted tracking.
- CUDA execution on the RTX 4090.
- Native-FPS processing.
- Binary mask extraction.
- Rendered MP4 generation.
- H.264/YUV420p transcoding.
- Frame count and FPS checks with OpenCV and ffprobe.
- HTTP 200 full-file responses.
- HTTP 206 byte-range responses.

## Current Core Configuration

Recommended local values:

```text
NEXT_PUBLIC_ORCHESTRATOR_URL=http://localhost:8000
RAGVLM_SERVICE_URL=http://localhost:8001
VIDEO_SERVICE_URL=http://localhost:8002
SAM3_SERVICE_URL=http://localhost:8003

USE_REDIS_STATE=false
USE_WORKER_QUEUE=false

RAGVLM_DOCUMENT_DIR=./data/ragvlm/documents
VIDEO_DATA_DIR=./data/video

SAM3_TRACKING_BACKEND=sam3
SAM3_VIDEO_ROOT=./data/video
SAM3_RENDERED_VIDEO_ROOT=./data/tracking
SAM3_CHECKPOINT_PATH=./models/sam3.pt
SAM3_ALLOW_SIMULATION_FALLBACK=false
SAM3_MAX_PROPAGATION_FRAMES=0
SAM3_IMAGE_SIZE=1024
SAM3_MAX_POLYGON_POINTS=512
```

`SAM3_MAX_POLYGON_POINTS` applies to polygon conversion utilities. The primary processed-video path renders from binary masks.

## Known Limitations and Future Work

### Tracking Throughput

Processing a full high-FPS video at 1024 inference resolution is computationally expensive, even on an RTX 4090. The current UX waits for the complete processed clip before playback.

Potential future improvements:

- Show estimated remaining time.
- Provide selectable quality/speed presets.
- Use configurable tracking duration rather than always processing to the end.
- Use NVIDIA hardware encoding when available.
- Persist and resume jobs across service restarts.

### Audio

Processed tracking clips currently omit audio.

### Output Cleanup

Generated clips are stored under `data/tracking`. Automatic expiry and file cleanup are not yet implemented.

### Multiple Objects and Colors

The rendered output currently uses one green highlight color. The standalone reference included deterministic per-instance colors. That could be restored if multi-instance distinction becomes important.

### Combining Original and Processed Video

The current player switches to a clip beginning at the paused timestamp. It does not concatenate the unchanged prefix of the source video with the processed suffix.

Possible future behavior:

- Keep the current clip-based UX.
- Add a “return to original” control.
- Generate a full-length video by concatenating the original prefix with the processed suffix.
- Preserve original audio while concatenating.

### Interactive Mask Editing

Because masks are baked into the processed video, users cannot currently toggle individual instances or edit masks after rendering. A future hybrid approach could store compressed masks alongside the rendered video.

### Document Intelligence

The RAG pipeline needs stronger support for diagrams, page layout, OCR, and visual manual content.

### Production State

In-memory state is intentionally local-development oriented. Multi-process or horizontally scaled deployment should use Redis or another shared store.

## Practical Debugging Checklist

### SAM3 Health

Check:

```text
GET http://localhost:8003/health
```

Expected:

```text
status = ok
backend = sam3
backend_ready = true
checkpoint_exists = true
cuda_available = true
simulation_enabled = false
max_propagation_frames = 0
image_size = 1024
```

### If Tracking Stops Almost Immediately

Use ffprobe on the generated file and inspect:

- `nb_frames`
- `duration`
- `r_frame_rate`

If it contains exactly 20 frames, the process inherited an old frame limit. Restart all services through the current `npm run dev` launcher and confirm `/health`.

### If Text Tracking Fails

Confirm that the installed CLIP package came from `ultralytics/CLIP`, not `openai/CLIP`.

### If the Browser Rejects the Generated Video

Use ffprobe. The final codec must be:

```text
codec_name=h264
pix_fmt=yuv420p
```

### If the Generated Video Is Missing

Check:

- The tracking status payload.
- `data/tracking/<job_id>.mp4`.
- FFmpeg availability.
- SAM3 service logs.
- `/tracking/video/<job_id>`.

### If YouTube Ingest Fails

Check:

```text
GET http://localhost:8002/diagnostics/ytdlp
```

Then distinguish:

- Authentication/cookies.
- Rate limiting or IP reputation.
- Missing JS runtime.
- Stale challenge solver.
- Unsupported impersonation.
- Missing PO token provider.

## Guiding Principles Learned

1. Verify the complete user-visible path, not only the model call.
2. A successful inference does not prove correct coordinates, timing, streaming, codecs, or rendering.
3. Use known-good standalone behavior as the reference when integrating unstable model APIs.
4. Keep coordinate spaces explicit in types and names.
5. Process tracking at the same temporal resolution as playback.
6. Prefer explicit failure over silent simulation.
7. Report effective runtime configuration, not only configuration files.
8. Browser media compatibility depends on codec, pixel format, metadata, and range support—not file extension alone.
9. Keep local development independent of production infrastructure.
10. Treat generated media as a first-class artifact with validation, storage, serving, and cleanup requirements.
