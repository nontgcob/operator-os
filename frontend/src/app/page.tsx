"use client";

import { useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AnnotationControls } from "@/components/AnnotationControls";
import { AnnotationOverlay } from "@/components/AnnotationOverlay";
import { parseModelResponse } from "@/lib/parseResponse";
import {
  askQuestion,
  getMediaSourceUrl,
  getTranscriptWindow,
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
  const [transcriptWindow, setTranscriptWindow] = useState<TranscriptWindowResponse | null>(null);
  const [transcriptError, setTranscriptError] = useState("");
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [trackingOverlays, setTrackingOverlays] = useState<TrackingOverlay[]>([]);
  const [trackingEnabled, setTrackingEnabled] = useState(true);
  const [showTrackingOverlays, setShowTrackingOverlays] = useState(true);
  const [sendAnnotatedSnapshot, setSendAnnotatedSnapshot] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [activeTool, setActiveTool] = useState<AnnotationType>("cursor");
  const [annotationUndoStack, setAnnotationUndoStack] = useState<AnnotationUndoEntry[]>([]);
  const [drawColor, setDrawColor] = useState("#ff6b6b");
  const [strokeWidth, setStrokeWidth] = useState(3);
  const [textAnnotation, setTextAnnotation] = useState("");

  const currentOverlay = useMemo(() => {
    return trackingOverlays.filter(
      (overlay) => Math.abs(overlay.timestamp - timestamp) < 0.25
    );
  }, [trackingOverlays, timestamp]);

  function resetVideoContext() {
    setVideoUrl("");
    setVideoId("");
    setVideoMetadataLoaded(false);
    setVideoAspectRatio(1);
    setPendingVideoReadyStatus("");
    setTimestamp(0);
    setTranscriptWindow(null);
    setTranscriptError("");
    setAnnotations([]);
    setAnnotationUndoStack([]);
    setTrackingOverlays([]);
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

    try {
      const frameData = await captureFrame();
      const annotatedFrameData = includeAnnotatedSnapshot ? await captureAnnotatedFrame() : undefined;
      const transcript = await loadTranscriptWindow(videoId, timestamp);
      const trackingPromise =
        trackingEnabled
          ? startTracking({
              session_id: sessionId,
              video_id: videoId,
              timestamp,
              frame_data_url: frameData,
              question: trimmedQuestion,
              annotations,
            }).catch(() => null)
          : Promise.resolve(null);

      const response = await askQuestion({
        session_id: sessionId,
        video_id: videoId,
        timestamp,
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
        setAnnotations((prev) => [...prev, ...parsed.annotations]);
        setAnnotationUndoStack((prev) => [...prev, { op: "pop", count: parsed.annotations.length }]);
      }
      if (trackingEnabled) {
        try {
          const tracking = await trackingPromise;
          if (!tracking) return;
          const events = new EventSource(
            `${process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8000"}/tracking/events/${tracking.tracking_job_id}`
          );
          events.onmessage = (e) => {
            const payload = JSON.parse(e.data) as {
              done: boolean;
              overlays: TrackingOverlay[];
            };
            setTrackingOverlays(payload.overlays);
            if (payload.done) {
              events.close();
            }
          };
        } catch {
          // Tracking is secondary; the answer should remain visible if tracking fails.
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

  return (
    <main style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16, padding: 16 }}>
      <section style={{ background: "#161b22", borderRadius: 8, padding: 12 }}>
        <h2>OperatorOS Video Player</h2>
        <div style={{ display: "grid", gap: 12 }}>
          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(180px, 1fr) 2fr" }}>
            <label style={{ display: "grid", gap: 6 }}>
              Upload local MP4
              <input
                type="file"
                accept="video/mp4"
                disabled={ingesting}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void handleUpload(file);
                }}
              />
            </label>
            <form onSubmit={handleYoutubeIngest} style={{ display: "grid", gap: 6 }}>
              <label htmlFor="youtube-url">YouTube URL</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  id="youtube-url"
                  type="url"
                  value={youtubeUrl}
                  disabled={ingesting}
                  placeholder="https://www.youtube.com/watch?v=..."
                  onChange={(e) => setYoutubeUrl(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button type="submit" disabled={ingesting || !youtubeUrl.trim()}>
                  {ingesting ? "Ingesting..." : "Ingest URL"}
                </button>
              </div>
            </form>
          </div>
          {ingestStatus && (
            <p role="status" style={{ margin: 0, color: "#9ecbff" }}>
              {ingestStatus}
            </p>
          )}
          {ingestError && (
            <div role="alert" style={{ margin: 0, color: "#ff7b72" }}>
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
        <div style={{ position: "relative", marginTop: 12 }}>
          {videoUrl ? (
            <video
              ref={videoRef}
              controls
              crossOrigin="anonymous"
              src={videoUrl}
              style={{ width: "100%", borderRadius: 8 }}
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
                setAnnotationUndoStack([]);
                setIsPaused(true);
                if (videoId) {
                  await loadTranscriptWindow(videoId, nextTs);
                }
              }}
              onPlay={() => {
                setAnnotations([]);
                setAnnotationUndoStack([]);
                setIsPaused(false);
              }}
              onTimeUpdate={() => {
                setTimestamp(videoRef.current?.currentTime ?? 0);
              }}
            />
          ) : (
            <div
              role="status"
              style={{
                display: "grid",
                minHeight: 260,
                placeItems: "center",
                border: "1px dashed #30363d",
                borderRadius: 8,
                color: "#8b949e",
              }}
            >
              Upload an MP4 or ingest a YouTube URL to load a video.
            </div>
          )}
          {videoUrl && showTrackingOverlays && (
            <svg
              aria-hidden="true"
              style={{
                position: "absolute",
                inset: 0,
                pointerEvents: "none",
                width: "100%",
                height: "100%",
              }}
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
            >
              {currentOverlay.map((overlay) => (
                <polygon
                  key={`${overlay.track_id}-${overlay.timestamp}`}
                  points={overlay.points.map((p) => `${p.x},${p.y}`).join(" ")}
                  fill="none"
                  stroke={overlay.color}
                  strokeWidth={0.7}
                  strokeDasharray="2 1.5"
                  opacity={0.85}
                />
              ))}
            </svg>
          )}
          {videoUrl && (
            <AnnotationOverlay
              activeTool={activeTool}
              annotations={annotations}
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

      <section style={{ background: "#161b22", borderRadius: 8, padding: 12 }}>
        <h2>Contextual Chat</h2>
        <p>Timestamp: {formatTimestamp(timestamp)}</p>
        <p>
          Paused annotations: {annotations.length} (
          {isPaused ? `${activeTool} tool active` : "pause video to add"})
        </p>
        <button type="button" disabled={!annotations.length} onClick={clearAnnotations}>
          Clear annotations
        </button>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={trackingEnabled}
            onChange={(e) => setTrackingEnabled(e.target.checked)}
          />
          Enable SAM3 tracking
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={showTrackingOverlays}
            onChange={(e) => setShowTrackingOverlays(e.target.checked)}
          />
          Show tracking overlays
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={sendAnnotatedSnapshot}
            onChange={(e) => setSendAnnotatedSnapshot(e.target.checked)}
          />
          Also send annotated snapshot
        </label>
        <p style={{ color: "#8b949e", margin: "4px 0 0" }}>
          Default payload: original frame + annotation JSON. When checked, OperatorOS also sends a second
          frame image with your annotations drawn on top.
        </p>
        <section
          style={{
            border: "1px solid #30363d",
            borderRadius: 8,
            display: "grid",
            gap: 8,
            marginTop: 12,
            padding: 10,
          }}
        >
          <strong>Documents / Manuals</strong>
          <label style={{ display: "grid", gap: 6 }}>
            Upload for RAG
            <input
              type="file"
              accept=".txt,.md,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              disabled={documentUploading}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.currentTarget.value = "";
                if (file) void handleDocumentUpload(file);
              }}
            />
          </label>
          {documentStatus && (
            <p role="status" style={{ color: "#9ecbff", margin: 0 }}>
              {documentStatus}
            </p>
          )}
          {documentError && (
            <p role="alert" style={{ color: "#ff7b72", margin: 0 }}>
              {documentError}
            </p>
          )}
          {documents.length ? (
            <div style={{ display: "grid", gap: 6 }}>
              {documents.map((document) => (
                <label
                  key={document.id}
                  style={{
                    alignItems: "flex-start",
                    display: "flex",
                    gap: 6,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedDocumentIds.includes(document.id)}
                    onChange={() => toggleDocumentSelection(document.id)}
                  />
                  <span>
                    {document.filename}
                    <span style={{ color: "#8b949e" }}> ({document.chunkCount} chunks)</span>
                  </span>
                </label>
              ))}
            </div>
          ) : (
            <p style={{ color: "#8b949e", margin: 0 }}>
              Upload a PDF, DOCX, markdown, or text file to ground answers in manuals.
            </p>
          )}
          <p style={{ color: "#8b949e", margin: 0 }}>
            RAG attached: {selectedDocumentIds.length ? `${selectedDocumentIds.length} document(s)` : "none"}
          </p>
        </section>
        <form onSubmit={handleAsk} style={{ display: "grid", gap: 8, marginTop: 12 }}>
          <label style={{ display: "grid", gap: 6 }}>
            Model
            <select
              value={selectedModel}
              disabled={loading}
              onChange={(event) => setSelectedModel(event.target.value)}
            >
              {RAGVLM_MODELS.map((model) => (
                <option key={model.value} value={model.value}>
                  {model.family}: {model.label}
                </option>
              ))}
            </select>
          </label>
          <p style={{ color: "#8b949e", margin: 0 }}>
            Active model: <code>{selectedModel}</code>
          </p>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={4}
            placeholder="Ask about the current frame..."
          />
          <button type="submit" disabled={loading || ingesting || !videoId || !videoMetadataLoaded}>
            {loading ? "Reasoning..." : "Ask"}
          </button>
        </form>
        <h3>Chat</h3>
        <div style={{ display: "grid", gap: 8 }}>
          {chatMessages.length ? (
            chatMessages.map((message) => (
              <div
                key={message.id}
                style={{
                  background:
                    message.role === "user"
                      ? "#0d1117"
                      : message.error
                        ? "rgba(248, 81, 73, 0.12)"
                        : "#111827",
                  border: `1px solid ${message.error ? "#7f1d1d" : "#30363d"}`,
                  borderRadius: 8,
                  color: message.error ? "#ffb4ad" : "#f0f6fc",
                  padding: 10,
                  whiteSpace: "pre-wrap",
                }}
              >
                <strong>{message.role === "user" ? "You" : "OperatorOS"}</strong>
                {message.model && (
                  <div style={{ color: "#8b949e", fontSize: 12, marginTop: 4 }}>
                    Model: <code>{message.model}</code>
                  </div>
                )}
                {message.documents?.length ? (
                  <div style={{ color: "#8b949e", fontSize: 12, marginTop: 4 }}>
                    RAG: {message.documents.join(", ")}
                  </div>
                ) : null}
                {message.role === "user" && (
                  <div style={{ color: "#8b949e", fontSize: 12, marginTop: 4 }}>
                    Annotated snapshot: {message.annotatedSnapshot ? "included" : "not sent"}
                  </div>
                )}
                <div style={{ marginTop: 6 }}>{message.content}</div>
              </div>
            ))
          ) : (
            <p style={{ color: "#8b949e" }}>Ask about the paused frame to start a chat.</p>
          )}
        </div>
        <h3>Transcript Window</h3>
        {transcriptWindow?.source && (
          <p style={{ color: transcriptWindow.source === "whisper" ? "#7ee787" : "#f2cc60" }}>
            Transcript source:{" "}
            {transcriptWindow.source === "whisper"
              ? `Whisper${transcriptWindow.model ? ` (${transcriptWindow.model})` : ""}`
              : transcriptWindow.source === "fallback"
                ? "fallback timestamps"
                : "empty"}
          </p>
        )}
        {transcriptError && (
          <p role="alert" style={{ color: "#f2cc60" }}>
            {transcriptError}
          </p>
        )}
        <pre style={{ whiteSpace: "pre-wrap" }}>
          {transcriptWindow
            ? transcriptWindow.segments.map((s) => `[${formatTimestamp(s.start)}-${formatTimestamp(s.end)}] ${s.text}`).join("\n")
            : "No transcript loaded"}
        </pre>
      </section>
    </main>
  );
}
