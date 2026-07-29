# OperatorOS independent multimodal RAG service

This service ingests original PDFs into its own page-level index. It never reads the
ordinary text-RAG index or the converted-text artifact.

Run it from the repository root:

```powershell
uvicorn app.main:app --app-dir services/multimodal-rag-service --port 8004
```

Endpoints:

- `GET /health`
- `POST /documents/ingest` (`multipart/form-data`: `file`, optional `document_id`)
- `GET /documents/{document_id}/status`
- `POST /rag/multimodal/answer` (`question`, `document_ids`, optional `top_k`,
  `conversation`, `model`, and `allow_model_knowledge`)
- `POST /ask` (compatibility alias for the same answer contract)

Configuration:

- `MULTIMODAL_RAG_DATA_DIR` (default `data/multimodal-rag`)
- `MULTIMODAL_RAG_MAX_UPLOAD_BYTES` (default 100 MiB)
- `MULTIMODAL_RAG_RENDER_DPI` (default 200)
- `MULTIMODAL_RAG_PIPELINE_VERSION` (default `0.1.0`)
- `MULTIMODAL_RAG_RETRIEVER`: `auto`, `openrouter`, or `deterministic`. `auto`
  visually ranks original page images through OpenRouter when a key is present,
  and otherwise uses the local lexical/scanned-page fallback.
- `MULTIMODAL_RAG_RETRIEVER_MODEL` (default `qwen/qwen3-vl-8b-instruct`)
- `MULTIMODAL_RAG_RETRIEVER_BATCH_SIZE` (default 6, maximum 12)
- `MULTIMODAL_RAG_ANSWERER`: `auto`, `offline`, or `openrouter`
- `MULTIMODAL_RAG_OPENROUTER_MODEL` (default `google/gemini-2.5-flash`)
- `MULTIMODAL_RAG_OPENROUTER_TIMEOUT_SECONDS` (default 120)
- `OPENROUTER_API_KEY`, `OPENROUTER_HTTP_REFERER`, `OPENROUTER_APP_TITLE`

In `auto` mode, OpenRouter is used when an API key exists; otherwise a deterministic
offline answerer provides a functional local fallback. PyMuPDF renders source pages
when installed. If it is unavailable or fails, ingestion safely retains page-aware
text extracted by pypdf and reports that visual retrieval is unavailable.
