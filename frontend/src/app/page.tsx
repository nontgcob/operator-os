"use client";

import { useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AnnotationControls } from "@/components/AnnotationControls";
import { AnnotationOverlay } from "@/components/AnnotationOverlay";
import { parseModelResponse } from "@/lib/parseResponse";
import { explicitlyRequestsTracking } from "@/lib/trackingIntent";
import {
  askQuestion,
  getMediaSourceUrl,
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
  TrackingOverlay,
  TranscriptWindowResponse,
} from "@/lib/types";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  error?: boolean;
  model?: string;
  documents?: string[];
  annotatedSnapshot?: boolean;
}

interface UploadedDocument {
  id: string;
  filename: string;
  chunkCount: number;
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

const DEFAULT_RAGVLM_MODEL = "qwen/qwen3-vl-8b-instruct";

function formatTimestamp(seconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}

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

// Shift annotations down by 1/8 of the video height (1000 * 1/8 = 125)
function clampRagvlmLocal(value: number) {
  return Math.min(1000, Math.max(0, value));
}

function translatePathD(d: string | undefined, dx: number, dy: number) {
  if (!d) return d;
  let isX = true;
  return d.replace(/([-+]?\d*\.?\d+)/g, (match) => {
    const num = parseFloat(match);
    const next = isX ? num + dx : num + dy;
    isX = !isX;
    return String(next);
  });
}

function shiftAnnotationDown(annotation: Annotation, dy = 125): Annotation {
  switch (annotation.type) {
    case "rect":
    case "text":
    case "number":
      return { ...annotation, y: annotation.y !== undefined ? clampRagvlmLocal(annotation.y + dy) : annotation.y };
    case "circle":
      return {
        ...annotation,
        cy: annotation.cy !== undefined ? clampRagvlmLocal(annotation.cy + dy) : annotation.cy,
        y: annotation.y !== undefined ? clampRagvlmLocal(annotation.y + dy) : annotation.y,
      };
    case "arrow":
      return {
        ...annotation,
        y1: annotation.y1 !== undefined ? clampRagvlmLocal(annotation.y1 + dy) : annotation.y1,
        y2: annotation.y2 !== undefined ? clampRagvlmLocal(annotation.y2 + dy) : annotation.y2,
      };
    case "path":
      return {
        ...annotation,
        d: translatePathD(annotation.d, 0, dy),
        points: (annotation.points ?? []).map((p) =>
          Array.isArray(p) ? [p[0], clampRagvlmLocal((p[1] as number) + dy)] : { x: p.x, y: clampRagvlmLocal(p.y + dy) }
        ),
      };
    case "polygon":
      return {
        ...annotation,
        points: (annotation.points ?? []).map((p) => (Array.isArray(p) ? [p[0], clampRagvlmLocal((p[1] as number) + dy)] : [p.x, clampRagvlmLocal(p.y + dy)])),
      } as Annotation;
    default:
      return annotation;
  }
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
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [videoUrl, setVideoUrl] = useState<string>("");
  const [videoId, setVideoId] = useState<string>("");
  const [videoTitle, setVideoTitle] = useState<string>("");
  const [sessionId] = useState(() => crypto.randomUUID());
  const [question, setQuestion] = useState("");
  const [selectedModel, setSelectedModel] = useState(DEFAULT_RAGVLM_MODEL);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [documents, setDocuments] = useState<UploadedDocument[]>([]);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [documentUploading, setDocumentUploading] = useState(false);
  const [documentStatus, setDocumentStatus] = useState("");
  const [documentError, setDocumentError] = useState("");
  const [loading, setLoading] = useState(false);
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
  const [trackingOverlays, setTrackingOverlays] = useState<TrackingOverlay[]>([]);
  const [trackingEnabled, setTrackingEnabled] = useState(false);
  const [showTrackingOverlays, setShowTrackingOverlays] = useState(false);
  const [activeTrackingJobId, setActiveTrackingJobId] = useState("");
  const [trackingStatus, setTrackingStatus] = useState("");
  const [trackingError, setTrackingError] = useState("");
  const [sendAnnotatedSnapshot, setSendAnnotatedSnapshot] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [activeTool, setActiveTool] = useState<AnnotationType>("cursor");
  const [annotationUndoStack, setAnnotationUndoStack] = useState<AnnotationUndoEntry[]>([]);
  const [drawColor, setDrawColor] = useState("#ef4444");
  const [strokeWidth, setStrokeWidth] = useState(3);
  const [textAnnotation, setTextAnnotation] = useState("");
  const [showTranscript, setShowTranscript] = useState(false);
  const localFileInputRef = useRef<HTMLInputElement | null>(null);
  const trackingEventSourceRef = useRef<EventSource | null>(null);
  const resumeAfterTrackingRef = useRef(false);

  const currentOverlay = useMemo(() => {
    if (!trackingOverlays.length) return [];
    const nearestTimestamp = trackingOverlays.reduce((nearest, overlay) =>
      Math.abs(overlay.timestamp - timestamp) < Math.abs(nearest - timestamp)
        ? overlay.timestamp
        : nearest
    , trackingOverlays[0].timestamp);
    if (Math.abs(nearestTimestamp - timestamp) > 0.1) return [];
    return trackingOverlays.filter((overlay) => Math.abs(overlay.timestamp - nearestTimestamp) < 0.0001);
  }, [trackingOverlays, timestamp]);

  function closeTrackingEventSource() {
    const source = trackingEventSourceRef.current as EventSource | null;
    if (source) {
      source.close();
    }
    trackingEventSourceRef.current = null;
  }

  function resetVideoContext() {
    closeTrackingEventSource();
    setVideoUrl("");
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
    setTrackingOverlays([]);
    setActiveTrackingJobId("");
    setTrackingStatus("");
    setTrackingError("");
    setChatMessages([]);
  }

  function clearAnnotations() {
    setAnnotations([]);
    setAnnotationUndoStack([]);
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
          chunkCount: result.chunk_count,
        };
        return [...current.filter((document) => document.id !== result.document_id), nextDocument];
      });
      setSelectedDocumentIds((current) =>
        current.includes(result.document_id) ? current : [...current, result.document_id]
      );
      setDocumentStatus(`Attached ${result.filename} (${result.chunk_count} chunks).`);
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
      setVideoId(result.video_id);
      await syncVideoTitle(result.video_id, result.title);
      setPendingVideoReadyStatus("YouTube video ready.");
      setVideoUrl(getMediaSourceUrl(result.video_id));
      setIngestStatus("YouTube video downloaded. Loading player metadata...");
    } catch (error) {
      setIngestError(errorMessage(error));
      setIngestStatus("YouTube ingest failed. Review the message below and retry when ready.");
    } finally {
      setIngesting(false);
    }
  }

  async function handleAsk(event: FormEvent) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!videoId || !videoMetadataLoaded || !trimmedQuestion) return;
    const sourceTimestamp = timestamp + videoTimeOffset;
    setLoading(true);
    const attachedDocuments = documents
      .filter((document) => selectedDocumentIds.includes(document.id))
      .map((document) => document.filename);
    const includeAnnotatedSnapshot = sendAnnotatedSnapshot;
    const userMessageId = crypto.randomUUID();
    const assistantMessageId = crypto.randomUUID();
    setChatMessages((prev) => [
      ...prev,
      {
        id: userMessageId,
        role: "user",
        content: trimmedQuestion,
        documents: attachedDocuments,
        annotatedSnapshot: includeAnnotatedSnapshot,
      },
      { id: assistantMessageId, role: "assistant", content: "Thinking...", model: selectedModel },
    ]);
    setQuestion("");
    closeTrackingEventSource();
    setTrackingOverlays([]);
    setActiveTrackingJobId("");
    setTrackingStatus("");
    setTrackingError("");

    try {
      const frameData = await captureFrame();
      const annotatedFrameData = includeAnnotatedSnapshot ? await captureAnnotatedFrame() : undefined;
      const transcript = await loadTranscriptWindow(videoId, sourceTimestamp);

      const response = await askQuestion({
        session_id: sessionId,
        video_id: videoId,
        video_title: videoTitle || undefined,
        timestamp: sourceTimestamp,
        frame_data_url: frameData,
        annotated_frame_data_url: annotatedFrameData,
        question: trimmedQuestion,
        annotations,
        transcript_window: transcript,
        document_ids: selectedDocumentIds,
        model: selectedModel,
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      let rawAssistantText = "";
      await readSSE(response, {
        onDelta: (chunk) => {
          rawAssistantText += chunk;
        },
        onError: (message) => {
          throw new Error(message);
        },
      });
      const parsed = parseModelResponse(rawAssistantText);
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
        // Shift machine/model-generated annotations down by 1/32 of the video
        // height (in ragvlm_0_1000 coords that's 1000 * 1/32 ≈ 31.25 → 31).
        const SHIFT_Y = Math.round(1000 / 32);
        const clampRagvlm = (v: number) => Math.min(1000, Math.max(0, Math.round(v)));

        function translateD(d: string | undefined, dy: number) {
          if (!d) return d;
          let isX = true;
          return d.replace(/([-+]?\d*\.?\d+)/g, (match) => {
            const n = parseFloat(match);
            if (Number.isNaN(n)) return match;
            if (isX) {
              isX = false;
              return String(n);
            }
            isX = true;
            return String(clampRagvlm(n + dy));
          });
        }

        function shiftAnnotation(a: Annotation): Annotation {
          switch (a.type) {
            case "rect":
            case "text":
            case "number":
              return { ...a, y: a.y !== undefined ? clampRagvlm(a.y + SHIFT_Y) : a.y };
            case "circle":
              return { ...a, cy: a.cy !== undefined ? clampRagvlm(a.cy + SHIFT_Y) : (a.y !== undefined ? clampRagvlm(a.y + SHIFT_Y) : a.cy) };
            case "arrow":
              return {
                ...a,
                y1: a.y1 !== undefined ? clampRagvlm(a.y1 + SHIFT_Y) : a.y1,
                y2: a.y2 !== undefined ? clampRagvlm(a.y2 + SHIFT_Y) : a.y2,
              };
            case "path":
              return {
                ...a,
                d: translateD(a.d, SHIFT_Y),
                points: Array.isArray(a.points)
                  ? a.points.map((p) => (Array.isArray(p) ? [p[0], clampRagvlm(p[1] + SHIFT_Y)] : { x: p.x, y: clampRagvlm(p.y + SHIFT_Y) }))
                  : a.points,
              };
            case "polygon":
              return {
                ...a,
                points: Array.isArray(a.points)
                  ? a.points.map((p) => (Array.isArray(p) ? [p[0], clampRagvlm(p[1] + SHIFT_Y)] : p))
                  : a.points,
              };
            default:
              return a;
          }
        }

        setModelAnnotations(
          parsed.annotations.map((a) => ({
            ...shiftAnnotation(a),
            fontSize: a.fontSize ?? 1,
            strokeWidth: a.strokeWidth ?? 3,
          }))
        );
      } else {
        setModelAnnotations([]);
      }
      const explicitTrackingRequest = explicitlyRequestsTracking(trimmedQuestion);
      const modelSuggestsTracking =
        Boolean(parsed.trackingPrompt.trim()) || parsed.trackingAnnotations.length > 0;
      const shouldStartTracking = explicitTrackingRequest || trackingEnabled || modelSuggestsTracking;

      if (shouldStartTracking) {
        const video = videoRef.current;
        resumeAfterTrackingRef.current = Boolean(video && !video.paused);
        video?.pause();
        closeTrackingEventSource();
        setTrackingOverlays([]);
        setActiveTrackingJobId("");
        setTrackingError("");
        setTrackingStatus(
          explicitTrackingRequest
            ? "Preparing requested SAM3 tracking..."
            : "Preparing SAM3 target from VLM response..."
        );

        const trackingAnnotations = parsed.trackingAnnotations.length
          ? parsed.trackingAnnotations
          : trackingEnabled && !explicitTrackingRequest
            ? parsed.annotations
            : [];

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
            segmentation_prompt:
              parsed.trackingPrompt.trim() || trimmedQuestion,
            annotations: trackingAnnotations,
          });
          const trackingJobId = tracking.tracking_job_id;
          setActiveTrackingJobId(trackingJobId);
          setTrackingStatus(`Tracking job started: ${trackingJobId}`);
          const events = new EventSource(
            `${process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8000"}/tracking/events/${trackingJobId}`
          );
          trackingEventSourceRef.current = events;
          events.onmessage = (e) => {
            const payload = JSON.parse(e.data) as {
              tracking_job_id?: string;
              done: boolean;
              progress?: number;
              backend?: string;
              overlays: TrackingOverlay[];
              rendered_video_path?: string;
              error?: { message?: string };
            };
            if (payload.tracking_job_id && payload.tracking_job_id !== trackingJobId) return;
            if (payload.error?.message) {
              setTrackingError(payload.error.message);
              resumeAfterTrackingRef.current = false;
            }
            setTrackingStatus(
              `${payload.backend ?? "SAM3"} tracking ${payload.done ? "complete" : "running"}${
                payload.progress !== undefined ? ` (${payload.progress}%)` : ""
              }`
            );
            setTrackingOverlays(payload.overlays);
            if (payload.done) {
              if (!payload.error && payload.rendered_video_path) {
                const baseUrl = process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8000";
                setTrackingOverlays([]);
                setShowTrackingOverlays(false);
                setVideoTimeOffset(sourceTimestamp);
                setTimestamp(0);
                setVideoUrl(`${baseUrl}/tracking/video/${trackingJobId}?v=${Date.now()}`);
                setTrackingStatus("SAM3 processed video ready. Press play to view tracking.");
              }
              resumeAfterTrackingRef.current = false;
              events.close();
              if (trackingEventSourceRef.current === events) {
                trackingEventSourceRef.current = null;
              }
            }
          };
          events.onerror = () => {
            setTrackingError("Tracking event stream failed.");
            resumeAfterTrackingRef.current = false;
            events.close();
            if (trackingEventSourceRef.current === events) {
              trackingEventSourceRef.current = null;
            }
          };
        } catch (error) {
          resumeAfterTrackingRef.current = false;
          const message = error instanceof Error ? error.message : "Unknown tracking start error.";
          setTrackingError(`Tracking failed to start: ${message}`);
          setTrackingStatus("");
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Question failed";
      setChatMessages((prev) =>
        prev.map((chatMessage) =>
          chatMessage.id === assistantMessageId
            ? { ...chatMessage, content: message, error: true }
            : chatMessage
        )
      );
    } finally {
      setLoading(false);
    }
  }

  const selectedModelLabel =
    RAGVLM_MODELS.find((model) => model.value === selectedModel)?.label ?? selectedModel;

  return (
    <div className="op-shell">
      <header className="op-header">
        <h1 className="op-logo">OperatorOS</h1>
        <div className="op-header-actions">
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
            strokeWidth={strokeWidth}
            textAnnotation={textAnnotation}
            onClear={clearAnnotations}
            onColorChange={setDrawColor}
            onStrokeWidthChange={setStrokeWidth}
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
                    await loadTranscriptWindow(videoId, nextTs);
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
              <AnnotationOverlay
                activeTool={activeTool}
                annotations={annotations}
                modelAnnotations={modelAnnotations}
                trackingOverlays={showTrackingOverlays ? currentOverlay : []}
                drawColor={drawColor}
                isPaused={isPaused}
                strokeWidth={strokeWidth}
                textAnnotation={textAnnotation}
                videoAspectRatio={videoAspectRatio}
                onAnnotationsChange={setAnnotations}
                onPushUndo={(entry) => setAnnotationUndoStack((prev) => [...prev, entry])}
              />
            )}
          </div>
        </section>

        <aside>
          <div className="op-card">
            <div className="op-sidebar-section-header">
              <h2 className="op-card-title" style={{ margin: 0 }}>
                Vision &amp; Tracking Engine
              </h2>
              <button
                type="button"
                className="op-secondary-button"
                onClick={() => setShowTranscript((current) => !current)}
              >
                {showTranscript ? "Hide Transcript" : "Show Transcript"}
              </button>
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
              <span>Enable SAM3 Overlay</span>
            </label>
            <label className="op-checkbox-row">
              <input
                type="checkbox"
                checked={sendAnnotatedSnapshot}
                onChange={(event) => setSendAnnotatedSnapshot(event.target.checked)}
              />
              <span>Send Annotated Snapshot</span>
            </label>
            <p className="op-help-text">
              Frame at {formatTimestamp(timestamp)}. Default payload sends the original frame plus
              annotation JSON; the annotated snapshot is optional.
            </p>
            {activeTrackingJobId && (
              <p className="op-help-text">Active tracking job: {activeTrackingJobId}</p>
            )}
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
            <h2 className="op-card-title">Contextual RAG Documents</h2>
            <input
              id="document-upload"
              type="file"
              accept=".txt,.md,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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
                : "Attach user manuals or documents (.pdf, .md)"}
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
                  <label key={document.id} className="op-document-item">
                    <input
                      type="checkbox"
                      checked={selectedDocumentIds.includes(document.id)}
                      onChange={() => toggleDocumentSelection(document.id)}
                    />
                    <span>
                      {document.filename}
                      <div className="op-document-meta">{document.chunkCount} chunks</div>
                    </span>
                  </label>
                ))}
              </div>
            ) : null}
            <p className="op-help-text">
              RAG attached: {selectedDocumentIds.length ? `${selectedDocumentIds.length} document(s)` : "none"}
            </p>
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
            <h2 className="op-card-title">Conversation</h2>
            <div className="op-chat-panel">
              <div className="op-chat-history">
                {chatMessages.length ? (
                  chatMessages.map((message) => (
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
                        {message.documents?.length ? ` · RAG: ${message.documents.join(", ")}` : ""}
                        {message.role === "user"
                          ? ` · snapshot ${message.annotatedSnapshot ? "sent" : "not sent"}`
                          : ""}
                      </div>
                      {message.content}
                    </div>
                  ))
                ) : (
                  <p className="op-chat-empty">Ask anything about the paused frame to start a conversation.</p>
                )}
              </div>

              <form className="op-chat-form" onSubmit={handleAsk}>
                <div className="op-chat-input-row">
                  <textarea
                    className="op-chat-input"
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    rows={2}
                    placeholder="Ask anything..."
                  />
                  <button
                    type="submit"
                    className="op-send-button"
                    aria-label={loading ? "Sending question" : "Send question"}
                    disabled={loading || ingesting || !videoId || !videoMetadataLoaded}
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
