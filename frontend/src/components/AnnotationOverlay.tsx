"use client";

import { useEffect, useState } from "react";
import type { PointerEvent } from "react";
import type { Annotation, AnnotationType, Point } from "@/lib/types";

const ANNOTATION_COLOR = "#DA5854";
const DEFAULT_MARK_SIZE = 8;
const MIN_DRAW_SIZE = 1;
const MIN_PATH_POINT_DISTANCE = 0.35;

interface AnnotationOverlayProps {
  activeTool: AnnotationType;
  annotations: Annotation[];
  isPaused: boolean;
  textAnnotation: string;
  onAddAnnotation: (annotation: Annotation) => void;
}

function clampPercent(value: number) {
  return Math.min(100, Math.max(0, value));
}

function getSvgPoint(event: PointerEvent<SVGSVGElement>): Point {
  const bounds = event.currentTarget.getBoundingClientRect();
  return {
    x: clampPercent(((event.clientX - bounds.left) / bounds.width) * 100),
    y: clampPercent(((event.clientY - bounds.top) / bounds.height) * 100),
  };
}

function distanceBetween(a: Point, b: Point) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function makeRectangleAnnotation(start: Point, end: Point): Annotation {
  const width = Math.abs(end.x - start.x);
  const height = Math.abs(end.y - start.y);

  if (width < MIN_DRAW_SIZE && height < MIN_DRAW_SIZE) {
    return {
      type: "rect",
      color: ANNOTATION_COLOR,
      x: clampPercent(start.x - DEFAULT_MARK_SIZE / 2),
      y: clampPercent(start.y - DEFAULT_MARK_SIZE / 2),
      width: DEFAULT_MARK_SIZE,
      height: DEFAULT_MARK_SIZE,
    };
  }

  return {
    type: "rect",
    color: ANNOTATION_COLOR,
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.max(width, MIN_DRAW_SIZE),
    height: Math.max(height, MIN_DRAW_SIZE),
  };
}

function makeCircleAnnotation(start: Point, end: Point): Annotation {
  const radius = distanceBetween(start, end);

  return {
    type: "circle",
    color: ANNOTATION_COLOR,
    x: start.x,
    y: start.y,
    radius: radius < MIN_DRAW_SIZE ? DEFAULT_MARK_SIZE / 2 : radius,
  };
}

function appendPathPoint(annotation: Annotation, point: Point): Annotation {
  const points = annotation.points ?? [];
  const lastPoint = points.at(-1);
  if (lastPoint && distanceBetween(lastPoint, point) < MIN_PATH_POINT_DISTANCE) {
    return annotation;
  }

  return {
    ...annotation,
    points: [...points, point],
  };
}

function renderAnnotation(annotation: Annotation, key: string, isDraft = false) {
  const strokeWidth = isDraft ? 0.5 : 0.65;
  const strokeDasharray = isDraft ? "1.5 1.1" : undefined;

  if (
    annotation.type === "rect" &&
    annotation.x !== undefined &&
    annotation.y !== undefined &&
    annotation.width !== undefined &&
    annotation.height !== undefined
  ) {
    return (
      <rect
        key={key}
        x={annotation.x}
        y={annotation.y}
        width={annotation.width}
        height={annotation.height}
        fill="rgba(218, 88, 84, 0.1)"
        stroke={annotation.color}
        strokeWidth={strokeWidth}
        strokeDasharray={strokeDasharray}
      />
    );
  }

  if (
    annotation.type === "circle" &&
    annotation.x !== undefined &&
    annotation.y !== undefined &&
    annotation.radius !== undefined
  ) {
    return (
      <circle
        key={key}
        cx={annotation.x}
        cy={annotation.y}
        r={annotation.radius}
        fill="rgba(218, 88, 84, 0.1)"
        stroke={annotation.color}
        strokeWidth={strokeWidth}
        strokeDasharray={strokeDasharray}
      />
    );
  }

  if (annotation.type === "path" && annotation.points?.length) {
    return (
      <polyline
        key={key}
        points={annotation.points.map((p) => `${p.x},${p.y}`).join(" ")}
        fill="none"
        stroke={annotation.color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={strokeDasharray}
      />
    );
  }

  if (
    annotation.type === "text" &&
    annotation.x !== undefined &&
    annotation.y !== undefined &&
    annotation.text
  ) {
    return (
      <text
        key={key}
        x={annotation.x}
        y={annotation.y}
        fill={annotation.color}
        stroke="#161b22"
        strokeWidth={0.35}
        paintOrder="stroke"
        fontSize={4}
        fontWeight={700}
      >
        {annotation.text}
      </text>
    );
  }

  return null;
}

export function AnnotationOverlay({
  activeTool,
  annotations,
  isPaused,
  textAnnotation,
  onAddAnnotation,
}: AnnotationOverlayProps) {
  const [draftStart, setDraftStart] = useState<Point | null>(null);
  const [draftAnnotation, setDraftAnnotation] = useState<Annotation | null>(null);
  const [activePointerId, setActivePointerId] = useState<number | null>(null);

  function resetDraftAnnotation() {
    setDraftStart(null);
    setDraftAnnotation(null);
    setActivePointerId(null);
  }

  useEffect(() => {
    setDraftStart(null);
    setDraftAnnotation(null);
    setActivePointerId(null);
  }, [activeTool, isPaused]);

  function handlePointerDown(event: PointerEvent<SVGSVGElement>) {
    if (!isPaused) return;
    const point = getSvgPoint(event);

    if (activeTool === "text") {
      const text = textAnnotation.trim() || window.prompt("Annotation text")?.trim();
      if (!text) return;
      onAddAnnotation({
        type: "text",
        color: ANNOTATION_COLOR,
        x: point.x,
        y: point.y,
        text,
      });
      return;
    }

    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setActivePointerId(event.pointerId);
    setDraftStart(point);

    if (activeTool === "rect") {
      setDraftAnnotation(makeRectangleAnnotation(point, point));
    } else if (activeTool === "circle") {
      setDraftAnnotation(makeCircleAnnotation(point, point));
    } else {
      setDraftAnnotation({
        type: "path",
        color: ANNOTATION_COLOR,
        points: [point],
      });
    }
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    if (!isPaused || activePointerId !== event.pointerId || !draftStart) return;
    const point = getSvgPoint(event);

    if (activeTool === "rect") {
      setDraftAnnotation(makeRectangleAnnotation(draftStart, point));
    } else if (activeTool === "circle") {
      setDraftAnnotation(makeCircleAnnotation(draftStart, point));
    } else if (activeTool === "path") {
      setDraftAnnotation((prev) =>
        appendPathPoint(
          prev ?? {
            type: "path",
            color: ANNOTATION_COLOR,
            points: [draftStart],
          },
          point
        )
      );
    }
  }

  function handlePointerUp(event: PointerEvent<SVGSVGElement>) {
    if (activePointerId !== event.pointerId || !draftStart) return;
    const point = getSvgPoint(event);
    let finalAnnotation: Annotation | null = draftAnnotation;

    if (activeTool === "rect") {
      finalAnnotation = makeRectangleAnnotation(draftStart, point);
    } else if (activeTool === "circle") {
      finalAnnotation = makeCircleAnnotation(draftStart, point);
    } else if (activeTool === "path" && draftAnnotation) {
      finalAnnotation = appendPathPoint(draftAnnotation, point);
    }

    const committedAnnotation = finalAnnotation;
    if (
      committedAnnotation &&
      (committedAnnotation.type !== "path" || (committedAnnotation.points?.length ?? 0) > 1)
    ) {
      onAddAnnotation(committedAnnotation);
    }

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    resetDraftAnnotation();
  }

  return (
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
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={resetDraftAnnotation}
    >
      {annotations.map((annotation, idx) => renderAnnotation(annotation, `ann-${idx}`))}
      {draftAnnotation && renderAnnotation(draftAnnotation, "ann-draft", true)}
    </svg>
  );
}
