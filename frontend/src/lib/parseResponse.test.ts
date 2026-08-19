import { parseModelResponse, partialAnswerFromModelResponse } from "@/lib/parseResponse";

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
        tracking_targets: [
          {
            label: "Red handle",
            prompt: "red emergency handle",
            annotations: [
              { type: "rect", x: 200, y: 220, width: 90, height: 40, color: "#ff0000" },
            ],
          },
          { label: "", prompt: "invalid target", annotations: [] },
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
    expect(parsed.trackingTargets).toEqual([
      {
        id: "target-1",
        label: "Red handle",
        prompt: "red emergency handle",
        annotations: [
          expect.objectContaining({ type: "rect", x: 200, y: 220, width: 90, height: 40 }),
        ],
      },
    ]);
  });
});

describe("partialAnswerFromModelResponse", () => {
  it("extracts and decodes the answer while JSON is still streaming", () => {
    expect(partialAnswerFromModelResponse('{"answer":"First line\\nSecond')).toBe(
      "First line\nSecond"
    );
  });

  it("returns an empty string before the answer field arrives", () => {
    expect(partialAnswerFromModelResponse('{"annotations":[')).toBe("");
  });
});
