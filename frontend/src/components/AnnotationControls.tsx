"use client";

import type { AnnotationType } from "@/lib/types";

export const ANNOTATION_TOOLS: Array<{
  type: AnnotationType;
  label: string;
  hint: string;
}> = [
  { type: "rect", label: "Rectangle", hint: "Drag to draw a rectangle" },
  { type: "circle", label: "Circle", hint: "Drag from the center outward" },
  { type: "path", label: "Freehand", hint: "Drag to sketch a path" },
  { type: "text", label: "Text", hint: "Click to place a label" },
];

interface AnnotationControlsProps {
  activeTool: AnnotationType;
  isPaused: boolean;
  textAnnotation: string;
  onToolChange: (tool: AnnotationType) => void;
  onTextAnnotationChange: (text: string) => void;
}

export function AnnotationControls({
  activeTool,
  isPaused,
  textAnnotation,
  onToolChange,
  onTextAnnotationChange,
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
