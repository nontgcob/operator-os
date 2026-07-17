import { parseModelResponse } from "@/lib/parseResponse";

describe("parseModelResponse", () => {
  it("keeps valid rect annotations and drops malformed ones", () => {
    const parsed = parseModelResponse(
      JSON.stringify({
        answer: "Found targets.",
        annotations: [
          { type: "rect", x: 100, y: 120, width: 80, height: 30, color: "#ff0000" },
          { type: "rect", x: 100, y: 120, width: -5, height: 30, color: "#00ff00" },
          { type: "circle", cx: 200, cy: 220, r: 0, color: "#0000ff" },
          { type: "polygon", points: [{ x: 1, y: 2 }, { x: 3, y: 4 }], color: "#fff" },
        ],
        tracking_prompt: "Track the red handle.",
        tracking_annotations: [
          { type: "rect", x: 200, y: 220, width: 90, height: 40, color: "#ff0000" },
          { type: "rect", x: 100, y: 120, width: -5, height: 30, color: "#00ff00" },
        ],
      })
    );

    expect(parsed.answer).toBe("Found targets.");
    expect(parsed.annotations).toHaveLength(1);
    expect(parsed.annotations[0]).toMatchObject({
      type: "rect",
      x: 100,
      y: 120,
      width: 80,
      height: 30,
    });
    expect(parsed.trackingPrompt).toBe("Track the red handle.");
    expect(parsed.trackingAnnotations).toHaveLength(1);
    expect(parsed.trackingAnnotations[0]).toMatchObject({
      type: "rect",
      x: 200,
      y: 220,
      width: 90,
      height: 40,
    });
  });
});
