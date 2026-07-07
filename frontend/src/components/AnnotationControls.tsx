"use client";

import type { AnnotationType } from "@/lib/types";

const PALETTE = ["#ff6b6b", "#6b9fff", "#6bffb0", "#F2D055", "#AC78A1"];

export const ANNOTATION_TOOLS: Array<{
  type: AnnotationType;
  label: string;
  hint: string;
}> = [
  { type: "cursor", label: "Cursor", hint: "Control the video player without drawing" },
  { type: "select", label: "Select", hint: "Select and move annotations" },
  { type: "pen", label: "Pen", hint: "Drag to sketch a freehand path" },
  { type: "arrow", label: "Arrow", hint: "Drag to point at a region" },
  { type: "rect", label: "Rectangle", hint: "Drag to draw a rectangle" },
  { type: "circle", label: "Circle", hint: "Drag from the center outward" },
  { type: "eraser", label: "Eraser", hint: "Click or drag across annotations to remove them" },
  { type: "text", label: "Text", hint: "Click to place a label" },
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
        {ANNOTATION_TOOLS.map((tool) => (
          <button
            key={tool.type}
            type="button"
            aria-pressed={activeTool === tool.type}
            onClick={() => onToolChange(tool.type)}
            style={{
              border: `1px solid ${activeTool === tool.type ? "#DA5854" : "#30363d"}`,
              background: activeTool === tool.type ? "rgba(218, 88, 84, 0.2)" : "#0d1117",
              color: "#f0f6fc",
              borderRadius: 6,
              padding: "6px 10px",
              cursor: "pointer",
            }}
          >
            {tool.label}
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
