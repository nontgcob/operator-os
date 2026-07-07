# OperatorOS RAGVLM Video RAG Progress

Date: 2026-07-07

## Previously Working

- Docker Compose app structure with frontend, orchestrator, video service, RAGVLM service, SAM3 service, and Redis.
- Video ingest supported local MP4 upload and YouTube URL ingest.
- Browser playback worked through the orchestrator media proxy.
- Basic paused-frame asking flow existed, but answer display and streaming behavior were rough.
- Basic transcript window endpoint existed, but it was showing fallback timestamp text when Whisper was disabled.
- Annotation overlay existed with user-drawn annotations.
- SAM3 tracking path existed, with simulation or development fallback depending on environment config.
- Backend document ingestion endpoints existed, but they were not connected to frontend UX.

## Built And Working In This Slice

- RAGVLM chat UI:
  - Replaced the single `Answer` area with chat history.
  - Shows user and OperatorOS messages.
  - Shows visible streaming/backend errors instead of leaving the UI blank.
- SketchVLM response handling:
  - Prompt now asks for `{ answer, annotations }`.
  - Frontend parses model JSON.
  - Returned model annotations can render back onto the video overlay.
- Model selection:
  - Added model dropdown.
  - Shows active model.
  - Each assistant response records which model was used.
  - Default changed to `qwen/qwen3-vl-8b-instruct` because Gemini Pro failed by region.
- Transcript:
  - Enabled Whisper in `.env`.
  - Real transcript works after re-ingesting videos.
  - UI labels transcript source as `Whisper`, `fallback`, or `empty`.
  - Timestamps display as `m:ss` instead of raw seconds.
- Document RAG:
  - Added document/manual upload UI.
  - Supports PDF, DOCX, markdown, and text.
  - Shows uploaded file name and chunk count.
  - Lets the user attach or detach docs for RAG.
  - Sends selected `document_ids` with chat requests.
- Annotation UX:
  - Ported richer RAGVLM-style tools: cursor, select, pen, arrow, rect, circle, eraser, and text.
  - Added undo, clear, color, and stroke width controls.
  - Fixed media controls by making cursor mode pass through to video controls.
  - Toolbar uses icons and active styling.
- Optional annotated snapshot:
  - Default remains original frame plus annotation JSON.
  - Added unchecked checkbox: `Also send annotated snapshot`.
  - When checked, sends a second frame image with annotations drawn on top.
  - Chat message records whether the annotated snapshot was included.
- Backend hardening:
  - SSE errors now propagate as visible frontend errors.
  - OpenRouter/RAGVLM stream disconnects no longer only show as server tracebacks.
  - Media source async client leak was already fixed before this slice.

## Waiting To Be Built Or Perfected

- Full end-to-end Docker smoke test after rebuild.
- Better document management:
  - Remove uploaded docs.
  - Persist selected docs across reloads.
  - List previously uploaded docs from backend.
- Better chat polish:
  - Stream tokens live into the assistant bubble instead of only showing the final parsed response.
  - Render markdown nicely.
  - Auto-scroll chat history.
- More robust SketchVLM annotation parsing:
  - Validate model-generated annotation shapes.
  - Handle malformed JSON repair more gracefully.
  - Distinguish user annotations from model annotations visually.
- Annotated snapshot fidelity:
  - Canvas drawing is good enough now, but could be made pixel-perfect with the SVG overlay renderer.
- Model availability:
  - Some OpenRouter models may fail by region or account.
  - Add model health/status or fallback selection.
- Transcript improvements:
  - Run transcription as background jobs instead of blocking ingest.
  - Add transcript search and full transcript panel.
  - Improve speaker and segment cleanup.
- Production-grade RAG:
  - Document deletion and versioning.
  - Citations in answers.
  - Richer source snippets in UI.
  - Configurable retrieval `top_k`.
- Real SAM3 backend:
  - Current setup can use simulation depending on env.
  - Full SAM3 model/checkpoint path still needs production setup.
