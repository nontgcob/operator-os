"use client";

import { FormEvent, useMemo, useRef, useState } from "react";
import {
  askQuestion,
  getTranscriptWindow,
  startTracking,
  uploadMedia,
} from "@/lib/api";
import type { Annotation, TrackingOverlay, TranscriptWindowResponse } from "@/lib/types";

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
  const [timestamp, setTimestamp] = useState(0);
  const [transcriptWindow, setTranscriptWindow] = useState<TranscriptWindowResponse | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [trackingOverlays, setTrackingOverlays] = useState<TrackingOverlay[]>([]);
  const [trackingEnabled, setTrackingEnabled] = useState(true);
  const [showTrackingOverlays, setShowTrackingOverlays] = useState(true);
  const [isPaused, setIsPaused] = useState(false);

  const currentOverlay = useMemo(() => {
    return trackingOverlays.filter(
      (overlay) => Math.abs(overlay.timestamp - timestamp) < 0.25
    );
  }, [trackingOverlays, timestamp]);

  async function captureFrame(): Promise<string> {
    const video = videoRef.current;
    if (!video) throw new Error("Video not ready");
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas unavailable");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.9);
  }

  async function handleUpload(file: File) {
    const objectUrl = URL.createObjectURL(file);
    setVideoUrl(objectUrl);
    const result = await uploadMedia(file);
    setVideoId(result.video_id);
  }

  async function handleAsk(event: FormEvent) {
    event.preventDefault();
    if (!videoId || !question.trim()) return;
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
        <input
          type="file"
          accept="video/mp4"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleUpload(file);
          }}
        />
        <div style={{ position: "relative", marginTop: 12 }}>
          <video
            ref={videoRef}
            controls
            src={videoUrl}
            style={{ width: "100%", borderRadius: 8 }}
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
          <svg
            style={{
              position: "absolute",
              inset: 0,
              pointerEvents: isPaused ? "auto" : "none",
              width: "100%",
              height: "100%",
            }}
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            onClick={(e) => {
              if (!isPaused) return;
              const bounds = e.currentTarget.getBoundingClientRect();
              const x = ((e.clientX - bounds.left) / bounds.width) * 100;
              const y = ((e.clientY - bounds.top) / bounds.height) * 100;
              setAnnotations((prev) => [
                ...prev,
                {
                  type: "rect",
                  color: "#DA5854",
                  x: Math.max(0, x - 4),
                  y: Math.max(0, y - 4),
                  width: 8,
                  height: 8,
                },
              ]);
            }}
          >
            {annotations.map((annotation, idx) => (
              <rect
                key={`ann-${idx}`}
                x={annotation.x}
                y={annotation.y}
                width={annotation.width}
                height={annotation.height}
                fill="none"
                stroke={annotation.color}
                strokeWidth={0.6}
              />
            ))}
            {showTrackingOverlays &&
              currentOverlay.map((overlay) => (
                <polygon
                  key={`${overlay.track_id}-${overlay.timestamp}`}
                  points={overlay.points.map((p) => `${p.x},${p.y}`).join(" ")}
                  fill="none"
                  stroke={overlay.color}
                  strokeWidth={0.7}
                />
              ))}
          </svg>
        </div>
      </section>

      <section style={{ background: "#161b22", borderRadius: 8, padding: 12 }}>
        <h2>Contextual Chat</h2>
        <p>Timestamp: {timestamp.toFixed(2)}s</p>
        <p>Paused annotations: {annotations.length} (click video while paused to add)</p>
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
          <button type="submit" disabled={loading || !videoId}>
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
