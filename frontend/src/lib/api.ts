import type { Annotation, TranscriptWindowResponse } from "@/lib/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8000";

export async function uploadMedia(file: File): Promise<{ video_id: string }> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${BASE_URL}/media/ingest`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function getTranscriptWindow(
  videoId: string,
  timestamp: number
): Promise<TranscriptWindowResponse> {
  const response = await fetch(
    `${BASE_URL}/transcript/window?video_id=${encodeURIComponent(videoId)}&timestamp=${timestamp}`
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function askQuestion(input: {
  session_id: string;
  video_id: string;
  timestamp: number;
  frame_data_url: string;
  question: string;
  annotations: Annotation[];
  transcript_window: TranscriptWindowResponse;
  document_ids: string[];
}): Promise<Response> {
  return fetch(`${BASE_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function startTracking(input: {
  session_id: string;
  video_id: string;
  timestamp: number;
  frame_data_url: string;
  question: string;
  annotations: Annotation[];
}): Promise<{ tracking_job_id: string }> {
  const response = await fetch(`${BASE_URL}/tracking/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}
