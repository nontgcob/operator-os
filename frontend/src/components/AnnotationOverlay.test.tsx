import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AnnotationOverlay } from "@/components/AnnotationOverlay";
import type { Annotation } from "@/lib/types";

function renderOverlay(modelAnnotations: Annotation[]) {
  return render(
    <AnnotationOverlay
      activeTool="cursor"
      annotations={[]}
      modelAnnotations={modelAnnotations}
      drawColor="#ffffff"
      isPaused={false}
      textAnnotation=""
      videoAspectRatio={16 / 9}
      onAnnotationsChange={vi.fn()}
      onPushUndo={vi.fn()}
    />
  );
}

function labelTranslation(label: string) {
  const transform = screen.getByText(label).closest("g")?.getAttribute("transform") ?? "";
  const match = /^translate\(([-.\d]+) ([-.\d]+)\)$/.exec(transform);
  if (!match) throw new Error(`Unexpected label transform: ${transform}`);
  return { x: Number(match[1]), y: Number(match[2]) };
}

describe("AnnotationOverlay machine labels", () => {
  it("keeps the standard label when a shape has no placed text label", () => {
    renderOverlay([
      {
        type: "rect",
        x: 100,
        y: 100,
        width: 300,
        height: 300,
        text: "Matcha maker",
        color: "#3b82f6",
      },
    ]);

    expect(screen.getByText("Matcha maker")).toBeInTheDocument();
  });

  it("uses placed text as the only label when it matches a shape label", () => {
    renderOverlay([
      {
        type: "rect",
        x: 100,
        y: 100,
        width: 300,
        height: 300,
        text: "Empty Cup",
        color: "#f59e0b",
      },
      {
        type: "text",
        x: 700,
        y: 100,
        text: "Empty Cups",
        color: "#f59e0b",
      },
    ]);

    expect(screen.queryByText("Empty Cup")).not.toBeInTheDocument();
    expect(screen.getAllByText("Empty Cups")).toHaveLength(1);
  });

  it("replaces a nearby generic shape tag with the placed text", () => {
    renderOverlay([
      {
        type: "circle",
        cx: 350,
        cy: 400,
        r: 80,
        color: "#22c55e",
      },
      {
        type: "text",
        x: 300,
        y: 480,
        text: "Matcha drink block",
        color: "#22c55e",
      },
    ]);

    expect(screen.queryByText("Visual target 1")).not.toBeInTheDocument();
    expect(screen.getAllByText("Matcha drink block")).toHaveLength(1);
  });

  it("retains standard labels for unrelated shapes", () => {
    renderOverlay([
      {
        type: "rect",
        x: 100,
        y: 100,
        width: 200,
        height: 200,
        color: "#3b82f6",
      },
      {
        type: "text",
        x: 800,
        y: 800,
        text: "Separate note",
        color: "#ef4444",
      },
    ]);

    expect(screen.getByText("Visual target 1")).toBeInTheDocument();
    expect(screen.getAllByText("Separate note")).toHaveLength(1);
  });

  it("keeps generated labels above the video control strip near the bottom edge", () => {
    renderOverlay([
      {
        type: "rect",
        x: 780,
        y: 925,
        width: 190,
        height: 55,
        text: "Green liquid/block in cup",
        color: "#22c55e",
      },
    ]);

    const position = labelTranslation("Green liquid/block in cup");
    expect(position.y).toBeLessThanOrEqual(90);
  });

  it("keeps arrow labels inside the right edge instead of clipping them", () => {
    renderOverlay([
      {
        type: "arrow",
        x1: 975,
        y1: 610,
        x2: 995,
        y2: 960,
        text: "Pull downward",
        color: "#ef4444",
      },
    ]);

    const position = labelTranslation("Pull downward");
    expect(position.x).toBeLessThanOrEqual(86.8);
    expect(position.y).toBeLessThanOrEqual(90);
  });

  it("renders training polygons that use object-style points", () => {
    const { container } = renderOverlay([
      {
        type: "polygon",
        points: [
          { x: 120, y: 200 },
          { x: 260, y: 220 },
          { x: 230, y: 350 },
        ],
        text: "Tube opening",
        color: "#60a5fa",
      },
    ]);

    expect(container.querySelector("polygon")?.getAttribute("points")).toBe("12,20 26,22 23,35");
    expect(screen.getByText("Tube opening")).toBeInTheDocument();
  });
});
