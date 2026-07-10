"use client";

import type { ComponentType } from "react";
import type { AnnotationType } from "@/lib/types";

const PALETTE = ["#ef4444", "#86efac", "#fde047", "#c4b5fd", "#ffffff"];

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
    <div className="op-card" style={{ marginTop: 14 }}>
      <div className="op-annotation-header">
        <h2 className="op-card-title" style={{ margin: 0 }}>
          Annotation Tools
        </h2>
        <div className="op-status-pill">
          <span className="op-status-dot" aria-hidden="true" />
          All system operational
        </div>
      </div>

      <div className="op-tool-row">
        {ANNOTATION_TOOLS.map(({ Icon, ...tool }) => (
          <button
            key={tool.type}
            type="button"
            className="op-tool-button"
            aria-label={tool.label}
            aria-pressed={activeTool === tool.type}
            title={tool.label}
            onClick={() => onToolChange(tool.type)}
          >
            <Icon />
          </button>
        ))}
        <button type="button" className="op-secondary-button" disabled={!canUndo} onClick={onUndo}>
          Undo
        </button>
        <button type="button" className="op-danger-text" disabled={!annotationsCount} onClick={onClear}>
          Clear
        </button>
      </div>

      <div className="op-color-row">
        <span className="op-color-label">Color</span>
        {PALETTE.map((color) => (
          <button
            key={color}
            type="button"
            className="op-color-swatch"
            aria-label={`Use ${color}`}
            data-active={drawColor === color}
            onClick={() => onColorChange(color)}
            style={{ background: color }}
          />
        ))}
        <label className="op-width-control">
          <span className="op-width-label">Width</span>
          <input
            type="range"
            min={1}
            max={8}
            value={strokeWidth}
            onChange={(event) => onStrokeWidthChange(Number(event.target.value))}
            style={{ accentColor: drawColor === "#ffffff" ? "#6366f1" : drawColor }}
          />
          <span className="op-width-value">{strokeWidth}</span>
        </label>
      </div>

      {activeTool === "text" && (
        <input
          className="op-text-tool-input"
          value={textAnnotation}
          onChange={(event) => onTextAnnotationChange(event.target.value)}
          placeholder="Text label to place on the frame"
        />
      )}

      <p className="op-annotation-hint">
        {isPaused
          ? ANNOTATION_TOOLS.find((tool) => tool.type === activeTool)?.hint ??
            "Select a tool from the bar above to start annotating the canvas."
          : "Pause playback to annotate the current frame."}
      </p>
    </div>
  );
}
