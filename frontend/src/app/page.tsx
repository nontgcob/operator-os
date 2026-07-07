"use client";

import { useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AnnotationControls } from "@/components/AnnotationControls";
import { AnnotationOverlay } from "@/components/AnnotationOverlay";
import {
  askQuestion,
  getMediaSourceUrl,
  getTranscriptWindow,
  ingestYoutubeUrl,
  startTracking,
  uploadMedia,
} from "@/lib/api";
import type {
  Annotation,
  AnnotationType,
  TrackingOverlay,
  TranscriptWindowResponse,
} from "@/lib/types";

function readSSE(response: Response, onDelta: (chunk: string) => void) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Missing stream body");
  const decoder = new TextDecoder();
  return (async () => {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value, { stream: true });
      for (const line of text.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (payload === "[DONE]") return;
        onDelta(payload);
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
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState("");
  const [ingestStatus, setIngestStatus] = useState("");
  const [pendingVideoReadyStatus, setPendingVideoReadyStatus] = useState("");
  const [videoMetadataLoaded, setVideoMetadataLoaded] = useState(false);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [timestamp, setTimestamp] = useState(0);
  const [transcriptWindow, setTranscriptWindow] = useState<TranscriptWindowResponse | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [trackingOverlays, setTrackingOverlays] = useState<TrackingOverlay[]>([]);
  const [trackingEnabled, setTrackingEnabled] = useState(true);
  const [showTrackingOverlays, setShowTrackingOverlays] = useState(true);
  const [isPaused, setIsPaused] = useState(false);
  const [activeTool, setActiveTool] = useState<AnnotationType>("rect");
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
    setPendingVideoReadyStatus("");
    setTimestamp(0);
    setTranscriptWindow(null);
    setAnnotations([]);
    setTrackingOverlays([]);
    setAnswer("");
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
    if (!videoId || !videoMetadataLoaded || !question.trim()) return;
    const frameData = await captureFrame();
    const transcript = await getTranscriptWindow(videoId, timestamp);
    setTranscriptWindow(transcript);
    setLoading(true);
    setAnswer("");
    try {
      const trackingPromise =
        trackingEnabled
          ? startTracking({
              session_id: sessionId,
              video_id: videoId,
              timestamp,
              frame_data_url: frameData,
              question,
              annotations,
            })
          : Promise.resolve(null);

      const response = await askQuestion({
        session_id: sessionId,
        video_id: videoId,
        timestamp,
        frame_data_url: frameData,
        question,
        annotations,
        transcript_window: transcript,
        document_ids: [],
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      await readSSE(response, (chunk) => {
        setAnswer((prev) => prev + chunk);
      });
      if (trackingEnabled) {
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
      }
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
          isPaused={isPaused}
          textAnnotation={textAnnotation}
          onToolChange={setActiveTool}
          onTextAnnotationChange={setTextAnnotation}
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
                setIsPaused(true);
                if (videoId) {
                  const transcript = await getTranscriptWindow(videoId, nextTs);
                  setTranscriptWindow(transcript);
                }
              }}
              onPlay={() => {
                setAnnotations([]);
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
              isPaused={isPaused}
              textAnnotation={textAnnotation}
              onAddAnnotation={(annotation) => setAnnotations((prev) => [...prev, annotation])}
            />
          )}
        </div>
      </section>

      <section style={{ background: "#161b22", borderRadius: 8, padding: 12 }}>
        <h2>Contextual Chat</h2>
        <p>Timestamp: {timestamp.toFixed(2)}s</p>
        <p>
          Paused annotations: {annotations.length} (
          {isPaused ? `${activeTool} tool active` : "pause video to add"})
        </p>
        <button type="button" disabled={!annotations.length} onClick={() => setAnnotations([])}>
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
        <form onSubmit={handleAsk} style={{ display: "grid", gap: 8, marginTop: 12 }}>
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
        <h3>Answer</h3>
        <pre style={{ whiteSpace: "pre-wrap" }}>{answer}</pre>
        <h3>Transcript Window</h3>
        <pre style={{ whiteSpace: "pre-wrap" }}>
          {transcriptWindow
            ? transcriptWindow.segments.map((s) => `[${s.start.toFixed(1)}-${s.end.toFixed(1)}] ${s.text}`).join("\n")
            : "No transcript loaded"}
        </pre>
      </section>
    </main>
  );
}
