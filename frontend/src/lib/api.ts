import type {
  Annotation,
  ComparisonRevealResponse,
  DocumentIngestResponse,
  DocumentStatusResponse,
  MediaIngestResponse,
  InteractionMode,
  TimelineStatusResponse,
  TrackingOverlayManifest,
  TrackingTarget,
  TranscriptWindowResponse,
  VideoMetadataResponse,
} from "@/lib/types";

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

export async function getVideoMetadata(videoId: string): Promise<VideoMetadataResponse> {
  const response = await fetch(`${BASE_URL}/media/metadata?video_id=${encodeURIComponent(videoId)}`);
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
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

export async function uploadDocument(file: File): Promise<DocumentIngestResponse> {
  const formData = new FormData();
  formData.append("file", file);
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/documents/ingest`, {
      method: "POST",
      body: formData,
    });
  } catch (error) {
    const message = error instanceof Error && error.message ? error.message : "network request failed";
    throw new Error(`Unable to upload document for RAG: ${message}.`);
  }
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export async function getTimelineStatus(videoId: string): Promise<TimelineStatusResponse> {
  const response = await fetch(
    `${BASE_URL}/video/timeline/status?video_id=${encodeURIComponent(videoId)}`
  );
  if (!response.ok) throw new Error(await readApiError(response));
  return response.json();
}

export async function rebuildTimeline(videoId: string): Promise<TimelineStatusResponse> {
  const response = await fetch(
    `${BASE_URL}/video/timeline/rebuild?video_id=${encodeURIComponent(videoId)}`,
    { method: "POST" }
  );
  if (!response.ok) throw new Error(await readApiError(response));
  return response.json();
}

export async function cancelTimeline(videoId: string): Promise<void> {
  const response = await fetch(
    `${BASE_URL}/video/timeline/cancel?video_id=${encodeURIComponent(videoId)}`,
    { method: "POST" }
  );
  if (!response.ok) throw new Error(await readApiError(response));
}

export function getTimelineFrameUrl(videoId: string, timestamp: number): string {
  return `${BASE_URL}/video/timeline/frame?video_id=${encodeURIComponent(videoId)}&timestamp=${timestamp}`;
}

export function getDocumentFileUrl(documentId: string, page?: number | null): string {
  const base = `${BASE_URL}/documents/${encodeURIComponent(documentId)}/file`;
  return page ? `${base}#page=${page}&view=FitH` : base;
}

export async function transcribeSpeech(blob: Blob, filename = "recording.webm"): Promise<string> {
  const formData = new FormData();
  formData.append("file", blob, filename);
  const response = await fetch(`${BASE_URL}/speech/transcribe`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) throw new Error(await readApiError(response));
  const payload = (await response.json()) as { text?: unknown };
  return typeof payload.text === "string" ? payload.text.trim() : "";
}

export async function getPreloadedDocuments(): Promise<DocumentIngestResponse[]> {
  const response = await fetch(`${BASE_URL}/documents/preloaded`);
  if (!response.ok) throw new Error(await readApiError(response));
  const payload = (await response.json()) as { documents?: DocumentIngestResponse[] };
  return Array.isArray(payload.documents) ? payload.documents : [];
}

export async function getDocumentStatus(documentId: string): Promise<DocumentStatusResponse> {
  const response = await fetch(
    `${BASE_URL}/documents/${encodeURIComponent(documentId)}/status`
  );
  if (!response.ok) throw new Error(await readApiError(response));
  return response.json();
}

export async function askQuestion(input: {
  session_id: string;
  video_id: string;
  video_title?: string;
  timestamp: number;
  frame_data_url: string;
  annotated_frame_data_url?: string;
  question: string;
  annotations: Annotation[];
  transcript_window: TranscriptWindowResponse;
  document_ids: string[];
  model?: string;
  additional_notes?: string;
  mode?: InteractionMode;
  tracking_context?: Array<{ label: string; start: number; end: number }>;
}, signal?: AbortSignal): Promise<Response> {
  return fetch(`${BASE_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
    signal,
  });
}

export async function clearChatSession(sessionId: string): Promise<void> {
  const response = await fetch(`${BASE_URL}/chat/session/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
}

export async function askComparison(input: {
  session_id: string;
  video_id?: string;
  video_title?: string;
  timestamp?: number;
  frame_data_url?: string;
  annotated_frame_data_url?: string;
  question: string;
  annotations: Annotation[];
  transcript_window?: TranscriptWindowResponse;
  document_ids: string[];
  model?: string;
  retry_of?: string;
  additional_notes?: string;
}): Promise<Response> {
  return fetch(`${BASE_URL}/chat/comparisons/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function revealComparison(
  comparisonId: string,
  selectedLabel: "A" | "B"
): Promise<ComparisonRevealResponse> {
  const response = await fetch(
    `${BASE_URL}/chat/comparisons/${encodeURIComponent(comparisonId)}/reveal`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_label: selectedLabel }),
    }
  );
  if (!response.ok) throw new Error(await readApiError(response));
  const result = (await response.json()) as ComparisonRevealResponse & {
    pipelines?: Record<"A" | "B", string>;
  };
  return { ...result, mapping: result.mapping ?? result.pipelines };
}

export async function getConvertedText(documentId: string): Promise<string> {
  const response = await fetch(
    `${BASE_URL}/documents/${encodeURIComponent(documentId)}/converted-text`
  );
  if (!response.ok) throw new Error(await readApiError(response));
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const value = (await response.json()) as { text?: string; content?: string; markdown?: string };
    return value.text ?? value.content ?? value.markdown ?? "";
  }
  return response.text();
}

export function getConvertedTextDownloadUrl(documentId: string): string {
  return `${BASE_URL}/documents/${encodeURIComponent(documentId)}/converted-text/download`;
}

export async function startTracking(input: {
  session_id: string;
  video_id: string;
  timestamp: number;
  frame_data_url: string;
  question: string;
  segmentation_prompt?: string;
  annotations: Annotation[];
  targets?: TrackingTarget[];
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

export async function cancelTracking(trackingJobId: string): Promise<void> {
  const response = await fetch(
    `${BASE_URL}/tracking/cancel/${encodeURIComponent(trackingJobId)}`,
    { method: "POST" }
  );
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
}

export async function getTrackingOverlays(trackingJobId: string): Promise<TrackingOverlayManifest> {
  const response = await fetch(`${BASE_URL}/tracking/overlays/${encodeURIComponent(trackingJobId)}`);
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.json();
}

export async function exportTrackingVideo(input: {
  video_id: string;
  layers: Array<{
    job_id: string;
    track_ids: string[];
    color: string;
    label: string;
  }>;
}): Promise<Blob> {
  const response = await fetch(`${BASE_URL}/tracking/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readApiError(response));
  }
  return response.blob();
}
