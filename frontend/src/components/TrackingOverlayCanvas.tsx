"use client";

import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import { visibleOverlaysAtTime } from "@/lib/trackingLayers";
import type { TrackingLayer, TrackingOverlay } from "@/lib/types";

interface TrackingOverlayCanvasProps {
  enabled: boolean;
  layers: TrackingLayer[];
  liveOverlays?: TrackingOverlay[];
  videoRef: RefObject<HTMLVideoElement | null>;
  videoTimeOffset?: number;
}

export function TrackingOverlayCanvas({
  enabled,
  layers,
  liveOverlays = [],
  videoRef,
  videoTimeOffset = 0,
}: TrackingOverlayCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const videoElement = video;
    const canvasElement = canvas;
    const drawingContext = context;
    const frameCallbacks = videoElement as unknown as {
      requestVideoFrameCallback?: (callback: VideoFrameRequestCallback) => number;
      cancelVideoFrameCallback?: (handle: number) => void;
    };

    let stopped = false;
    let videoFrameHandle = 0;
    let animationFrameHandle = 0;

    function draw(mediaTime: number) {
      const width = Math.max(1, Math.round(videoElement.clientWidth * window.devicePixelRatio));
      const height = Math.max(1, Math.round(videoElement.clientHeight * window.devicePixelRatio));
      if (canvasElement.width !== width || canvasElement.height !== height) {
        canvasElement.width = width;
        canvasElement.height = height;
      }
      drawingContext.clearRect(0, 0, width, height);
      if (!enabled) return;

      const sourceTime = mediaTime + videoTimeOffset;
      const completed = visibleOverlaysAtTime(layers, sourceTime);
      const live = liveOverlays.filter((overlay) => Math.abs(overlay.timestamp - sourceTime) <= 0.1);
      for (const overlay of [...completed, ...live]) {
        if (overlay.points.length < 3) continue;
        drawingContext.beginPath();
        overlay.points.forEach((point, index) => {
          const x = (Math.min(100, Math.max(0, point.x)) / 100) * width;
          const y = (Math.min(100, Math.max(0, point.y)) / 100) * height;
          if (index === 0) drawingContext.moveTo(x, y);
          else drawingContext.lineTo(x, y);
        });
        drawingContext.closePath();
        drawingContext.globalAlpha = 0.38;
        drawingContext.fillStyle = overlay.color;
        drawingContext.fill();
        drawingContext.globalAlpha = 0.95;
        drawingContext.strokeStyle = overlay.color;
        drawingContext.lineWidth = Math.max(1, 1.5 * window.devicePixelRatio);
        drawingContext.stroke();
      }
      drawingContext.globalAlpha = 1;
    }

    function scheduleVideoFrame() {
      if (stopped) return;
      if (frameCallbacks.requestVideoFrameCallback) {
        videoFrameHandle = frameCallbacks.requestVideoFrameCallback((_now, metadata) => {
          draw(metadata.mediaTime);
          scheduleVideoFrame();
        });
      } else {
        animationFrameHandle = window.requestAnimationFrame(() => {
          draw(videoElement.currentTime);
          scheduleVideoFrame();
        });
      }
    }

    draw(videoElement.currentTime);
    scheduleVideoFrame();
    return () => {
      stopped = true;
      if (videoFrameHandle && frameCallbacks.cancelVideoFrameCallback) {
        frameCallbacks.cancelVideoFrameCallback(videoFrameHandle);
      }
      if (animationFrameHandle) window.cancelAnimationFrame(animationFrameHandle);
    };
  }, [enabled, layers, liveOverlays, videoRef, videoTimeOffset]);

  return <canvas ref={canvasRef} className="op-tracking-canvas" aria-hidden="true" />;
}
