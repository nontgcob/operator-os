import type { Annotation, MediaIngestResponse, TranscriptWindowResponse } from "@/lib/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8000";

async function readApiError(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) return `Request failed with status ${response.status}`;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      try {
        const nested = JSON.parse(parsed.detail) as { detail?: unknown };
        if (typeof nested.detail === "string") return nested.detail;
      } catch {
        // The detail is already plain text.
      }
      return parsed.detail;
    }
    if (parsed.detail) return JSON.stringify(parsed.detail);
  } catch {
    // Fall through to the plain response body.
  }
  return text;
}

function mediaIngestNetworkError(error: unknown): Error {
  const message = error instanceof Error && error.message ? error.message : "network request failed";
  return new Error(
    `Unable to reach orchestrator while starting media ingest: ${message}. ` +
      "Large YouTube downloads and first-run transcription can take several minutes; check backend logs and retry."
  );
}

export function getMediaSourceUrl(videoId: string): string {
  return `${BASE_URL}/media/source?video_id=${encodeURIComponent(videoId)}`;
}

export async function uploadMedia(file: File): Promise<MediaIngestResponse> {
  const formData = new FormData();
  formData.append("file", file);
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/media/ingest`, {
      method: "POST",
      body: formData,
    });
  } catch (error) {
    throw mediaIngestNetworkError(error);
  }
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export async function ingestYoutubeUrl(youtubeUrl: string): Promise<MediaIngestResponse> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/media/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ youtube_url: youtubeUrl }),
    });
  } catch (error) {
    throw mediaIngestNetworkError(error);
  }
  if (!response.ok) {
    throw new Error(await readApiError(response));
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
    throw new Error(await readApiError(response));
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
    throw new Error(await readApiError(response));
  }
  return response.json();
}
