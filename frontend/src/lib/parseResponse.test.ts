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

  it("parses pinpoint citations, video moments, and a training procedure", () => {
    const parsed = parseModelResponse(JSON.stringify({
      answer: "Follow the shutdown steps.",
      citations: [{
        citation_id: "c1",
        document_id: "manual-1",
        filename: "manual.pdf",
        page: 12,
        section: "Shutdown",
        excerpt: "Press stop before disconnecting power.",
      }],
      video_moments: [{
        timestamp: 8,
        label: "Stop button",
        reason: "The operator presses stop.",
        source: "video_index",
        confidence: "high",
      }],
      training_procedure: {
        title: "Safe shutdown",
        objective: "Power down safely.",
        prerequisites: ["Wear gloves"],
        materials: [],
        safety_warnings: ["Do not open energized panels"],
        manual_verified: true,
        steps: [{
          id: "step-1",
          title: "Press stop",
          instruction: "Press the stop button.",
          timestamp: 8,
          document_id: "manual-1",
          filename: "manual.pdf",
          page: 12,
          components: ["stop button"],
          warnings: [],
        }],
      },
    }));

    expect(parsed.citations[0]).toMatchObject({ filename: "manual.pdf", page: 12 });
    expect(parsed.videoMoments[0]).toMatchObject({ timestamp: 8, label: "Stop button" });
    expect(parsed.trainingProcedure?.steps[0]).toMatchObject({ id: "step-1", timestamp: 8 });
  });

  it("repairs a stray bare token without exposing raw JSON", () => {
    const parsed = parseModelResponse(
      '```json{"answer":"Rear view found.","annotations":[],"tracking_prompt":"",' +
      '"tracking_annotations":[],"tracking_targets":[],"citations":[],"video_moments":[],' +
      '"training_procedure":{"title":"Rear inspection","objective":"Inspect rear",' +
      '"prerequisites":[],"materials":[],"safety_warnings":[],"manual_verified":true,Box ' +
      '"steps":[{"id":"step-1","title":"Inspect","instruction":"Check the rear.",' +
      '"components":[],"warnings":[]}]}}```'
    );

    expect(parsed.answer).toBe("Rear view found.");
    expect(parsed.trainingProcedure?.title).toBe("Rear inspection");
  });

  it("normalizes Gemini coordinate arrays into drawable annotations", () => {
    const parsed = parseModelResponse(JSON.stringify({
      answer: "Marked.",
      annotations: [
        { type: "rect", coordinates: [100, 200, 350, 500], color: "#3b82f6" },
        { type: "arrow", coordinates: [10, 20, 30, 40], color: "#3b82f6" },
      ],
    }));

    expect(parsed.annotations).toEqual([
      expect.objectContaining({ type: "rect", x: 100, y: 200, width: 250, height: 300 }),
      expect.objectContaining({ type: "arrow", x1: 10, y1: 20, x2: 30, y2: 40 }),
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
