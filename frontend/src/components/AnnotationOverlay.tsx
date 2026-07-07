"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
import type { Annotation, AnnotationType, AnnotationUndoEntry, Point } from "@/lib/types";

const ANNOTATION_SCALE = 1.5;
const MIN_DRAG_DISTANCE = 10;

interface AnnotationOverlayProps {
  activeTool: AnnotationType;
  annotations: Annotation[];
  drawColor: string;
  isPaused: boolean;
  strokeWidth: number;
  textAnnotation: string;
  videoAspectRatio: number;
  onAnnotationsChange: (annotations: Annotation[]) => void;
  onPushUndo: (entry: AnnotationUndoEntry) => void;
}

function clampRagvlm(value: number) {
  return Math.min(1000, Math.max(0, value));
}

function getSvgPoint(event: PointerEvent<SVGSVGElement>): Point {
  const bounds = event.currentTarget.getBoundingClientRect();
  return {
    x: clampRagvlm(((event.clientX - bounds.left) / bounds.width) * 1000),
    y: clampRagvlm(((event.clientY - bounds.top) / bounds.height) * 1000),
  };
}

function v(value = 0) {
  return (value / 1000) * 100;
}

function pathFromPoints(points: Point[]) {
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
}

function objectPoints(points: Annotation["points"]): Point[] {
  return (points ?? []).filter((point): point is Point => !Array.isArray(point));
}

function tuplePoints(points: Annotation["points"]): number[][] {
  return (points ?? []).filter((point): point is number[] => Array.isArray(point));
}

function scalePath(d: string) {
  return d.replace(/([-+]?\d*\.?\d+)/g, (match) => String(parseFloat(match) / 10));
}

function translatePath(d: string, dx: number, dy: number) {
  let isX = true;
  return d.replace(/([-+]?\d*\.?\d+)/g, (match) => {
    const next = parseFloat(match) + (isX ? dx : dy);
    isX = !isX;
    return String(Math.round(next * 10) / 10);
  });
}

function translateAnnotation(annotation: Annotation, dx: number, dy: number): Annotation {
  switch (annotation.type) {
    case "circle":
      return {
        ...annotation,
        cx: annotation.cx !== undefined ? clampRagvlm(annotation.cx + dx) : undefined,
        cy: annotation.cy !== undefined ? clampRagvlm(annotation.cy + dy) : undefined,
        x: annotation.x !== undefined ? clampRagvlm(annotation.x + dx) : undefined,
        y: annotation.y !== undefined ? clampRagvlm(annotation.y + dy) : undefined,
      };
    case "rect":
    case "text":
    case "number":
      return {
        ...annotation,
        x: annotation.x !== undefined ? clampRagvlm(annotation.x + dx) : undefined,
        y: annotation.y !== undefined ? clampRagvlm(annotation.y + dy) : undefined,
      };
    case "arrow":
      return {
        ...annotation,
        x1: annotation.x1 !== undefined ? clampRagvlm(annotation.x1 + dx) : undefined,
        y1: annotation.y1 !== undefined ? clampRagvlm(annotation.y1 + dy) : undefined,
        x2: annotation.x2 !== undefined ? clampRagvlm(annotation.x2 + dx) : undefined,
        y2: annotation.y2 !== undefined ? clampRagvlm(annotation.y2 + dy) : undefined,
      };
    case "path":
      return {
        ...annotation,
        d: annotation.d ? translatePath(annotation.d, dx, dy) : undefined,
        points: objectPoints(annotation.points).map((point) => ({
          x: clampRagvlm(point.x + dx),
          y: clampRagvlm(point.y + dy),
        })),
      };
    case "polygon":
      return {
        ...annotation,
        points: tuplePoints(annotation.points).map(([x, y]) => [clampRagvlm(x + dx), clampRagvlm(y + dy)]),
      };
    default:
      return annotation;
  }
}

function annotationIndex(target: EventTarget | null): number | null {
  let element = target as Element | null;
  while (element) {
    const value = element.getAttribute?.("data-annotation-index");
    if (value !== null && value !== undefined) return Number(value);
    element = element.parentElement;
  }
  return null;
}

function toolCursor(tool: AnnotationType) {
  if (tool === "cursor") return "auto";
  if (tool === "select") return "default";
  if (tool === "eraser") return "cell";
  return "crosshair";
}

function renderAnnotation(
  annotation: Annotation,
  index: number,
  {
    aspectRatio,
    interactive,
    isDraft = false,
    selected = false,
  }: {
    aspectRatio: number;
    interactive: boolean;
    isDraft?: boolean;
    selected?: boolean;
  }
) {
  const color = annotation.color ?? "#ff6b6b";
  const strokeWidth = ((annotation.strokeWidth ?? 15) / 1000) * 100 * ANNOTATION_SCALE;
  const dataAttrs = interactive ? { "data-annotation-index": String(index) } : {};
  const interactiveStyle = {
    cursor: interactive ? "move" : "default",
    filter: selected ? "drop-shadow(0 0 0.6px #9ecbff) drop-shadow(0 0 0.6px #9ecbff)" : undefined,
    pointerEvents: interactive ? "auto" : "none",
  } as const;
  const dash = isDraft ? "1.5 1.1" : undefined;

  if (
    annotation.type === "rect" &&
    annotation.x !== undefined &&
    annotation.y !== undefined &&
    annotation.width !== undefined &&
    annotation.height !== undefined
  ) {
    return (
      <rect
        key={index}
        {...dataAttrs}
        x={v(annotation.x)}
        y={v(annotation.y)}
        width={v(annotation.width)}
        height={v(annotation.height)}
        fill={annotation.fill ?? "none"}
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={dash}
        style={interactiveStyle}
      />
    );
  }

  if (
    annotation.type === "circle" &&
    (annotation.cx ?? annotation.x) !== undefined &&
    (annotation.cy ?? annotation.y) !== undefined &&
    (annotation.r ?? annotation.radius) !== undefined
  ) {
    const r = v(annotation.r ?? annotation.radius);
    return (
      <ellipse
        key={index}
        {...dataAttrs}
        cx={v(annotation.cx ?? annotation.x)}
        cy={v(annotation.cy ?? annotation.y)}
        rx={r / aspectRatio}
        ry={r}
        fill={annotation.fill ?? "none"}
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={dash}
        style={interactiveStyle}
      />
    );
  }

  if (annotation.type === "path" && (annotation.d || annotation.points?.length)) {
    return (
      <path
        key={index}
        {...dataAttrs}
        d={scalePath(annotation.d ?? pathFromPoints(objectPoints(annotation.points)))}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={dash}
        style={interactiveStyle}
      />
    );
  }

  if (annotation.type === "polygon" && annotation.points?.length) {
    const points = tuplePoints(annotation.points)
      .map(([x, y]) => `${v(x)},${v(y)}`)
      .join(" ");
    return (
      <polygon
        key={index}
        {...dataAttrs}
        points={points}
        fill={annotation.fill ?? "none"}
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={dash}
        strokeLinejoin="round"
        style={interactiveStyle}
      />
    );
  }

  if (
    annotation.type === "arrow" &&
    annotation.x1 !== undefined &&
    annotation.y1 !== undefined &&
    annotation.x2 !== undefined &&
    annotation.y2 !== undefined
  ) {
    const x1 = v(annotation.x1);
    const y1 = v(annotation.y1);
    const x2 = v(annotation.x2);
    const y2 = v(annotation.y2);
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const headLength = Math.max(2, strokeWidth * 3);
    const headA = {
      x: x2 - headLength * Math.cos(angle - Math.PI / 6),
      y: y2 - headLength * Math.sin(angle - Math.PI / 6),
    };
    const headB = {
      x: x2 - headLength * Math.cos(angle + Math.PI / 6),
      y: y2 - headLength * Math.sin(angle + Math.PI / 6),
    };
    const shaftEnd = {
      x: x2 - headLength * Math.cos(angle),
      y: y2 - headLength * Math.sin(angle),
    };
    return (
      <g key={index} {...dataAttrs} style={interactiveStyle}>
        <line
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          stroke="transparent"
          strokeWidth={Math.max(strokeWidth, 3)}
          style={{ pointerEvents: interactive ? "stroke" : "none" }}
        />
        <line
          x1={x1}
          y1={y1}
          x2={shaftEnd.x}
          y2={shaftEnd.y}
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={dash}
          style={{ pointerEvents: "none" }}
        />
        <polygon
          points={`${x2},${y2} ${headA.x},${headA.y} ${headB.x},${headB.y}`}
          fill={color}
          style={{ pointerEvents: "none" }}
        />
      </g>
    );
  }

  if (
    annotation.type === "text" &&
    annotation.x !== undefined &&
    annotation.y !== undefined &&
    (annotation.text || annotation.content)
  ) {
    return (
      <text
        key={index}
        {...dataAttrs}
        x={v(annotation.x)}
        y={v(annotation.y)}
        fill={color}
        stroke="#161b22"
        strokeWidth={strokeWidth * 0.3}
        paintOrder="stroke"
        fontSize={v(annotation.fontSize ?? 28) * ANNOTATION_SCALE}
        fontWeight={700}
        style={interactiveStyle}
      >
        {annotation.text ?? annotation.content}
      </text>
    );
  }

  return null;
}

export function AnnotationOverlay({
  activeTool,
  annotations,
  drawColor,
  isPaused,
  strokeWidth,
  textAnnotation,
  videoAspectRatio,
  onAnnotationsChange,
  onPushUndo,
}: AnnotationOverlayProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const annotationsRef = useRef(annotations);
  const activeToolRef = useRef(activeTool);
  const drawColorRef = useRef(drawColor);
  const strokeWidthRef = useRef(strokeWidth);
  const dragRef = useRef<{
    idx: number;
    lastPoint: Point;
    original: Annotation;
    hasMoved: boolean;
  } | null>(null);
  const isDrawingRef = useRef(false);
  const draftStartRef = useRef<Point | null>(null);
  const penPointsRef = useRef<Point[]>([]);

  const [draftAnnotation, setDraftAnnotation] = useState<Annotation | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  useEffect(() => {
    annotationsRef.current = annotations;
  }, [annotations]);

  useEffect(() => {
    activeToolRef.current = activeTool;
    drawColorRef.current = drawColor;
    strokeWidthRef.current = strokeWidth;
  }, [activeTool, drawColor, strokeWidth]);

  const resetDraftAnnotation = useCallback(() => {
    isDrawingRef.current = false;
    draftStartRef.current = null;
    penPointsRef.current = [];
    dragRef.current = null;
    setDraftAnnotation(null);
  }, []);

  useEffect(() => {
    resetDraftAnnotation();
    if (activeTool !== "select") {
      setSelectedIndex(null);
    }
  }, [activeTool, isPaused, resetDraftAnnotation]);

  function handlePointerDown(event: PointerEvent<SVGSVGElement>) {
    if (!isPaused || activeTool === "cursor") return;
    const point = getSvgPoint(event);
    const tool = activeToolRef.current;
    const color = drawColorRef.current;
    const scaledStroke = strokeWidthRef.current * 5;

    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);

    if (tool === "eraser") {
      const idx = annotationIndex(event.target);
      if (idx !== null) eraseAnnotation(idx);
      isDrawingRef.current = true;
      return;
    }

    if (tool === "select") {
      const idx = annotationIndex(event.target);
      if (idx !== null) {
        setSelectedIndex(idx);
        dragRef.current = {
          idx,
          lastPoint: point,
          original: annotationsRef.current[idx],
          hasMoved: false,
        };
      } else {
        setSelectedIndex(null);
      }
      return;
    }

    if (tool === "text") {
      const text = textAnnotation.trim() || window.prompt("Annotation text")?.trim();
      if (!text) return;
      addAnnotation({
        type: "text",
        color,
        coordinate_space: "ragvlm_0_1000",
        fontSize: 28,
        strokeWidth: scaledStroke,
        x: point.x,
        y: point.y,
        text,
      });
      return;
    }

    isDrawingRef.current = true;
    draftStartRef.current = point;

    if (tool === "pen") {
      penPointsRef.current = [point];
      setDraftAnnotation({
        type: "path",
        color,
        coordinate_space: "ragvlm_0_1000",
        d: `M ${point.x} ${point.y}`,
        points: [point],
        strokeWidth: scaledStroke,
      });
    }
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    if (!isPaused) return;
    const point = getSvgPoint(event);
    const color = drawColorRef.current;
    const scaledStroke = strokeWidthRef.current * 5;
    const tool = activeToolRef.current;

    if (tool === "eraser" && isDrawingRef.current) {
      const idx = annotationIndex(document.elementFromPoint(event.clientX, event.clientY));
      if (idx !== null) eraseAnnotation(idx);
      return;
    }

    if (dragRef.current) {
      const { idx, lastPoint } = dragRef.current;
      const dx = point.x - lastPoint.x;
      const dy = point.y - lastPoint.y;
      const next = [...annotationsRef.current];
      next[idx] = translateAnnotation(next[idx], dx, dy);
      dragRef.current.lastPoint = point;
      dragRef.current.hasMoved = true;
      onAnnotationsChange(next);
      return;
    }

    const start = draftStartRef.current;
    if (!isDrawingRef.current || !start) return;

    if (tool === "pen") {
      penPointsRef.current = [...penPointsRef.current, point];
      setDraftAnnotation({
        type: "path",
        color,
        coordinate_space: "ragvlm_0_1000",
        d: pathFromPoints(penPointsRef.current),
        points: penPointsRef.current,
        strokeWidth: scaledStroke,
      });
    } else if (tool === "arrow") {
      setDraftAnnotation({
        type: "arrow",
        color,
        coordinate_space: "ragvlm_0_1000",
        strokeWidth: scaledStroke,
        x1: start.x,
        y1: start.y,
        x2: point.x,
        y2: point.y,
      });
    } else if (tool === "rect") {
      setDraftAnnotation({
        type: "rect",
        color,
        coordinate_space: "ragvlm_0_1000",
        fill: "none",
        strokeWidth: scaledStroke,
        x: Math.min(start.x, point.x),
        y: Math.min(start.y, point.y),
        width: Math.abs(point.x - start.x),
        height: Math.abs(point.y - start.y),
      });
    } else if (tool === "circle") {
      setDraftAnnotation({
        type: "circle",
        color,
        coordinate_space: "ragvlm_0_1000",
        fill: "none",
        strokeWidth: scaledStroke,
        cx: start.x,
        cy: start.y,
        r: Math.hypot(point.x - start.x, point.y - start.y),
      });
    }
  }

  function handlePointerUp(event: PointerEvent<SVGSVGElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    if (dragRef.current) {
      const { idx, original, hasMoved } = dragRef.current;
      if (hasMoved) {
        onPushUndo({ op: "replace", idx, previous: original });
      }
      dragRef.current = null;
      return;
    }

    if (isDrawingRef.current && draftAnnotation && isValidAnnotation(draftAnnotation)) {
      addAnnotation(draftAnnotation);
    }

    resetDraftAnnotation();
  }

  function addAnnotation(annotation: Annotation) {
    const next = [...annotationsRef.current, annotation];
    onAnnotationsChange(next);
    onPushUndo({ op: "pop", count: 1 });
  }

  function eraseAnnotation(idx: number) {
    const annotation = annotationsRef.current[idx];
    if (!annotation) return;
    const next = annotationsRef.current.filter((_, index) => index !== idx);
    onAnnotationsChange(next);
    onPushUndo({ op: "insert", idx, annotation });
    setSelectedIndex(null);
  }

  function isValidAnnotation(annotation: Annotation) {
    if (annotation.type === "path") return (annotation.points?.length ?? 0) > 1;
    if (annotation.type === "arrow") return Math.hypot((annotation.x2 ?? 0) - (annotation.x1 ?? 0), (annotation.y2 ?? 0) - (annotation.y1 ?? 0)) > MIN_DRAG_DISTANCE;
    if (annotation.type === "rect") return (annotation.width ?? 0) > MIN_DRAG_DISTANCE || (annotation.height ?? 0) > MIN_DRAG_DISTANCE;
    if (annotation.type === "circle") return (annotation.r ?? annotation.radius ?? 0) > MIN_DRAG_DISTANCE;
    return true;
  }

  const overlayInteractive = isPaused && activeTool !== "cursor";
  const shapeInteractive = activeTool === "select" || activeTool === "eraser";

  return (
    <svg
      ref={svgRef}
      style={{
        position: "absolute",
        inset: 0,
        cursor: toolCursor(activeTool),
        pointerEvents: overlayInteractive ? "auto" : "none",
        touchAction: overlayInteractive ? "none" : "auto",
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
      {annotations.map((annotation, idx) =>
        renderAnnotation(annotation, idx, {
          aspectRatio: videoAspectRatio,
          interactive: shapeInteractive,
          selected: selectedIndex === idx,
        })
      )}
      {draftAnnotation &&
        renderAnnotation(draftAnnotation, -1, {
          aspectRatio: videoAspectRatio,
          interactive: false,
          isDraft: true,
        })}
    </svg>
  );
}
