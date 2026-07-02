from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from prompts import build_prompt

app = FastAPI(title="OperatorOS RAGVLM Service", version="0.1.0")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:3000")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "OperatorOS")


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class InferRequest(BaseModel):
    question: str
    frame_data_url: str
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    retrieved_chunks: list[str] = Field(default_factory=list)
    conversation: list[dict[str, str]] = Field(default_factory=list)
    model: str = "google/gemini-3.1-pro-preview"


def _build_prompt(payload: InferRequest) -> str:
    transcript = "\n".join(
        f"[{segment.start:.2f}-{segment.end:.2f}] {segment.text}"
        for segment in payload.transcript_segments
    )
    docs = "\n".join(payload.retrieved_chunks) if payload.retrieved_chunks else "No manuals."
    return build_prompt(payload.question, json.dumps(payload.annotations), transcript, docs)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ragvlm/infer")
async def infer(payload: InferRequest) -> StreamingResponse:
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY missing")

    system_prompt = _build_prompt(payload)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(payload.conversation[-12:])
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": payload.frame_data_url}},
                {"type": "text", "text": payload.question},
            ],
        }
    )

    request_body = {
        "model": payload.model,
        "messages": messages,
        "stream": True,
    }

    async def stream() -> Any:
        async with httpx.AsyncClient(timeout=90) as client:
            async with client.stream(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": OPENROUTER_HTTP_REFERER,
                    "X-Title": OPENROUTER_APP_TITLE,
                },
                json=request_body,
            ) as response:
                if response.status_code >= 400:
                    text = await response.aread()
                    raise HTTPException(status_code=response.status_code, detail=text.decode())
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        payload_line = line[6:].strip()
                        if payload_line == "[DONE]":
                            yield "data: [DONE]\n\n"
                            break
                        try:
                            parsed = json.loads(payload_line)
                            delta = parsed["choices"][0]["delta"].get("content", "")
                            if delta:
                                yield f"data: {delta}\n\n"
                        except Exception:
                            continue

    return StreamingResponse(stream(), media_type="text/event-stream")
