"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AnnotationControls } from "@/components/AnnotationControls";
import { AnnotationOverlay } from "@/components/AnnotationOverlay";
import { ComparisonTurnCard } from "@/components/ComparisonTurnCard";
import { TrackingOverlayCanvas } from "@/components/TrackingOverlayCanvas";
import {
  comparisonExportLines,
  createComparisonTurn,
  reduceComparisonEvent,
  revealTurn,
} from "@/lib/comparisonState";
import { readComparisonSSE } from "@/lib/comparisonStream";
import { parseModelResponse, partialAnswerFromModelResponse } from "@/lib/parseResponse";
import { explicitlyRequestsTracking } from "@/lib/trackingIntent";
import { excludeTrackedTargets } from "@/lib/trackingDedup";
import { colorizeTrackingTargets, createTrackingLayers } from "@/lib/trackingLayers";
import {
  askComparison,
  askQuestion,
  cancelTracking,
  clearChatSession,
  exportTrackingVideo,
  revealComparison,
  getMediaSourceUrl,
  getPreloadedDocuments,
  getTrackingOverlays,
  getDocumentStatus,
  getTranscriptWindow,
  getVideoMetadata,
  ingestYoutubeUrl,
  startTracking,
  uploadDocument,
  uploadMedia,
} from "@/lib/api";
import type {
  Annotation,
  AnnotationUndoEntry,
  AnnotationType,
  AnswerLabel,
  ComparisonTurn,
  TrackingLayer,
  TrackingOverlay,
  TrackingTarget,
  TrackingTargetProgress,
  TranscriptWindowResponse,
} from "@/lib/types";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  error?: boolean;
  model?: string;
  documents?: string[];
  annotatedSnapshot?: boolean;
  comparison?: ComparisonTurn;
  comparisonQuestion?: string;
  cancelled?: boolean;
}

interface UploadedDocument {
  id: string;
  filename: string;
  chunkCount: number;
  source: "user" | "preloaded";
}

interface QueuedChatMessage {
  id: string;
  text: string;
  createdAt: string;
  model: string;
  timestamp: number;
  documentIds: string[];
  documentNames: string[];
  annotations: Annotation[];
  includeAnnotatedSnapshot: boolean;
  additionalNotes: string;
}

interface ActiveTrackingJob {
  id: string;
  progress: TrackingTargetProgress[];
  status: string;
  error: string;
  liveOverlays: TrackingOverlay[];
  cancelling: boolean;
}

const RAGVLM_MODELS = [
  {
    family: "Gemini",
    label: "Gemini 3.1 Pro Preview",
    value: "google/gemini-3.1-pro-preview",
  },
  {
    family: "Gemini",
    label: "Gemini 3 Flash Preview",
    value: "google/gemini-3-flash-preview",
  },
  {
    family: "GPT",
    label: "GPT-5 Chat",
    value: "openai/gpt-5-chat",
  },
  {
    family: "GPT",
    label: "GPT-5 Mini",
    value: "openai/gpt-5-mini",
  },
  {
    family: "Qwen",
    label: "Qwen3 VL 235B Instruct",
    value: "qwen/qwen3-vl-235b-a22b-instruct",
  },
  {
    family: "Qwen",
    label: "Qwen3 VL 8B Instruct",
    value: "qwen/qwen3-vl-8b-instruct",
  },
];

const DEFAULT_RAGVLM_MODEL = "google/gemini-3.1-pro-preview";
type Theme = "light" | "dark";

function ThemeIcon({ theme }: { theme: Theme }) {
  return theme === "dark" ? (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41" />
    </svg>
  ) : (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M20.2 15.1A8.5 8.5 0 0 1 8.9 3.8 8.5 8.5 0 1 0 20.2 15.1Z" />
    </svg>
  );
}

function formatTimestamp(seconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}

function shortTrackingLabel(prompt: string): string {
  const label = prompt
    .replace(/^(track|follow|trace|monitor)\s+(the\s+)?/i, "")
    .replace(/[.?!]+$/, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!label) return "Tracked object";
  return label.length > 48 ? `${label.slice(0, 45).trimEnd()}...` : label;
}

const TRACKING_STAGE_LABELS: Record<TrackingTargetProgress["stage"], string> = {
  queued: "Queued",
  preparing: "Preparing video",
  tracking: "Tracking frames",
  finalizing: "Finalizing overlays",
  complete: "Complete",
  cancelled: "Cancelled",
  error: "Failed",
};

function annotationPoints(annotation: Annotation): Array<{ x: number; y: number }> {
  return (annotation.points ?? []).flatMap((point) => {
    if (Array.isArray(point)) {
      const [x, y] = point;
      return typeof x === "number" && typeof y === "number" ? [{ x, y }] : [];
    }
    return typeof point.x === "number" && typeof point.y === "number" ? [point] : [];
  });
}

function pathPoints(d: string): Array<{ x: number; y: number }> {
  const numbers = d.match(/[-+]?\d*\.?\d+/g)?.map(Number) ?? [];
  const points: Array<{ x: number; y: number }> = [];
  for (let index = 0; index < numbers.length - 1; index += 2) {
    points.push({ x: numbers[index], y: numbers[index + 1] });
  }
  return points;
}

function drawCanvasArrow(
  ctx: CanvasRenderingContext2D,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  strokeWidth: number
) {
  const angle = Math.atan2(y2 - y1, x2 - x1);
  const headLength = Math.max(10, strokeWidth * 4);
  const shaftEndX = x2 - headLength * Math.cos(angle);
  const shaftEndY = y2 - headLength * Math.sin(angle);

  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(shaftEndX, shaftEndY);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(
    x2 - headLength * Math.cos(angle - Math.PI / 6),
    y2 - headLength * Math.sin(angle - Math.PI / 6)
  );
  ctx.lineTo(
    x2 - headLength * Math.cos(angle + Math.PI / 6),
    y2 - headLength * Math.sin(angle + Math.PI / 6)
  );
  ctx.closePath();
  ctx.fill();
}

function readSSE(
  response: Response,
  {
    onDelta,
    onError,
  }: {
    onDelta: (chunk: string) => void;
    onError: (message: string) => void;
  }
) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Missing stream body");
  const decoder = new TextDecoder();
  return (async () => {
    let buffer = "";
    function processEvent(event: string) {
      let eventType = "message";
      const data: string[] = [];
      for (const line of event.split("\n")) {
        if (line.startsWith("event: ")) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          data.push(line.slice(6));
        }
      }
      const payload = data.join("\n");
      if (!payload) return false;
      if (eventType === "error") {
        onError(payload);
        return true;
      }
      if (payload.trim() === "[DONE]") return true;
      onDelta(payload);
      return false;
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        if (buffer.trim() && processEvent(buffer)) return;
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";

      for (const event of events) {
        if (processEvent(event)) return;
      }
    }
  })();
}

export default function Home() {
  const [theme, setTheme] = useState<Theme>("light");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [videoUrl, setVideoUrl] = useState<string>("");
  const [originalVideoUrl, setOriginalVideoUrl] = useState<string>("");
  const [videoId, setVideoId] = useState<string>("");
  const [videoTitle, setVideoTitle] = useState<string>("");
  const [sessionId] = useState(() => crypto.randomUUID());
  const [question, setQuestion] = useState("");
  const [additionalNotes, setAdditionalNotes] = useState("");
  const [selectedModel, setSelectedModel] = useState(DEFAULT_RAGVLM_MODEL);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [documents, setDocuments] = useState<UploadedDocument[]>([]);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [documentUploading, setDocumentUploading] = useState(false);
  const [documentStatus, setDocumentStatus] = useState("");
  const [documentError, setDocumentError] = useState("");
  const [revealingComparisonId, setRevealingComparisonId] = useState("");
  const [loading, setLoading] = useState(false);
  const [stoppingResponse, setStoppingResponse] = useState(false);
  const [queuedChatMessages, setQueuedChatMessages] = useState<QueuedChatMessage[]>([]);
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState("");
  const [ingestStatus, setIngestStatus] = useState("");
  const [pendingVideoReadyStatus, setPendingVideoReadyStatus] = useState("");
  const [videoMetadataLoaded, setVideoMetadataLoaded] = useState(false);
  const [videoAspectRatio, setVideoAspectRatio] = useState(1);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [timestamp, setTimestamp] = useState(0);
  const [videoTimeOffset, setVideoTimeOffset] = useState(0);
  const [transcriptWindow, setTranscriptWindow] = useState<TranscriptWindowResponse | null>(null);
  const [transcriptError, setTranscriptError] = useState("");
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [modelAnnotations, setModelAnnotations] = useState<Annotation[]>([]);
  const [trackingLayers, setTrackingLayers] = useState<TrackingLayer[]>([]);
  const [trackingEnabled, setTrackingEnabled] = useState(false);
  const [showTrackingOverlays, setShowTrackingOverlays] = useState(false);
  const [activeTrackingJobs, setActiveTrackingJobs] = useState<ActiveTrackingJob[]>([]);
  const [trackingStatus, setTrackingStatus] = useState("");
  const [trackingError, setTrackingError] = useState("");
  const [trackingExporting, setTrackingExporting] = useState(false);
  const [trackingExportStatus, setTrackingExportStatus] = useState("");
  const [chatClearStatus, setChatClearStatus] = useState("");
  const [sendAnnotatedSnapshot, setSendAnnotatedSnapshot] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [activeTool, setActiveTool] = useState<AnnotationType>("cursor");
  const [annotationUndoStack, setAnnotationUndoStack] = useState<AnnotationUndoEntry[]>([]);
  const [drawColor, setDrawColor] = useState("#ef4444");
  const [textAnnotation, setTextAnnotation] = useState("");
  const [showTranscript, setShowTranscript] = useState(false);
  const localFileInputRef = useRef<HTMLInputElement | null>(null);
  const trackingEventSourcesRef = useRef<Map<string, EventSource>>(new Map());
  const activeChatAbortRef = useRef<AbortController | null>(null);
  const processingChatRef = useRef(false);
  const queuedChatMessagesRef = useRef<QueuedChatMessage[]>([]);
  const trackingLayersRef = useRef<TrackingLayer[]>([]);
  const activeTrackingJobsRef = useRef<ActiveTrackingJob[]>([]);
  const contextGenerationRef = useRef(0);

  useEffect(() => {
    const savedTheme = window.localStorage.getItem("operatoros-theme");
    const resolvedTheme: Theme =
      savedTheme === "dark" || savedTheme === "light"
        ? savedTheme
        : window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
    setTheme(resolvedTheme);
    document.documentElement.dataset.theme = resolvedTheme;
  }, []);

  useEffect(() => {
    trackingLayersRef.current = trackingLayers;
  }, [trackingLayers]);

  useEffect(() => {
    activeTrackingJobsRef.current = activeTrackingJobs;
  }, [activeTrackingJobs]);

  useEffect(() => {
    let cancelled = false;
    async function loadPreloadedManuals() {
      try {
        const preloaded = await getPreloadedDocuments();
        if (cancelled) return;
        setDocuments((current) => {
          const byId = new Map(current.map((document) => [document.id, document]));
          preloaded.forEach((document) => {
            byId.set(document.document_id, {
              id: document.document_id,
              filename: document.filename,
              chunkCount: document.chunk_count,
              source: "preloaded",
            });
          });
          return [...byId.values()];
        });
        setDocumentError((current) =>
          current.startsWith("Preloaded manuals unavailable:") ? "" : current
        );
      } catch (error) {
        if (!cancelled) setDocumentError(`Preloaded manuals unavailable: ${errorMessage(error)}`);
      }
    }
    void loadPreloadedManuals();
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  function toggleTheme() {
    setTheme((current) => {
      const next = current === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      window.localStorage.setItem("operatoros-theme", next);
      return next;
    });
  }

  function closeTrackingEventSources() {
    trackingEventSourcesRef.current.forEach((source) => source.close());
    trackingEventSourcesRef.current.clear();
  }

  function replaceQueuedChatMessages(
    updater: (current: QueuedChatMessage[]) => QueuedChatMessage[]
  ) {
    const next = updater(queuedChatMessagesRef.current);
    queuedChatMessagesRef.current = next;
    setQueuedChatMessages(next);
  }

  function replaceTrackingLayers(
    updater: TrackingLayer[] | ((current: TrackingLayer[]) => TrackingLayer[])
  ) {
    const next = typeof updater === "function" ? updater(trackingLayersRef.current) : updater;
    trackingLayersRef.current = next;
    setTrackingLayers(next);
  }

  function replaceActiveTrackingJobs(
    updater: ActiveTrackingJob[] | ((current: ActiveTrackingJob[]) => ActiveTrackingJob[])
  ) {
    const next = typeof updater === "function" ? updater(activeTrackingJobsRef.current) : updater;
    activeTrackingJobsRef.current = next;
    setActiveTrackingJobs(next);
  }

  function stopActiveResponse() {
    if (!activeChatAbortRef.current) return;
    setStoppingResponse(true);
    activeChatAbortRef.current.abort();
  }

  function resetVideoContext() {
    contextGenerationRef.current += 1;
    activeChatAbortRef.current?.abort();
    activeChatAbortRef.current = null;
    processingChatRef.current = false;
    replaceQueuedChatMessages(() => []);
    closeTrackingEventSources();
    if (videoUrl && videoUrl.startsWith("blob:")) {
      URL.revokeObjectURL(videoUrl);
    }
    if (originalVideoUrl && originalVideoUrl !== videoUrl && originalVideoUrl.startsWith("blob:")) {
      URL.revokeObjectURL(originalVideoUrl);
    }
    setVideoUrl("");
    setOriginalVideoUrl("");
    setVideoId("");
    setVideoTitle("");
    setVideoMetadataLoaded(false);
    setVideoAspectRatio(1);
    setPendingVideoReadyStatus("");
    setTimestamp(0);
    setVideoTimeOffset(0);
    setTranscriptWindow(null);
    setTranscriptError("");
    setAnnotations([]);
    setModelAnnotations([]);
    setAnnotationUndoStack([]);
    replaceTrackingLayers([]);
    replaceActiveTrackingJobs([]);
    setTrackingStatus("");
    setTrackingError("");
    setTrackingExporting(false);
    setTrackingExportStatus("");
    setChatMessages([]);
    setChatClearStatus("");
    setAdditionalNotes("");
    setStoppingResponse(false);
    setLoading(false);
  }

  function downloadChat() {
    if (!chatMessages.length) return;

    const exportedAt = new Date();
    const lines = [
      "# OperatorOS Conversation",
      "",
      `- Exported: ${exportedAt.toLocaleString()}`,
      `- Session ID: ${sessionId}`,
      `- Video: ${videoTitle || videoId || "Unknown"}`,
      "",
      ...chatMessages.flatMap((message) => {
        if (message.comparison) {
          return comparisonExportLines(message.comparison);
        }
        const details = [
          message.model ? `Model: ${message.model}` : "",
          message.documents?.length ? `RAG documents: ${message.documents.join(", ")}` : "",
          message.role === "user"
            ? `Annotated snapshot: ${message.annotatedSnapshot ? "sent" : "not sent"}`
            : "",
          message.error ? "Status: error" : "",
          message.cancelled ? "Status: stopped" : "",
        ].filter(Boolean);
        return [
          `## ${message.role === "user" ? "User" : "Operator OS"} — ${new Date(message.createdAt).toLocaleString()}`,
          "",
          ...(details.length ? [details.join(" · "), ""] : []),
          message.content,
          "",
        ];
      }),
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const filenameTimestamp = exportedAt.toISOString().replace(/[:.]/g, "-");
    link.href = downloadUrl;
    link.download = `operator-os-chat-${filenameTimestamp}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(downloadUrl);
  }

  function clearAnnotations() {
    setAnnotations([]);
    setAnnotationUndoStack([]);
  }

  function removeTracking() {
    if (!trackingLayers.length) return;
    replaceTrackingLayers([]);
    setTrackingStatus("All tracking items removed.");
    setTrackingError("");
    setTrackingExportStatus("");
  }

  async function exportVisibleTrackingVideo() {
    const visibleLayers = showTrackingOverlays
      ? trackingLayersRef.current.filter((layer) => layer.visible)
      : [];
    if (!videoId || !visibleLayers.length || trackingExporting) return;

    setTrackingExporting(true);
    setTrackingExportStatus("Rendering the visible tracking items into a video...");
    setTrackingError("");
    try {
      const blob = await exportTrackingVideo({
        video_id: videoId,
        layers: visibleLayers.map((layer) => ({
          job_id: layer.jobId,
          track_ids: Array.from(new Set(layer.overlays.map((overlay) => overlay.track_id))),
          color: layer.color,
          label: layer.label,
        })),
      });
      const downloadUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const safeTitle = (videoTitle || "operatoros-tracking")
        .replace(/[^a-z0-9_-]+/gi, "-")
        .replace(/^-+|-+$/g, "") || "operatoros-tracking";
      link.href = downloadUrl;
      link.download = `${safeTitle}-tracked.mp4`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
      setTrackingExportStatus("Tracked video exported.");
    } catch (error) {
      setTrackingExportStatus("");
      setTrackingError(`Unable to export tracked video: ${errorMessage(error)}`);
    } finally {
      setTrackingExporting(false);
    }
  }

  async function requestTrackingCancellation(job: ActiveTrackingJob) {
    const targetNames = job.progress.map((target) => target.label).join(", ");
    const confirmed = window.confirm(
      `Cancel tracking for ${targetNames || "this item"}? Completed progress from this job will be discarded.`
    );
    if (!confirmed) return;

    replaceActiveTrackingJobs((current) =>
      current.map((candidate) =>
        candidate.id === job.id
          ? { ...candidate, cancelling: true, status: "Cancelling tracking..." }
          : candidate
      )
    );
    try {
      await cancelTracking(job.id);
    } catch (error) {
      replaceActiveTrackingJobs((current) =>
        current.map((candidate) =>
          candidate.id === job.id
            ? {
                ...candidate,
                cancelling: false,
                error: `Unable to cancel: ${errorMessage(error)}`,
              }
            : candidate
        )
      );
    }
  }

  function restoreFullVideo() {
    setAnnotations([]);
    setModelAnnotations([]);
    setAnnotationUndoStack([]);
    replaceTrackingLayers([]);
    setTrackingStatus("");
    setTrackingError("");
    setTrackingExportStatus("");
    setShowTrackingOverlays(false);
    setVideoTimeOffset(0);
    setTimestamp(0);
    if (originalVideoUrl && videoUrl !== originalVideoUrl) {
      setVideoUrl(originalVideoUrl);
      setPendingVideoReadyStatus("Full original video restored.");
    }
  }

  async function clearAllContext() {
    const confirmed = window.confirm(
      "Clear all conversation, annotations, tracking, notes, and queued work? The video, cached transcript, PDFs, and PDF selections will be kept."
    );
    if (!confirmed) return;

    contextGenerationRef.current += 1;
    activeChatAbortRef.current?.abort();
    activeChatAbortRef.current = null;
    processingChatRef.current = false;
    replaceQueuedChatMessages(() => []);
    const jobsToCancel = activeTrackingJobsRef.current;
    await Promise.allSettled(jobsToCancel.map((job) => cancelTracking(job.id)));
    closeTrackingEventSources();

    setChatMessages([]);
    setQuestion("");
    setAdditionalNotes("");
    setAnnotations([]);
    setModelAnnotations([]);
    setAnnotationUndoStack([]);
    replaceTrackingLayers([]);
    replaceActiveTrackingJobs([]);
    setTrackingStatus("");
    setTrackingError("");
    setTrackingExporting(false);
    setTrackingExportStatus("");
    setShowTrackingOverlays(false);
    setTrackingEnabled(false);
    setSendAnnotatedSnapshot(false);
    setActiveTool("cursor");
    setTextAnnotation("");
    setShowTranscript(false);
    setRevealingComparisonId("");
    setSelectedModel(DEFAULT_RAGVLM_MODEL);
    setStoppingResponse(false);
    setLoading(false);
    setDocumentStatus("");
    setDocumentError("");
    setChatClearStatus("Clearing all conversation context...");
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = 0;
    }
    setTimestamp(0);
    setVideoTimeOffset(0);
    try {
      await clearChatSession(sessionId);
      setChatClearStatus("All context cleared. Video, transcript, PDFs, and PDF selections were kept.");
    } catch (error) {
      setChatClearStatus(`Local context cleared, but backend conversation memory could not be cleared: ${errorMessage(error)}`);
    }
  }

  function undoAnnotation() {
    const entry = annotationUndoStack.at(-1);
    if (!entry) return;
    setAnnotations((current) => {
      if (entry.op === "pop") {
        return current.slice(0, Math.max(0, current.length - entry.count));
      }
      if (entry.op === "insert") {
        const next = [...current];
        next.splice(entry.idx, 0, entry.annotation);
        return next;
      }
      const next = [...current];
      if (entry.idx >= 0 && entry.idx < next.length) {
        next[entry.idx] = entry.previous;
      }
      return next;
    });
    setAnnotationUndoStack((current) => current.slice(0, -1));
  }

  function errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : "Ingestion failed";
  }

  function videoElementErrorMessage(video: HTMLVideoElement | null): string {
    const mediaError = video?.error;
    if (!mediaError) return "The browser did not provide a specific media error.";
    const details = mediaError.message ? ` ${mediaError.message}` : "";
    switch (mediaError.code) {
      case 1:
        return `The browser aborted the media load.${details}`;
      case 2:
        return `A network error interrupted the media load.${details}`;
      case 3:
        return `The browser could not decode the media file.${details}`;
      case 4:
        return `The media source is missing or uses an unsupported format.${details}`;
      default:
        return `The browser reported media error ${mediaError.code}.${details}`;
    }
  }

  function shouldShowYoutubeCookieHelp(message: string): boolean {
    const lower = message.toLowerCase();
    if (lower.includes("./data/ytdlp/cookies.txt")) return false;
    return (
      lower.includes("not a bot") ||
      lower.includes("http error 429") ||
      lower.includes("too many requests") ||
      lower.includes("cookies")
    );
  }

  async function captureFrame(): Promise<string> {
    const video = videoRef.current;
    if (!video) throw new Error("Video not ready");
    if (!videoMetadataLoaded || video.videoWidth === 0 || video.videoHeight === 0) {
      throw new Error("Video metadata has not loaded yet");
    }
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas unavailable");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.9);
  }

  async function captureAnnotatedFrame(): Promise<string> {
    const video = videoRef.current;
    if (!video) throw new Error("Video not ready");
    if (!videoMetadataLoaded || video.videoWidth === 0 || video.videoHeight === 0) {
      throw new Error("Video metadata has not loaded yet");
    }

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas unavailable");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    const x = (value = 0) => (value / 1000) * canvas.width;
    const y = (value = 0) => (value / 1000) * canvas.height;
    const radius = (value = 0) => (value / 1000) * canvas.height;
    const scaleStroke = (value = 15) => Math.max(2, (value / 1000) * canvas.height * 1.5);

    for (const annotation of annotations) {
      const color = annotation.color ?? "#ff6b6b";
      const strokeWidth = scaleStroke(annotation.strokeWidth);
      ctx.save();
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = strokeWidth;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";

      if (
        annotation.type === "rect" &&
        annotation.x !== undefined &&
        annotation.y !== undefined &&
        annotation.width !== undefined &&
        annotation.height !== undefined
      ) {
        if (annotation.fill && annotation.fill !== "none") {
          ctx.fillStyle = annotation.fill;
          ctx.fillRect(x(annotation.x), y(annotation.y), x(annotation.width), y(annotation.height));
        }
        ctx.strokeRect(x(annotation.x), y(annotation.y), x(annotation.width), y(annotation.height));
      } else if (
        annotation.type === "circle" &&
        (annotation.cx ?? annotation.x) !== undefined &&
        (annotation.cy ?? annotation.y) !== undefined &&
        (annotation.r ?? annotation.radius) !== undefined
      ) {
        ctx.beginPath();
        const r = radius(annotation.r ?? annotation.radius);
        ctx.ellipse(x(annotation.cx ?? annotation.x), y(annotation.cy ?? annotation.y), r, r, 0, 0, Math.PI * 2);
        if (annotation.fill && annotation.fill !== "none") ctx.fill();
        ctx.stroke();
      } else if (annotation.type === "path") {
        const points = annotationPoints(annotation).length
          ? annotationPoints(annotation)
          : pathPoints(annotation.d ?? "");
        if (points.length > 1) {
          ctx.beginPath();
          ctx.moveTo(x(points[0].x), y(points[0].y));
          points.slice(1).forEach((point) => ctx.lineTo(x(point.x), y(point.y)));
          ctx.stroke();
        }
      } else if (annotation.type === "polygon") {
        const points = annotationPoints(annotation);
        if (points.length > 1) {
          ctx.beginPath();
          ctx.moveTo(x(points[0].x), y(points[0].y));
          points.slice(1).forEach((point) => ctx.lineTo(x(point.x), y(point.y)));
          ctx.closePath();
          if (annotation.fill && annotation.fill !== "none") ctx.fill();
          ctx.stroke();
        }
      } else if (
        annotation.type === "arrow" &&
        annotation.x1 !== undefined &&
        annotation.y1 !== undefined &&
        annotation.x2 !== undefined &&
        annotation.y2 !== undefined
      ) {
        drawCanvasArrow(ctx, x(annotation.x1), y(annotation.y1), x(annotation.x2), y(annotation.y2), strokeWidth);
      } else if (
        (annotation.type === "text" || annotation.type === "number") &&
        annotation.x !== undefined &&
        annotation.y !== undefined
      ) {
        const text = annotation.text ?? annotation.content ?? annotation.value?.toString();
        if (text) {
          ctx.font = `700 ${Math.max(14, radius(annotation.fontSize ?? 28) * 1.5)}px sans-serif`;
          ctx.lineWidth = Math.max(2, strokeWidth * 0.3);
          ctx.strokeStyle = "#161b22";
          ctx.strokeText(text, x(annotation.x), y(annotation.y));
          ctx.fillStyle = color;
          ctx.fillText(text, x(annotation.x), y(annotation.y));
        }
      }
      ctx.restore();
    }

    return canvas.toDataURL("image/jpeg", 0.9);
  }

  async function loadTranscriptWindow(videoId: string, timestamp: number): Promise<TranscriptWindowResponse> {
    try {
      const transcript = await getTranscriptWindow(videoId, timestamp);
      setTranscriptWindow(transcript);
      setTranscriptError(transcript.warning ?? "");
      return transcript;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Transcript unavailable";
      setTranscriptError(message);
      const fallbackTranscript = {
        timestamp,
        start: Math.max(0, timestamp - 30),
        end: timestamp + 15,
        segments: [],
        source: "empty" as const,
        warning: message,
      };
      setTranscriptWindow(fallbackTranscript);
      return fallbackTranscript;
    }
  }

  function toggleDocumentSelection(documentId: string) {
    setSelectedDocumentIds((current) =>
      current.includes(documentId)
        ? current.filter((id) => id !== documentId)
        : [...current, documentId]
    );
  }

  async function handleDocumentUpload(file: File) {
    setDocumentUploading(true);
    setDocumentError("");
    setDocumentStatus(`Uploading ${file.name}...`);
    try {
      const result = await uploadDocument(file);
      setDocuments((current) => {
        const nextDocument = {
          id: result.document_id,
          filename: result.filename,
          chunkCount: 0,
          source: "user" as const,
        };
        return [...current.filter((document) => document.id !== result.document_id), nextDocument];
      });
      const isPdf = file.name.toLowerCase().endsWith(".pdf");
      if (isPdf) {
        setDocumentStatus(`Preparing ${result.filename} for direct PDF reasoning...`);
        let status = await getDocumentStatus(result.document_id);
        for (let attempt = 0; status.status === "processing" && attempt < 300; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          status = await getDocumentStatus(result.document_id);
        }
        if (status.status !== "queryable") {
          const details = Object.entries(status.pipelines)
            .filter(([, pipeline]) => pipeline.error)
            .map(([name, pipeline]) => `${name}: ${pipeline.error}`)
            .join("; ");
          throw new Error(
            details || "The PDF did not become available for direct VLM reasoning."
          );
        }
      }
      setSelectedDocumentIds((current) =>
        current.includes(result.document_id) ? current : [...current, result.document_id]
      );
      setDocumentStatus(`Attached ${result.filename}${isPdf ? " - direct PDF ready" : ""}.`);
    } catch (error) {
      setDocumentError(errorMessage(error));
      setDocumentStatus("");
    } finally {
      setDocumentUploading(false);
    }
  }

  async function syncVideoTitle(nextVideoId: string, title?: string) {
    if (title?.trim()) {
      setVideoTitle(title.trim());
      return;
    }
    try {
      const metadata = await getVideoMetadata(nextVideoId);
      setVideoTitle(metadata.title?.trim() || "Untitled video");
    } catch {
      setVideoTitle("Untitled video");
    }
  }

  async function handleUpload(file: File) {
    resetVideoContext();
    setIngesting(true);
    setIngestError("");
    setIngestStatus("Uploading local MP4...");
    const objectUrl = URL.createObjectURL(file);
    setVideoUrl(objectUrl);
    setOriginalVideoUrl(objectUrl);
    try {
      const result = await uploadMedia(file);
      setVideoId(result.video_id);
      await syncVideoTitle(result.video_id, result.title);
      setPendingVideoReadyStatus("Local video ready.");
      if ((videoRef.current?.readyState ?? 0) >= HTMLMediaElement.HAVE_METADATA) {
        setVideoMetadataLoaded(true);
        setPendingVideoReadyStatus("");
        setIngestStatus("Local video ready.");
      } else {
        setIngestStatus("Local video uploaded. Loading player metadata...");
      }
    } catch (error) {
      URL.revokeObjectURL(objectUrl);
      setVideoUrl("");
      setOriginalVideoUrl("");
      setIngestError(errorMessage(error));
      setIngestStatus("");
    } finally {
      setIngesting(false);
    }
  }

  async function handleYoutubeIngest(event: FormEvent) {
    event.preventDefault();
    const trimmedUrl = youtubeUrl.trim();
    if (!trimmedUrl) {
      setIngestError("Enter a YouTube URL to ingest.");
      return;
    }

    resetVideoContext();
    setIngesting(true);
    setIngestError("");
    setIngestStatus(
      "Downloading and preparing the YouTube video. Large downloads and first-run transcription can take several minutes..."
    );
    try {
      const result = await ingestYoutubeUrl(trimmedUrl);
      const sourceUrl = getMediaSourceUrl(result.video_id);
      setVideoId(result.video_id);
      await syncVideoTitle(result.video_id, result.title);
      setPendingVideoReadyStatus("YouTube video ready.");
      setVideoUrl(sourceUrl);
      setOriginalVideoUrl(sourceUrl);
      setIngestStatus("YouTube video downloaded. Loading player metadata...");
    } catch (error) {
      setIngestError(errorMessage(error));
      setIngestStatus("YouTube ingest failed. Review the message below and retry when ready.");
    } finally {
      setIngesting(false);
    }
  }

  function handleAsk(event: FormEvent) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    const videoReady = Boolean(videoId && videoMetadataLoaded);
    if (!videoReady || !trimmedQuestion) return;
    const request: QueuedChatMessage = {
      id: crypto.randomUUID(),
      text: trimmedQuestion,
      createdAt: new Date().toISOString(),
      model: selectedModel,
      timestamp: timestamp + videoTimeOffset,
      documentIds: [...selectedDocumentIds],
      documentNames: documents
        .filter((document) => selectedDocumentIds.includes(document.id))
        .map((document) => document.filename),
      annotations: annotations.map((annotation) => ({ ...annotation })),
      includeAnnotatedSnapshot: sendAnnotatedSnapshot,
      additionalNotes: additionalNotes.trim(),
    };
    setQuestion("");
    setChatClearStatus("");
    if (processingChatRef.current || loading) {
      replaceQueuedChatMessages((current) => [...current, request]);
      return;
    }
    void processChatMessage(request);
  }

  async function processChatMessage(request: QueuedChatMessage) {
    if (!videoId || !videoMetadataLoaded) return;
    const requestGeneration = contextGenerationRef.current;
    processingChatRef.current = true;
    setLoading(true);
    setStoppingResponse(false);
    const abortController = new AbortController();
    activeChatAbortRef.current = abortController;
    const trimmedQuestion = request.text.trim();
    const sourceTimestamp = request.timestamp;
    const userMessageId = crypto.randomUUID();
    const assistantMessageId = crypto.randomUUID();
    const createdAt = request.createdAt;
    setChatMessages((prev) => [
      ...prev,
      {
        id: userMessageId,
        role: "user",
        content: trimmedQuestion,
        createdAt,
        documents: request.documentNames,
        annotatedSnapshot: request.includeAnnotatedSnapshot,
      },
      {
        id: assistantMessageId,
        role: "assistant",
        content: "Thinking...",
        createdAt,
        model: request.model,
      },
    ]);

    try {
      const frameData = await captureFrame();
      const annotatedFrameData = request.includeAnnotatedSnapshot ? await captureAnnotatedFrame() : undefined;
      const transcript = await loadTranscriptWindow(videoId, sourceTimestamp);

      const response = await askQuestion({
        session_id: sessionId,
        video_id: videoId,
        video_title: videoTitle || undefined,
        timestamp: sourceTimestamp,
        frame_data_url: frameData,
        annotated_frame_data_url: annotatedFrameData,
        question: trimmedQuestion,
        annotations: request.annotations,
        transcript_window: transcript,
        document_ids: request.documentIds,
        model: request.model,
        additional_notes: request.additionalNotes,
      }, abortController.signal);
      if (!response.ok) {
        throw new Error(await response.text());
      }
      let rawAssistantText = "";
      await readSSE(response, {
        onDelta: (chunk) => {
          rawAssistantText += chunk;
          const partial = partialAnswerFromModelResponse(rawAssistantText);
          if (partial) {
            setChatMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId ? { ...message, content: partial } : message
              )
            );
          }
        },
        onError: (message) => {
          throw new Error(message);
        },
      });
      const parsed = parseModelResponse(rawAssistantText);
      if (abortController.signal.aborted || requestGeneration !== contextGenerationRef.current) {
        return;
      }
      setChatMessages((prev) =>
        prev.map((message) =>
          message.id === assistantMessageId
            ? {
                ...message,
                content: parsed.answer || rawAssistantText || "No answer returned.",
              }
            : message
        )
      );
      if (parsed.annotations.length) {
        // Render the model's normalized coordinates without a frontend offset.
        setModelAnnotations(
          parsed.annotations.map((a) => ({
            ...a,
            fontSize: a.fontSize ?? 1,
            strokeWidth: a.strokeWidth ?? 3,
          }))
        );
      } else {
        setModelAnnotations([]);
      }
      const explicitTrackingRequest = explicitlyRequestsTracking(trimmedQuestion);
      const modelSuggestsTracking =
        parsed.trackingTargets.length > 0 ||
        Boolean(parsed.trackingPrompt.trim()) ||
        parsed.trackingAnnotations.length > 0;
      const shouldStartTracking = explicitTrackingRequest || (trackingEnabled && modelSuggestsTracking);

      if (shouldStartTracking) {
        const video = videoRef.current;
        video?.pause();
        setTrackingError("");
        setTrackingStatus(
          explicitTrackingRequest
            ? "Preparing requested SAM3 tracking..."
            : "Preparing SAM3 target from VLM response..."
        );

        const trackingAnnotations = parsed.trackingAnnotations.length
          ? parsed.trackingAnnotations
          : modelSuggestsTracking
            ? parsed.annotations
            : [];

        const layerPrompt = parsed.trackingPrompt.trim() || trimmedQuestion;
        const baseTrackingTargets: TrackingTarget[] = parsed.trackingTargets.length
          ? parsed.trackingTargets
          : [
              {
                id: "target-1",
                label: shortTrackingLabel(layerPrompt),
                prompt: layerPrompt,
                annotations: trackingAnnotations,
              },
            ];
        const coloredTrackingTargets = colorizeTrackingTargets({
          targets: baseTrackingTargets,
          annotations: parsed.annotations,
          colorOffset:
            trackingLayersRef.current.length +
            activeTrackingJobsRef.current.reduce((count, job) => count + job.progress.length, 0),
        });
        const existingTrackingLabels = [
          ...trackingLayersRef.current.map((layer) => layer.label),
          ...activeTrackingJobsRef.current.flatMap((job) => job.progress.map((target) => target.label)),
        ];
        const { targets: trackingTargets, duplicates } = excludeTrackedTargets(
          coloredTrackingTargets,
          existingTrackingLabels
        );
        if (!trackingTargets.length) {
          const duplicateNames = duplicates.map((target) => target.label).join(", ");
          setTrackingStatus(
            `${duplicateNames || "That item"} ${duplicates.length === 1 ? "is" : "are"} already tracked.`
          );
          return;
        }
        if (duplicates.length) {
          setTrackingStatus(
            `Skipped already tracked ${duplicates.map((target) => target.label).join(", ")}; preparing new items.`
          );
        }
        const targetAnnotations = trackingTargets.flatMap((target) => target.annotations);
        const linkedMachineAnnotations = targetAnnotations.length
          ? targetAnnotations
          : parsed.annotations.map((annotation, index) => {
              const target = trackingTargets[Math.min(index, trackingTargets.length - 1)];
              return target
                ? {
                    ...annotation,
                    color: target.color ?? annotation.color,
                    tracking_target_id: target.id,
                  }
                : annotation;
            });
        setModelAnnotations(
          linkedMachineAnnotations.map((annotation) => ({
            ...annotation,
            fontSize: annotation.fontSize ?? 1,
            strokeWidth: annotation.strokeWidth ?? 3,
          }))
        );
        const initialProgress: TrackingTargetProgress[] = trackingTargets.map((target) => ({
          target_id: target.id,
          label: target.label,
          progress: 0,
          stage: "queued",
          color: target.color ?? "#6366f1",
        }));

        if (explicitTrackingRequest) {
          setShowTrackingOverlays(true);
        }

        try {
          const tracking = await startTracking({
            session_id: sessionId,
            video_id: videoId,
            timestamp: sourceTimestamp,
            frame_data_url: frameData,
            question: trimmedQuestion,
            segmentation_prompt: layerPrompt,
            annotations: trackingAnnotations,
            targets: trackingTargets,
          });
          const trackingJobId = tracking.tracking_job_id;
          replaceActiveTrackingJobs((current) => [
            ...current,
            {
              id: trackingJobId,
              progress: initialProgress,
              status: "SAM3: Queued (0%)",
              error: "",
              liveOverlays: [],
              cancelling: false,
            },
          ]);
          setTrackingStatus("");
          const events = new EventSource(
            `${process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8000"}/tracking/events/${trackingJobId}`
          );
          trackingEventSourcesRef.current.set(trackingJobId, events);
          let completionHandled = false;
          events.onmessage = async (e) => {
            const payload = JSON.parse(e.data) as {
              tracking_job_id?: string;
              done: boolean;
              cancelled?: boolean;
              progress?: number;
              stage?: TrackingTargetProgress["stage"];
              target_progress?: TrackingTargetProgress[];
              backend?: string;
              overlays: TrackingOverlay[];
              error?: { message?: string };
            };
            if (payload.tracking_job_id && payload.tracking_job_id !== trackingJobId) return;
            if (payload.error?.message) {
              replaceActiveTrackingJobs((current) =>
                current.map((job) =>
                  job.id === trackingJobId
                    ? {
                        ...job,
                        error: payload.error?.message ?? "Tracking failed.",
                        progress: job.progress.map((target) => ({ ...target, stage: "error" })),
                      }
                    : job
                )
              );
            }
            const stageLabel = payload.stage
              ? TRACKING_STAGE_LABELS[payload.stage]
              : payload.done
                ? "Complete"
                : "Tracking frames";
            const backendLabel =
              payload.backend === "sam3" || payload.backend === "pending"
                ? "SAM3"
                : payload.backend ?? "SAM3";
            const nextStatus = `${backendLabel}: ${stageLabel}${
              payload.progress !== undefined ? ` (${payload.progress}%)` : ""
            }`;
            replaceActiveTrackingJobs((current) =>
              current.map((job) =>
                job.id === trackingJobId
                  ? {
                      ...job,
                      status: nextStatus,
                      progress: Array.isArray(payload.target_progress)
                        ? payload.target_progress
                        : job.progress,
                      liveOverlays: payload.overlays,
                    }
                  : job
              )
            );
            if (payload.done) {
              if (completionHandled) return;
              completionHandled = true;
              if (payload.cancelled) {
                setTrackingStatus("Tracking cancelled.");
              } else if (!payload.error) {
                try {
                  const manifest = await getTrackingOverlays(trackingJobId);
                  const layerCount = new Set(
                    manifest.overlays.map((overlay) => overlay.target_id || overlay.track_id)
                  ).size;
                  if (!layerCount) {
                    setTrackingError("SAM3 completed but did not return any trackable masks.");
                  } else {
                    replaceTrackingLayers((currentLayers) => {
                      const nextRound = Math.max(0, ...currentLayers.map((layer) => layer.round)) + 1;
                      return [
                        ...currentLayers,
                        ...createTrackingLayers({
                          jobId: trackingJobId,
                          round: nextRound,
                          prompt: layerPrompt,
                          overlays: manifest.overlays,
                          colorOffset: currentLayers.length,
                        }),
                      ];
                    });
                    setTrackingStatus(
                      `Added ${layerCount} tracking item${layerCount === 1 ? "" : "s"}.`
                    );
                    setShowTrackingOverlays(true);
                  }
                } catch (error) {
                  const message = error instanceof Error ? error.message : "Unable to load tracking masks.";
                  setTrackingError(`Tracking finished, but its items could not be loaded: ${message}`);
                }
              } else {
                setTrackingError(payload.error.message ?? "Tracking failed.");
              }
              replaceActiveTrackingJobs((current) => current.filter((job) => job.id !== trackingJobId));
              events.close();
              trackingEventSourcesRef.current.delete(trackingJobId);
            }
          };
          events.onerror = () => {
            if (completionHandled) return;
            replaceActiveTrackingJobs((current) =>
              current.map((job) =>
                job.id === trackingJobId
                  ? { ...job, status: "Reconnecting to tracking progress..." }
                  : job
              )
            );
          };
        } catch (error) {
          const message = error instanceof Error ? error.message : "Unknown tracking start error.";
          setTrackingError(`Tracking failed to start: ${message}`);
          setTrackingStatus("");
        }
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setChatMessages((prev) => {
          const currentAssistant = prev.find((message) => message.id === assistantMessageId);
          if (!currentAssistant || currentAssistant.content === "Thinking...") {
            return prev.filter((message) => message.id !== assistantMessageId);
          }
          return prev.map((message) =>
            message.id === assistantMessageId ? { ...message, cancelled: true } : message
          );
        });
        return;
      }
      const message = error instanceof Error ? error.message : "Question failed";
      setChatMessages((prev) =>
        prev.map((chatMessage) =>
          chatMessage.id === assistantMessageId
            ? { ...chatMessage, content: message, error: true }
            : chatMessage
        )
      );
    } finally {
      if (activeChatAbortRef.current === abortController) {
        activeChatAbortRef.current = null;
      }
      processingChatRef.current = false;
      setStoppingResponse(false);
      setLoading(false);
      if (requestGeneration === contextGenerationRef.current) {
        const queue = queuedChatMessagesRef.current;
        const nextIndex = queue.findIndex((item) => item.text.trim());
        if (nextIndex >= 0) {
          const next = { ...queue[nextIndex], text: queue[nextIndex].text.trim() };
          replaceQueuedChatMessages((current) => current.slice(nextIndex + 1));
          window.setTimeout(() => void processChatMessage(next), 0);
        } else if (queue.length) {
          replaceQueuedChatMessages(() => []);
        }
      }
    }
  }

  async function runComparison(
    assistantMessageId: string,
    trimmedQuestion: string,
    includeAnnotatedSnapshot: boolean,
    retryOf?: string
  ) {
    setLoading(true);
    try {
      const videoReady = Boolean(videoId && videoMetadataLoaded);
      const sourceTimestamp = timestamp + videoTimeOffset;
      const frameData = videoReady ? await captureFrame() : undefined;
      const annotatedFrameData =
        videoReady && includeAnnotatedSnapshot ? await captureAnnotatedFrame() : undefined;
      const transcript = videoReady
        ? await loadTranscriptWindow(videoId, sourceTimestamp)
        : undefined;
      const response = await askComparison({
        session_id: sessionId,
        video_id: videoReady ? videoId : undefined,
        video_title: videoReady ? videoTitle || undefined : undefined,
        timestamp: videoReady ? sourceTimestamp : undefined,
        frame_data_url: frameData,
        annotated_frame_data_url: annotatedFrameData,
        question: trimmedQuestion,
        annotations,
        transcript_window: transcript,
        document_ids: selectedDocumentIds,
        model: selectedModel,
        retry_of: retryOf,
      });
      if (!response.ok) throw new Error(await response.text());
      await readComparisonSSE(response, (streamEvent) => {
        setChatMessages((current) =>
          current.map((message) =>
            message.id === assistantMessageId && message.comparison
              ? { ...message, comparison: reduceComparisonEvent(message.comparison, streamEvent) }
              : message
          )
        );
      });
    } catch (error) {
      const errorText = errorMessage(error);
      setChatMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId && message.comparison
            ? {
                ...message,
                comparison: {
                  ...message.comparison,
                  status: "error",
                  retryable: true,
                  answers: {
                    A:
                      message.comparison.answers.A.status === "complete"
                        ? message.comparison.answers.A
                        : { ...message.comparison.answers.A, status: "error", error: errorText },
                    B:
                      message.comparison.answers.B.status === "complete"
                        ? message.comparison.answers.B
                        : { ...message.comparison.answers.B, status: "error", error: errorText },
                  },
                },
              }
            : message
        )
      );
    } finally {
      setLoading(false);
    }
  }

  function selectComparisonAnswer(messageId: string, label: AnswerLabel) {
    setChatMessages((current) =>
      current.map((message) =>
        message.id === messageId && message.comparison && !message.comparison.revealed
          ? { ...message, comparison: { ...message.comparison, selected_label: label } }
          : message
      )
    );
  }

  async function revealComparisonAnswer(messageId: string) {
    const message = chatMessages.find((item) => item.id === messageId);
    const turn = message?.comparison;
    if (!turn?.comparison_id || !turn.selected_label || turn.revealed) return;
    setRevealingComparisonId(messageId);
    try {
      const result = await revealComparison(turn.comparison_id, turn.selected_label);
      const selectedAnswer = turn.answers[turn.selected_label];
      setChatMessages((current) =>
        current.map((item) =>
          item.id === messageId && item.comparison
            ? { ...item, comparison: revealTurn(item.comparison, result.mapping) }
            : item
        )
      );
      if (selectedAnswer.annotations.length) {
        setModelAnnotations(
          selectedAnswer.annotations.map((annotation) => ({
            ...annotation,
            fontSize: annotation.fontSize ?? 1,
            strokeWidth: annotation.strokeWidth ?? 3,
          }))
        );
      } else {
        setModelAnnotations([]);
      }
    } catch (error) {
      setChatMessages((current) =>
        current.map((item) =>
          item.id === messageId && item.comparison
            ? {
                ...item,
                comparison: { ...item.comparison, reveal_error: errorMessage(error) },
              }
            : item
        )
      );
    } finally {
      setRevealingComparisonId("");
    }
  }

  async function retryComparison(messageId: string) {
    const message = chatMessages.find((item) => item.id === messageId);
    if (!message?.comparisonQuestion || loading) return;
    const retryOf = message.comparison?.comparison_id;
    setChatMessages((current) =>
      current.map((item) =>
        item.id === messageId ? { ...item, comparison: createComparisonTurn() } : item
      )
    );
    await runComparison(messageId, message.comparisonQuestion, false, retryOf);
  }

  const selectedModelLabel =
    RAGVLM_MODELS.find((model) => model.value === selectedModel)?.label ?? selectedModel;
  const hasUnrevealedCompletedComparison = chatMessages.some(
    (message) =>
      message.comparison?.status === "complete" &&
      !message.comparison.revealed
  );
  const liveTrackingOverlays = activeTrackingJobs.flatMap((job) => job.liveOverlays);
  const visibleTrackingCount = trackingLayers.filter((layer) => layer.visible).length;
  const totalTrackingCount = trackingLayers.length;

  return (
    <div className="op-shell">
      <header className="op-header">
        <h1 className="op-logo">OperatorOS</h1>
        <div className="op-header-actions">
          <button
            type="button"
            className="op-theme-toggle"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
            title={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
          >
            <ThemeIcon theme={theme} />
          </button>
          <a className="op-header-link" href="http://localhost:8000/docs" target="_blank" rel="noreferrer">
            Docs
          </a>
          <a
            className="op-header-link"
            href="https://github.com/nontgcob/operator-os"
            target="_blank"
            rel="noreferrer"
          >
            GitHub
          </a>
        </div>
      </header>

      <div className="op-layout">
        <section>
          <div className="op-card">
            <div className="op-upload-row">
              <div>
                <span className="op-field-label">Local Source</span>
                <input
                  ref={localFileInputRef}
                  type="file"
                  accept="video/mp4"
                  hidden
                  disabled={ingesting}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.currentTarget.value = "";
                    if (file) void handleUpload(file);
                  }}
                />
                <button
                  type="button"
                  className="op-file-button"
                  disabled={ingesting}
                  onClick={() => localFileInputRef.current?.click()}
                >
                  Upload local MP4 file
                </button>
              </div>
              <div>
                <span className="op-field-label">Local Source</span>
                <input
                  id="youtube-url"
                  type="url"
                  className="op-text-input"
                  value={youtubeUrl}
                  disabled={ingesting}
                  placeholder="Paste a Youtube URL..."
                  onChange={(event) => setYoutubeUrl(event.target.value)}
                />
              </div>
              <form onSubmit={handleYoutubeIngest}>
                <span className="op-field-label" style={{ visibility: "hidden" }}>
                  Upload
                </span>
                <button
                  type="submit"
                  className="op-primary-button"
                  disabled={ingesting || !youtubeUrl.trim()}
                >
                  {ingesting ? "Uploading..." : "Upload Video"}
                </button>
              </form>
            </div>
            {ingestStatus && (
              <p role="status" className="op-status-text">
                {ingestStatus}
              </p>
            )}
            {ingestError && (
              <div role="alert" className="op-error-text">
                <p style={{ margin: 0 }}>{ingestError}</p>
                {shouldShowYoutubeCookieHelp(ingestError) && (
                  <p style={{ margin: "6px 0 0" }}>
                    For Docker, export YouTube browser cookies to <code>./data/ytdlp/cookies.txt</code>,
                    set <code>YTDLP_COOKIES_FILE=/app/data/ytdlp/cookies.txt</code>, then rebuild the
                    video service.
                  </p>
                )}
              </div>
            )}
          </div>

          <AnnotationControls
            activeTool={activeTool}
            annotationsCount={annotations.length}
            canUndo={annotationUndoStack.length > 0}
            drawColor={drawColor}
            isPaused={isPaused}
            textAnnotation={textAnnotation}
            onClear={clearAnnotations}
            onColorChange={setDrawColor}
            onToolChange={setActiveTool}
            onTextAnnotationChange={setTextAnnotation}
            onUndo={undoAnnotation}
          />

          <div className="op-video-shell">
            {videoTitle ? (
              <div className="op-video-title-bar">
                <span className="op-video-title-label">Now playing</span>
                <h2 className="op-video-title">{videoTitle}</h2>
              </div>
            ) : null}
            <div className="op-video-stage">
            {videoUrl ? (
              <video
                ref={videoRef}
                controls
                crossOrigin="anonymous"
                src={videoUrl}
                className="op-video-player"
                onLoadStart={() => {
                  setVideoMetadataLoaded(false);
                }}
                onLoadedMetadata={() => {
                  setVideoMetadataLoaded(true);
                  if (videoRef.current?.videoWidth && videoRef.current.videoHeight) {
                    setVideoAspectRatio(videoRef.current.videoWidth / videoRef.current.videoHeight);
                  }
                  setIngestError("");
                  if (pendingVideoReadyStatus) {
                    setIngestStatus(pendingVideoReadyStatus);
                    setPendingVideoReadyStatus("");
                  }
                }}
                onError={() => {
                  setVideoMetadataLoaded(false);
                  setPendingVideoReadyStatus("");
                  setIngestStatus("Video source failed to load in the player.");
                  setIngestError(
                    `Video player could not load the selected media. ${videoElementErrorMessage(videoRef.current)}`
                  );
                }}
                onPause={async () => {
                  const nextTs = videoRef.current?.currentTime ?? 0;
                  setTimestamp(nextTs);
                  setAnnotations([]);
                  setModelAnnotations([]);
                  setAnnotationUndoStack([]);
                  setIsPaused(true);
                  if (videoId) {
                    await loadTranscriptWindow(videoId, nextTs + videoTimeOffset);
                  }
                }}
                onPlay={() => {
                  setAnnotations([]);
                  setModelAnnotations([]);
                  setAnnotationUndoStack([]);
                  setIsPaused(false);
                }}
                onTimeUpdate={() => {
                  setTimestamp(videoRef.current?.currentTime ?? 0);
                }}
              />
            ) : (
              <div role="status" className="op-video-placeholder">
                Upload the video with the menu above and the video media player will appear here
              </div>
            )}
            {videoUrl && (
              <TrackingOverlayCanvas
                enabled={showTrackingOverlays}
                layers={trackingLayers}
                liveOverlays={liveTrackingOverlays}
                videoRef={videoRef}
                videoTimeOffset={videoTimeOffset}
              />
            )}
            {videoUrl && (
              <AnnotationOverlay
                activeTool={activeTool}
                annotations={annotations}
                modelAnnotations={modelAnnotations}
                drawColor={drawColor}
                isPaused={isPaused}
                textAnnotation={textAnnotation}
                videoAspectRatio={videoAspectRatio}
                onAnnotationsChange={setAnnotations}
                onPushUndo={(entry) => setAnnotationUndoStack((prev) => [...prev, entry])}
              />
            )}
            </div>
          </div>
        </section>

        <aside>
          <div className="op-card">
            <div className="op-sidebar-section-header">
              <h2 className="op-card-title" style={{ margin: 0 }}>
                Vision &amp; Tracking Engine
              </h2>
              <div className="op-inline-actions">
                <button
                  type="button"
                  className="op-secondary-button"
                  onClick={removeTracking}
                  disabled={!trackingLayers.length}
                >
                  Clear Items
                </button>
                <button
                  type="button"
                  className="op-secondary-button"
                  onClick={restoreFullVideo}
                  disabled={!originalVideoUrl || videoUrl === originalVideoUrl}
                >
                  Restore Full Video
                </button>
                <button
                  type="button"
                  className="op-secondary-button"
                  onClick={() => setShowTranscript((current) => !current)}
                >
                  {showTranscript ? "Hide Transcript" : "Show Transcript"}
                </button>
              </div>
            </div>
            <label className="op-checkbox-row">
              <input
                type="checkbox"
                checked={trackingEnabled}
                onChange={(event) => setTrackingEnabled(event.target.checked)}
              />
              <span>Automatically use SAM3 Tracking</span>
            </label>
            <label className="op-checkbox-row">
              <input
                type="checkbox"
                checked={showTrackingOverlays}
                onChange={(event) => setShowTrackingOverlays(event.target.checked)}
              />
              <span>Show tracking items</span>
            </label>
            <label className="op-checkbox-row">
              <input
                type="checkbox"
                checked={sendAnnotatedSnapshot}
                onChange={(event) => setSendAnnotatedSnapshot(event.target.checked)}
              />
              <span>Send Annotated Snapshot</span>
            </label>
            <div className="op-tracking-layer-panel">
              <div className="op-tracking-layer-heading">
                <strong>Tracking Items</strong>
                <div className="op-inline-actions">
                  <button
                    type="button"
                    className="op-layer-action"
                    disabled={!trackingLayers.length}
                    onClick={() => replaceTrackingLayers((layers) => layers.map((layer) => ({ ...layer, visible: true })))}
                  >
                    Show all
                  </button>
                  <button
                    type="button"
                    className="op-layer-action"
                    disabled={!trackingLayers.length}
                    onClick={() => replaceTrackingLayers((layers) => layers.map((layer) => ({ ...layer, visible: false })))}
                  >
                    Hide all
                  </button>
                  <button
                    type="button"
                    className="op-layer-action"
                    disabled={
                      trackingExporting ||
                      !videoId ||
                      !showTrackingOverlays ||
                      visibleTrackingCount === 0
                    }
                    onClick={() => void exportVisibleTrackingVideo()}
                    title="Download a video with the currently visible tracking items baked in"
                  >
                    {trackingExporting ? "Exporting..." : "Export visible"}
                  </button>
                </div>
              </div>
              {!trackingLayers.length ? (
                <p className="op-help-text">Completed SAM3 objects will appear here as removable items.</p>
              ) : (
                Array.from(new Set(trackingLayers.map((layer) => layer.round)))
                  .sort((a, b) => b - a)
                  .map((round) => (
                    <div className="op-tracking-round" key={`tracking-round-${round}`}>
                      <span className="op-tracking-round-label">Round {round}</span>
                      {trackingLayers
                        .filter((layer) => layer.round === round)
                        .map((layer) => (
                          <div className="op-tracking-layer-row" key={layer.id}>
                            <input
                              type="checkbox"
                              checked={layer.visible}
                              aria-label={`Show ${layer.label}`}
                              onChange={(event) =>
                                replaceTrackingLayers((layers) =>
                                  layers.map((candidate) =>
                                    candidate.id === layer.id
                                      ? { ...candidate, visible: event.target.checked }
                                      : candidate
                                  )
                                )
                              }
                            />
                            <input
                              type="color"
                              className="op-tracking-layer-color"
                              value={layer.color}
                              aria-label={`Color for ${layer.label}`}
                              onChange={(event) => {
                                const nextColor = event.target.value;
                                replaceTrackingLayers((layers) =>
                                  layers.map((candidate) =>
                                    candidate.id === layer.id
                                      ? {
                                          ...candidate,
                                          color: nextColor,
                                          overlays: candidate.overlays.map((overlay) => ({
                                            ...overlay,
                                            color: nextColor,
                                            target_color: nextColor,
                                          })),
                                        }
                                      : candidate
                                  )
                                );
                                if (layer.targetId) {
                                  setModelAnnotations((current) =>
                                    current.map((annotation) =>
                                      annotation.tracking_target_id === layer.targetId
                                        ? { ...annotation, color: nextColor }
                                        : annotation
                                    )
                                  );
                                }
                              }}
                            />
                            <span className="op-tracking-layer-name" title={layer.label}>{layer.label}</span>
                            <button
                              type="button"
                              className="op-tracking-layer-remove"
                              aria-label={`Remove ${layer.label}`}
                              title="Remove this tracking item"
                              onClick={() => {
                                const confirmed = window.confirm(
                                  `Remove the tracking mask for "${layer.label}"? You can track it again afterward.`
                                );
                                if (confirmed) {
                                  replaceTrackingLayers((layers) =>
                                    layers.filter((candidate) => candidate.id !== layer.id)
                                  );
                                }
                              }}
                            >
                              <span aria-hidden="true">×</span>
                            </button>
                          </div>
                        ))}
                    </div>
                  ))
              )}
              {totalTrackingCount > 0 && (
                <p className="op-tracking-count">
                  Showing {visibleTrackingCount} out of {totalTrackingCount} tracked item
                  {totalTrackingCount === 1 ? "" : "s"}.
                </p>
              )}
              {trackingExportStatus && (
                <p className="op-status-text" role="status">{trackingExportStatus}</p>
              )}
            </div>
            <p className="op-help-text">
              Frame at {formatTimestamp(timestamp)}. Default payload sends the original frame plus
              annotation JSON; the annotated snapshot is optional.
            </p>
            {activeTrackingJobs.map((job) => (
              <div
                className="op-tracking-progress-list"
                aria-label={`Tracking progress for ${job.progress.map((target) => target.label).join(", ")}`}
                key={job.id}
              >
                <div className="op-tracking-job-heading">
                  <span>{job.status}</span>
                  <button
                    type="button"
                    className="op-tracking-cancel"
                    disabled={job.cancelling}
                    onClick={() => void requestTrackingCancellation(job)}
                  >
                    {job.cancelling ? "Cancelling..." : "Cancel"}
                  </button>
                </div>
                {job.progress.map((target) => {
                  const progress = Math.max(0, Math.min(100, Math.round(target.progress)));
                  return (
                    <div className="op-tracking-progress-item" key={`${job.id}:${target.target_id}`}>
                      <div className="op-tracking-progress-heading">
                        <span title={target.label}>{target.label}</span>
                        <small>{TRACKING_STAGE_LABELS[target.stage]}</small>
                      </div>
                      <div className="op-tracking-progress-line">
                        <div
                          className="op-tracking-progress-track"
                          role="progressbar"
                          aria-label={`${target.label} tracking progress`}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={progress}
                        >
                          <span
                            style={{
                              width: `${progress}%`,
                              backgroundColor: target.color,
                            }}
                          />
                        </div>
                        <strong>{progress}%</strong>
                      </div>
                    </div>
                  );
                })}
                {job.error && (
                  <p role="alert" className="op-error-text op-tracking-job-error">
                    {job.error}
                  </p>
                )}
              </div>
            ))}
            {trackingStatus && (
              <p role="status" className="op-status-text">
                {trackingStatus}
              </p>
            )}
            {trackingError && (
              <p role="alert" className="op-error-text">
                {trackingError}
              </p>
            )}
            {showTranscript && (
              <div className="op-transcript-panel">
                {transcriptWindow?.source && (
                  <p
                    className={`op-transcript-badge ${
                      transcriptWindow.source === "whisper"
                        ? "op-transcript-badge-whisper"
                        : "op-transcript-badge-fallback"
                    }`}
                  >
                    Transcript source:{" "}
                    {transcriptWindow.source === "whisper"
                      ? `Whisper${transcriptWindow.model ? ` (${transcriptWindow.model})` : ""}`
                      : transcriptWindow.source === "fallback"
                        ? "fallback timestamps"
                        : "empty"}
                  </p>
                )}
                {transcriptError && (
                  <p role="alert" className="op-error-text" style={{ marginTop: 0 }}>
                    {transcriptError}
                  </p>
                )}
                <pre>
                  {transcriptWindow
                    ? transcriptWindow.segments
                        .map(
                          (segment) =>
                            `[${formatTimestamp(segment.start)}-${formatTimestamp(segment.end)}] ${segment.text}`
                        )
                        .join("\n")
                    : "No transcript loaded yet. Pause the video to load the current window."}
                </pre>
              </div>
            )}
          </div>

          <div className="op-card">
            <h2 className="op-card-title">Direct PDF Documents</h2>
            <input
              id="document-upload"
              type="file"
              accept=".pdf,application/pdf"
              hidden
              disabled={documentUploading}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.currentTarget.value = "";
                if (file) void handleDocumentUpload(file);
              }}
            />
            <label htmlFor="document-upload" className="op-attach-button">
              {documentUploading
                ? "Uploading document..."
                : "Attach PDF manuals or documents"}
            </label>
            {documentStatus && (
              <p role="status" className="op-status-text">
                {documentStatus}
              </p>
            )}
            {documentError && (
              <p role="alert" className="op-error-text">
                {documentError}
              </p>
            )}
            {documents.length ? (
              <div className="op-document-list">
                {documents.map((document) => (
                  <div key={document.id} className="op-document-item">
                    <label className="op-document-selector">
                      <input
                        type="checkbox"
                        checked={selectedDocumentIds.includes(document.id)}
                        onChange={() => toggleDocumentSelection(document.id)}
                      />
                      <span>
                        {document.filename}
                        <span className="op-document-meta">
                          {document.source === "preloaded" ? "Preloaded manual" : "Direct PDF"}
                        </span>
                      </span>
                    </label>
                  </div>
                ))}
              </div>
            ) : null}
            <p className="op-help-text">
              Direct PDF attached: {selectedDocumentIds.length ? `${selectedDocumentIds.length} document(s)` : "none"}
            </p>
          </div>

          <div className="op-card">
            <h2 className="op-card-title">Additional Notes</h2>
            <textarea
              id="additional-notes"
              className="op-chat-input op-additional-notes"
              value={additionalNotes}
              onChange={(event) => setAdditionalNotes(event.target.value)}
              rows={3}
              placeholder="i.e. machine name, specs, etc."
            />
            <p className="op-help-text">These notes are included with every new message.</p>
          </div>

          <div className="op-card">
            <h2 className="op-card-title">Vision Language Model (VLM)</h2>
            <select
              className="op-select"
              value={selectedModel}
              disabled={loading}
              onChange={(event) => setSelectedModel(event.target.value)}
            >
              {RAGVLM_MODELS.map((model) => (
                <option key={model.value} value={model.value}>
                  {model.label}
                </option>
              ))}
            </select>
            <p className="op-help-text">Active model: {selectedModelLabel}</p>
          </div>

          <div className="op-card">
            <div className="op-card-heading">
              <h2 className="op-card-title">Conversation</h2>
              <div className="op-inline-actions">
                <button
                  type="button"
                  className="op-secondary-button"
                  onClick={() => void clearAllContext()}
                  disabled={
                    !chatMessages.length &&
                    !queuedChatMessages.length &&
                    !annotations.length &&
                    !modelAnnotations.length &&
                    !trackingLayers.length &&
                    !activeTrackingJobs.length &&
                    !additionalNotes &&
                    !chatClearStatus
                  }
                  title="Clear conversation, annotations, tracking, notes, and queued work while keeping the video, transcript, PDFs, and PDF selections"
                >
                  Clear All Context
                </button>
                <button
                  type="button"
                  className="op-download-button"
                  onClick={downloadChat}
                  disabled={!chatMessages.length || loading}
                  title={
                    loading
                      ? "Wait for the current response to finish"
                      : chatMessages.length
                      ? "Download the entire conversation"
                      : "Start a conversation before downloading"
                  }
                >
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true">
                    <path
                      d="M12 3v12m0 0 4-4m-4 4-4-4M5 19h14"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  Download chat
                </button>
              </div>
            </div>
            {chatClearStatus && (
              <p role="status" className="op-status-text" style={{ marginTop: -4, marginBottom: 10 }}>
                {chatClearStatus}
              </p>
            )}
            <div className="op-chat-panel">
              <div className="op-chat-history">
                {chatMessages.length ? (
                  chatMessages.map((message) =>
                    message.comparison ? (
                      <ComparisonTurnCard
                        key={message.id}
                        turn={message.comparison}
                        revealing={revealingComparisonId === message.id}
                        onSelect={(label) => selectComparisonAnswer(message.id, label)}
                        onReveal={() => void revealComparisonAnswer(message.id)}
                        onRetry={() => void retryComparison(message.id)}
                      />
                    ) : (
                      <div
                        key={message.id}
                        className={`op-chat-bubble ${
                          message.role === "user"
                            ? "op-chat-bubble-user"
                            : message.error
                              ? "op-chat-bubble-error"
                              : "op-chat-bubble-assistant"
                        }`}
                      >
                        <div className="op-chat-meta">
                          {message.role === "user" ? "User" : "Operator OS"}
                          {message.model ? ` · ${message.model}` : ""}
                          {message.documents?.length ? ` · PDF: ${message.documents.join(", ")}` : ""}
                          {message.role === "user"
                            ? ` · snapshot ${message.annotatedSnapshot ? "sent" : "not sent"}`
                            : ""}
                        </div>
                        {message.cancelled && <div className="op-chat-stopped">Response stopped</div>}
                        {message.content}
                      </div>
                    )
                  )
                ) : (
                  <p className="op-chat-empty">
                    Ask about the paused frame or a selected document to start a conversation.
                  </p>
                )}
              </div>

              <form className="op-chat-form" onSubmit={handleAsk}>
                {queuedChatMessages.length > 0 && (
                  <div className="op-chat-queue" aria-label="Queued messages">
                    <div className="op-chat-queue-heading">
                      <strong>Message queue</strong>
                      <span>{queuedChatMessages.length}</span>
                    </div>
                    {queuedChatMessages.map((queuedMessage, index) => (
                      <div className="op-chat-queue-item" key={queuedMessage.id}>
                        <span className="op-chat-queue-number">{index + 1}</span>
                        <textarea
                          value={queuedMessage.text}
                          rows={2}
                          aria-label={`Edit queued message ${index + 1}`}
                          onChange={(event) => {
                            const text = event.target.value;
                            replaceQueuedChatMessages((current) =>
                              current.map((item) =>
                                item.id === queuedMessage.id ? { ...item, text } : item
                              )
                            );
                          }}
                        />
                        <button
                          type="button"
                          aria-label={`Remove queued message ${index + 1}`}
                          title="Remove queued message"
                          onClick={() =>
                            replaceQueuedChatMessages((current) =>
                              current.filter((item) => item.id !== queuedMessage.id)
                            )
                          }
                        >
                          <span aria-hidden="true">×</span>
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="op-chat-input-row">
                  <textarea
                    className="op-chat-input"
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        event.currentTarget.form?.requestSubmit();
                      }
                    }}
                    rows={2}
                    placeholder="Ask anything..."
                  />
                  {loading && activeChatAbortRef.current && (
                    <button
                      type="button"
                      className="op-stop-button"
                      onClick={stopActiveResponse}
                      disabled={stoppingResponse}
                    >
                      {stoppingResponse ? "Stopping..." : "Stop"}
                    </button>
                  )}
                  <button
                    type="submit"
                    className="op-send-button"
                    aria-label={loading ? "Queue message" : "Send question"}
                    disabled={
                      ingesting ||
                      hasUnrevealedCompletedComparison ||
                      (!videoId || !videoMetadataLoaded)
                    }
                  >
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true">
                      <path
                        d="M4 12 L20 4 L14 20 L12 13 Z"
                        stroke="currentColor"
                        strokeWidth="1.8"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                </div>
              </form>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
