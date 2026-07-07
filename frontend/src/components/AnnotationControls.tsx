"use client";

import type { ComponentType } from "react";
import type { AnnotationType } from "@/lib/types";

const PALETTE = ["#ff6b6b", "#6b9fff", "#6bffb0", "#F2D055", "#AC78A1"];

const iconStyle = { height: 16, width: 16 } as const;

const IconCursor = () => (
  <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" style={iconStyle}>
    <path d="M3 1 L3 12 L6 9 L8 13.5 L10 12.5 L8 8 L12 8 Z" />
  </svg>
);

const IconSelect = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={iconStyle}>
    <path d="M8 2 L8 14" />
    <path d="M2 8 L14 8" />
    <path d="M8 2 L5.6 4.4" />
    <path d="M8 2 L10.4 4.4" />
    <path d="M8 14 L5.6 11.6" />
    <path d="M8 14 L10.4 11.6" />
    <path d="M2 8 L4.4 5.6" />
    <path d="M2 8 L4.4 10.4" />
    <path d="M14 8 L11.6 5.6" />
    <path d="M14 8 L11.6 10.4" />
  </svg>
);

const IconPen = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={iconStyle}>
    <path d="M10.5 2.5 L13.5 5.5 L5 14 L2 14 L2 11 Z" />
    <path d="M9 4 L12 7" />
  </svg>
);

const IconArrow = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={iconStyle}>
    <path d="M3 13 L13 3" />
    <path d="M7 3 L13 3 L13 9" />
  </svg>
);

const IconRect = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" style={iconStyle}>
    <rect x="2" y="3" width="12" height="10" rx="1" />
  </svg>
);

const IconCircle = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" style={iconStyle}>
    <circle cx="8" cy="8" r="5.5" />
  </svg>
);

const IconEraser = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={iconStyle}>
    <polygon points="4,2 13,2 13,10 4,10 1,6" />
    <line x1="4" y1="2" x2="4" y2="10" />
    <path d="M6.5 4.5 L10.5 7.5 M10.5 4.5 L6.5 7.5" />
  </svg>
);

const IconText = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={iconStyle}>
    <path d="M3 3 H13" />
    <path d="M8 3 V13" />
    <path d="M5.5 13 H10.5" />
  </svg>
);

export const ANNOTATION_TOOLS: Array<{
  type: AnnotationType;
  label: string;
  hint: string;
  Icon: ComponentType;
}> = [
  { type: "cursor", label: "Cursor", hint: "Control the video player without drawing", Icon: IconCursor },
  { type: "select", label: "Select", hint: "Select and move annotations", Icon: IconSelect },
  { type: "pen", label: "Pen", hint: "Drag to sketch a freehand path", Icon: IconPen },
  { type: "arrow", label: "Arrow", hint: "Drag to point at a region", Icon: IconArrow },
  { type: "rect", label: "Rectangle", hint: "Drag to draw a rectangle", Icon: IconRect },
  { type: "circle", label: "Circle", hint: "Drag from the center outward", Icon: IconCircle },
  { type: "eraser", label: "Eraser", hint: "Click or drag across annotations to remove them", Icon: IconEraser },
  { type: "text", label: "Text", hint: "Click to place a label", Icon: IconText },
];

interface AnnotationControlsProps {
  activeTool: AnnotationType;
  annotationsCount: number;
  canUndo: boolean;
  drawColor: string;
  isPaused: boolean;
  strokeWidth: number;
  textAnnotation: string;
  onClear: () => void;
  onColorChange: (color: string) => void;
  onStrokeWidthChange: (width: number) => void;
  onToolChange: (tool: AnnotationType) => void;
  onTextAnnotationChange: (text: string) => void;
  onUndo: () => void;
}

export function AnnotationControls({
  activeTool,
  annotationsCount,
  canUndo,
  drawColor,
  isPaused,
  strokeWidth,
  textAnnotation,
  onClear,
  onColorChange,
  onStrokeWidthChange,
  onToolChange,
  onTextAnnotationChange,
  onUndo,
}: AnnotationControlsProps) {
  return (
    <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <strong>Annotation tool</strong>
        {ANNOTATION_TOOLS.map(({ Icon, ...tool }) => (
          <button
            key={tool.type}
            type="button"
            aria-label={tool.label}
            aria-pressed={activeTool === tool.type}
            title={tool.label}
            onClick={() => onToolChange(tool.type)}
            style={{
              border: `1px solid ${activeTool === tool.type ? "#1d4ed8" : "#30363d"}`,
              background: activeTool === tool.type ? "#2563eb" : "#0d1117",
              color: "#f0f6fc",
              borderRadius: 6,
              cursor: "pointer",
              display: "grid",
              height: 32,
              placeItems: "center",
              width: 32,
            }}
          >
            <Icon />
          </button>
        ))}
        <button
          type="button"
          disabled={!canUndo}
          onClick={onUndo}
          style={{
            background: "#0d1117",
            border: "1px solid #30363d",
            borderRadius: 6,
            color: "#f0f6fc",
            cursor: canUndo ? "pointer" : "not-allowed",
            opacity: canUndo ? 1 : 0.45,
            padding: "6px 10px",
          }}
        >
          Undo
        </button>
        <button
          type="button"
          disabled={!annotationsCount}
          onClick={onClear}
          style={{
            background: "rgba(248, 81, 73, 0.18)",
            border: "1px solid #7f1d1d",
            borderRadius: 6,
            color: "#ffb4ad",
            cursor: annotationsCount ? "pointer" : "not-allowed",
            opacity: annotationsCount ? 1 : 0.45,
            padding: "6px 10px",
          }}
        >
          Clear
        </button>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
        <span style={{ color: "#8b949e", fontSize: 12 }}>Color</span>
        {PALETTE.map((color) => (
          <button
            key={color}
            type="button"
            aria-label={`Use ${color}`}
            onClick={() => onColorChange(color)}
            style={{
              background: color,
              border: drawColor === color ? "2px solid #f0f6fc" : "2px solid transparent",
              borderRadius: 999,
              cursor: "pointer",
              height: 22,
              outline: "1px solid #30363d",
              width: 22,
            }}
          />
        ))}
        <label style={{ display: "flex", gap: 6, alignItems: "center", color: "#8b949e", fontSize: 12 }}>
          Width
          <input
            type="range"
            min={1}
            max={8}
            value={strokeWidth}
            onChange={(event) => onStrokeWidthChange(Number(event.target.value))}
            style={{ accentColor: drawColor }}
          />
          <span>{strokeWidth}</span>
        </label>
      </div>
      {activeTool === "text" && (
        <input
          value={textAnnotation}
          onChange={(event) => onTextAnnotationChange(event.target.value)}
          placeholder="Text label to place on the frame"
          style={{
            background: "#0d1117",
            border: "1px solid #30363d",
            borderRadius: 6,
            color: "#f0f6fc",
            padding: 8,
          }}
        />
      )}
      <small style={{ color: "#8b949e" }}>
        {isPaused
          ? ANNOTATION_TOOLS.find((tool) => tool.type === activeTool)?.hint
          : "Pause playback to annotate the current frame."}
      </small>
    </div>
  );
}
