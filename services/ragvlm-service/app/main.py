from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

try:
    from services.common.env import load_env_file
except ImportError:
    load_env_file = None

if load_env_file:
    load_env_file()

try:
    from .annotations import normalize_annotations, normalize_generated_annotations
    from .model_families import DEFAULT_MODEL, model_family_for, model_supports_reasoning
    from .parse_response import DONE_SENTINEL, parse_openrouter_sse_line
    from .prompts import (
        build_prompt,
        build_training_annotation_prompt,
        build_training_frame_selection_prompt,
    )
    from .rag.retrieval import (
        get_document_file_content_parts,
        get_document_catalog,
        get_document_file_path,
        get_document_status,
        ingest_document_bytes,
        reprocess_document,
        sync_preloaded_documents,
    )
except ImportError:
    from annotations import normalize_annotations, normalize_generated_annotations
    from model_families import DEFAULT_MODEL, model_family_for, model_supports_reasoning
    from parse_response import DONE_SENTINEL, parse_openrouter_sse_line
    from prompts import (
        build_prompt,
        build_training_annotation_prompt,
        build_training_frame_selection_prompt,
    )
    from rag.retrieval import (
        get_document_file_content_parts,
        get_document_catalog,
        get_document_file_path,
        get_document_status,
        ingest_document_bytes,
        reprocess_document,
        sync_preloaded_documents,
    )

app = FastAPI(title="OperatorOS RAGVLM Service", version="0.1.0")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:3000")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "OperatorOS")
OPENROUTER_PDF_ENGINE = os.getenv("OPENROUTER_PDF_ENGINE", "native")
TRAINING_VISUAL_CONCURRENCY = max(1, int(os.getenv("TRAINING_VISUAL_CONCURRENCY", "3")))


def _sse(payload: str, event: str | None = None) -> str:
    lines = payload.split("\n")
    prefix = f"event: {event}\n" if event else ""
    return prefix + "".join(f"data: {line}\n" for line in lines) + "\n"


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class InferRequest(BaseModel):
    question: str
    video_title: str | None = None
    frame_data_url: str
    annotated_frame_data_url: str | None = None
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    retrieved_chunks: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    conversation: list[dict[str, str]] = Field(default_factory=list)
    model: str = DEFAULT_MODEL
    additional_notes: str = ""
    mode: str = Field(default="qna", pattern=r"^(qna|training)$")
    video_evidence: list[dict[str, Any]] = Field(default_factory=list)
    video_overview: dict[str, Any] = Field(default_factory=dict)


class TrainingAnnotationRequest(BaseModel):
    frame_data_url: str
    step_title: str
    instruction: str
    expected_result: str = ""
    components: list[str] = Field(default_factory=list)
    timestamp: float = Field(ge=0)
    previous_annotations: list[dict[str, Any]] = Field(default_factory=list)
    model: str = DEFAULT_MODEL


def _build_prompt(payload: InferRequest) -> str:
    transcript = "\n".join(
        f"[{segment.start:.2f}-{segment.end:.2f}] {segment.text}"
        for segment in payload.transcript_segments
    ) or "No transcript."
    try:
        catalog = get_document_catalog(payload.document_ids)
    except KeyError:
        catalog = []
    docs = (
        "The selected PDF manual(s) are attached as native PDF inputs. Use the exact document IDs and filenames "
        "below in every document citation. Cite the most precise page and section supported by the PDF parser.\n"
        + json.dumps(catalog, ensure_ascii=False)
        if catalog
        else "No document PDFs are attached."
    )
    return build_prompt(
        payload.question,
        normalize_annotations(payload.annotations),
        transcript,
        docs,
        model_family=model_family_for(payload.model),
        video_title=payload.video_title,
        additional_notes=payload.additional_notes,
        mode=payload.mode,
        video_evidence=payload.video_evidence,
        video_overview=payload.video_overview,
    )


def _pdf_plugins() -> list[dict[str, Any]] | None:
    if not OPENROUTER_PDF_ENGINE:
        return None
    return [
        {
            "id": "file-parser",
            "pdf": {
                "engine": OPENROUTER_PDF_ENGINE,
            },
        }
    ]


def _openrouter_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_HTTP_REFERER,
        "X-Title": OPENROUTER_APP_TITLE,
    }


def _json_object_from_content(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    if not isinstance(content, str):
        raise ValueError("OpenRouter returned no JSON content")
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("OpenRouter returned invalid JSON") from None
        parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("OpenRouter JSON response was not an object")
    return parsed


async def _openrouter_json_request(
    client: httpx.AsyncClient,
    *,
    model: str,
    messages: list[dict[str, Any]],
    reasoning_effort: str = "low",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    if model_supports_reasoning(model):
        body["reasoning"] = {"effort": reasoning_effort}
    response = await client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=_openrouter_headers(),
        json=body,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenRouter returned HTTP {response.status_code}: {response.text}")
    payload = response.json()
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenRouter returned no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    return _json_object_from_content(message.get("content") if isinstance(message, dict) else None)


def _training_candidates(video_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, evidence in enumerate(video_evidence):
        frame_data_url = evidence.get("frame_data_url")
        if not isinstance(frame_data_url, str) or not frame_data_url.startswith("data:image/"):
            continue
        timestamp = evidence.get("representative_timestamp", evidence.get("start"))
        if not isinstance(timestamp, (int, float)):
            continue
        candidate_id = str(evidence.get("segment_id") or f"candidate-{index + 1}")
        public = {
            "candidate_id": candidate_id,
            "timestamp": float(timestamp),
            "end_timestamp": evidence.get("end"),
            "summary": evidence.get("summary", ""),
            "objects": evidence.get("objects", []),
            "actions": evidence.get("actions", []),
            "visible_text": evidence.get("visible_text", []),
            "procedure_phase": evidence.get("procedure_phase", ""),
            "transcript": evidence.get("transcript", evidence.get("transcript_text", "")),
            "confidence": evidence.get("confidence", ""),
        }
        candidates.append({"candidate_id": candidate_id, "frame_data_url": frame_data_url, "public": public})
    return candidates


def _training_step_id(step: dict[str, Any], index: int) -> str:
    value = step.get("id")
    return str(value).strip() if isinstance(value, str) and value.strip() else f"step-{index + 1}"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/documents/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    document_id: str | None = None,
) -> dict[str, Any]:
    data = await file.read()
    try:
        return ingest_document_bytes(
            data,
            filename=file.filename or "document.pdf",
            content_type=file.content_type,
            document_id=document_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/documents/preloaded")
async def preloaded_documents() -> dict[str, Any]:
    try:
        return {"documents": sync_preloaded_documents()}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Unable to load preloaded manuals: {exc}") from exc


@app.get("/documents/{document_id}/status")
async def document_status(document_id: str) -> dict[str, Any]:
    try:
        return get_document_status(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc


@app.get("/documents/{document_id}/file")
async def document_file(document_id: str) -> FileResponse:
    try:
        path, document = get_document_file_path(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    return FileResponse(
        path,
        media_type=str(document.get("content_type") or "application/pdf"),
        filename=str(document.get("name") or path.name),
        content_disposition_type="inline",
    )


@app.post("/documents/{document_id}/reprocess")
async def reprocess_uploaded_document(document_id: str) -> dict[str, Any]:
    try:
        return reprocess_document(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc


@app.get("/documents/{document_id}/converted-text")
async def converted_text(document_id: str) -> None:
    raise HTTPException(status_code=410, detail="Converted text artifacts were removed; PDFs are sent directly.")


@app.get("/documents/{document_id}/converted-text/download")
async def download_converted_text(document_id: str) -> None:
    raise HTTPException(status_code=410, detail="Converted text artifacts were removed; PDFs are sent directly.")


@app.post("/documents/retrieve")
async def retrieve_document_chunks() -> None:
    raise HTTPException(status_code=410, detail="Document retrieval was removed; PDFs are sent directly to the VLM.")


@app.post("/rag/text/answer")
async def text_rag_answer() -> None:
    raise HTTPException(status_code=410, detail="Text RAG was removed; PDFs are sent directly to the VLM.")


@app.post("/ragvlm/infer")
async def infer(payload: InferRequest) -> StreamingResponse:
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY missing")

    try:
        document_parts = get_document_file_content_parts(payload.document_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Document not found: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    system_prompt = _build_prompt(payload)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(payload.conversation[-12:])
    user_content: list[dict[str, Any]] = []
    if payload.mode == "training":
        user_content.append(
            {
                "type": "text",
                "text": (
                    "Create the text-and-citation training plan from the attached manuals and the indexed-video "
                    "metadata below. Do not inspect or annotate images in this planning request."
                ),
            }
        )
    else:
        user_content.extend(
            [
                {"type": "text", "text": "Original video frame:"},
                {"type": "image_url", "image_url": {"url": payload.frame_data_url}},
            ]
        )
    if payload.mode != "training" and payload.annotated_frame_data_url:
        user_content.extend(
            [
                {"type": "text", "text": "Same frame with user annotations overlaid:"},
                {"type": "image_url", "image_url": {"url": payload.annotated_frame_data_url}},
            ]
        )
    if document_parts:
        user_content.append(
            {
                "type": "text",
                "text": "Attached PDF manual(s). Use these directly for document-grounded claims:",
            }
        )
        user_content.extend(document_parts)
    if payload.video_evidence:
        user_content.append(
            {
                "type": "text",
                "text": "Retrieved moments from the full-video timeline follow. Treat timestamps as source evidence:",
            }
        )
        for evidence_index, evidence in enumerate(payload.video_evidence[:12]):
            frame_data_url = evidence.get("frame_data_url")
            visible_evidence = {key: value for key, value in evidence.items() if key != "frame_data_url"}
            timestamp = visible_evidence.get("representative_timestamp", visible_evidence.get("start"))
            user_content.append(
                {
                    "type": "text",
                    "text": (
                        f"Evidence frame {evidence_index + 1} (timestamp {timestamp} seconds). "
                        "Any annotation assigned to this timestamp must be measured only on the image immediately below. "
                        "Metadata: "
                        + json.dumps(visible_evidence, ensure_ascii=False)
                    ),
                }
            )
            if (
                payload.mode != "training"
                and
                evidence_index < 4
                and isinstance(frame_data_url, str)
                and frame_data_url.startswith("data:image/")
            ):
                user_content.append({"type": "image_url", "image_url": {"url": frame_data_url}})
    user_content.append({"type": "text", "text": payload.question})
    messages.append({"role": "user", "content": user_content})

    request_body: dict[str, Any] = {
        "model": payload.model,
        "messages": messages,
        "stream": True,
        "response_format": {"type": "json_object"},
    }
    plugins = _pdf_plugins() if document_parts else None
    if plugins:
        request_body["plugins"] = plugins
    if model_supports_reasoning(payload.model):
        request_body["reasoning"] = {"effort": "medium" if payload.mode == "training" else "low"}

    async def stream() -> Any:
        try:
            async with httpx.AsyncClient(timeout=240) as client:
                async with client.stream(
                    "POST",
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=_openrouter_headers(),
                    json=request_body,
                ) as response:
                    if response.status_code >= 400:
                        text = await response.aread()
                        yield _sse(f"OpenRouter returned HTTP {response.status_code}: {text.decode()}", event="error")
                        return
                    planner_chunks: list[str] = []
                    async for line in response.aiter_lines():
                        parsed = parse_openrouter_sse_line(line)
                        if parsed == DONE_SENTINEL:
                            break
                        if parsed:
                            if payload.mode == "training":
                                planner_chunks.append(parsed)
                            else:
                                yield _sse(parsed)

                if payload.mode != "training":
                    yield _sse("[DONE]")
                    return

                training_response = _json_object_from_content("".join(planner_chunks))
                procedure = training_response.get("training_procedure")
                if not isinstance(procedure, dict):
                    raise ValueError("Training planner returned no training procedure")
                steps = procedure.get("steps")
                if not isinstance(steps, list) or not steps:
                    raise ValueError("Training planner returned no steps")

                normalized_steps: list[dict[str, Any]] = []
                for index, raw_step in enumerate(steps):
                    step = dict(raw_step) if isinstance(raw_step, dict) else {}
                    step["id"] = _training_step_id(step, index)
                    step["timestamp"] = None
                    step["end_timestamp"] = None
                    step["annotations"] = []
                    step["visual_status"] = "pending"
                    normalized_steps.append(step)
                procedure["steps"] = normalized_steps
                yield _sse(
                    json.dumps(training_response, ensure_ascii=False, separators=(",", ":")),
                    event="training_plan",
                )

                candidates = _training_candidates(payload.video_evidence)
                queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
                semaphore = asyncio.Semaphore(TRAINING_VISUAL_CONCURRENCY)

                async def enrich_step(step: dict[str, Any]) -> None:
                    step_id = str(step["id"])
                    try:
                        if not candidates:
                            raise ValueError("No indexed video frames are available for visual guidance")
                        await queue.put(
                            (
                                "training_step_progress",
                                {"step_id": step_id, "status": "selecting_frame"},
                            )
                        )
                        async with semaphore:
                            selection_prompt = build_training_frame_selection_prompt(
                                step_title=str(step.get("title") or "Training step"),
                                instruction=str(step.get("instruction") or ""),
                                expected_result=str(step.get("expected_result") or ""),
                                components=[str(value) for value in step.get("components", [])],
                                candidates=[candidate["public"] for candidate in candidates],
                            )
                            selection = await _openrouter_json_request(
                                client,
                                model=payload.model,
                                messages=[{"role": "user", "content": selection_prompt}],
                                reasoning_effort="low",
                            )
                            selected_id = selection.get("candidate_id")
                            selected = next(
                                (candidate for candidate in candidates if candidate["candidate_id"] == selected_id),
                                None,
                            )
                            if selected is None:
                                raise ValueError("Frame selector returned an unknown candidate")

                            timestamp = float(selected["public"]["timestamp"])
                            step["timestamp"] = timestamp
                            end_timestamp = selected["public"].get("end_timestamp")
                            step["end_timestamp"] = (
                                float(end_timestamp) if isinstance(end_timestamp, (int, float)) else None
                            )
                            await queue.put(
                                (
                                    "training_step_progress",
                                    {
                                        "step_id": step_id,
                                        "status": "pending",
                                        "timestamp": timestamp,
                                    },
                                )
                            )
                            # The browser generates visual guidance when this step opens.
                            # That request captures the exact displayed video frame, avoiding
                            # drift between an indexed representative image and the player.
                            step["annotations"] = []
                            step["visual_status"] = "pending"
                            step.pop("visual_error", None)
                            await queue.put(
                                (
                                    "training_step_complete",
                                    {"step_id": step_id, "step": step},
                                )
                            )
                    except (httpx.HTTPError, RuntimeError, ValueError, TypeError) as exc:
                        step["visual_status"] = "error"
                        step["visual_error"] = str(exc)
                        await queue.put(
                            (
                                "training_step_error",
                                {"step_id": step_id, "message": str(exc)},
                            )
                        )
                    finally:
                        await queue.put(("_step_done", {"step_id": step_id}))

                tasks = [asyncio.create_task(enrich_step(step)) for step in normalized_steps]
                completed = 0
                try:
                    while completed < len(tasks):
                        event_name, event_payload = await queue.get()
                        if event_name == "_step_done":
                            completed += 1
                            continue
                        yield _sse(
                            json.dumps(event_payload, ensure_ascii=False, separators=(",", ":")),
                            event=event_name,
                        )
                finally:
                    if completed < len(tasks):
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                yield _sse(
                    json.dumps(training_response, ensure_ascii=False, separators=(",", ":")),
                    event="training_complete",
                )
                yield _sse("[DONE]")
        except (httpx.HTTPError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            yield _sse(f"OpenRouter stream failed: {exc}", event="error")

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/training/annotations/regenerate")
async def regenerate_training_annotation(payload: TrainingAnnotationRequest) -> dict[str, Any]:
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY missing")
    prompt = build_training_annotation_prompt(
        step_title=payload.step_title,
        instruction=payload.instruction,
        expected_result=payload.expected_result,
        components=payload.components,
        timestamp=payload.timestamp,
        previous_annotations=payload.previous_annotations,
    )
    try:
        async with httpx.AsyncClient(timeout=240) as client:
            result = await _openrouter_json_request(
                client,
                model=payload.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": payload.frame_data_url}},
                        ],
                    }
                ],
                reasoning_effort="medium",
            )
    except (httpx.HTTPError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raw_annotations = result.get("annotations")
    annotations = normalize_generated_annotations(
        raw_annotations if isinstance(raw_annotations, list) else []
    )
    if not annotations:
        raise HTTPException(status_code=502, detail="Annotation model returned no usable annotations")
    return {"annotations": annotations}
