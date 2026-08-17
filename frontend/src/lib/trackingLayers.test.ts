import {
  colorizeTrackingTargets,
  createTrackingLayers,
  visibleOverlaysAtTime,
} from "@/lib/trackingLayers";
import type { TrackingOverlay } from "@/lib/types";

function overlay(trackId: string, timestamp: number, pointsOffset = 0): TrackingOverlay {
  return {
    track_id: trackId,
    label: trackId,
    color: "#000000",
    timestamp,
    points: [
      { x: pointsOffset, y: 0 },
      { x: 10 + pointsOffset, y: 0 },
      { x: 10 + pointsOffset, y: 10 },
    ],
  };
}

describe("tracking layers", () => {
  it("uses one machine annotation color for progress metadata and the finished mask", () => {
    const [target] = colorizeTrackingTargets({
      targets: [
        {
          id: "lead",
          label: "Extension lead",
          prompt: "white extension lead",
          annotations: [
            { type: "rect", x: 10, y: 20, width: 30, height: 10, color: "#ffd43b" },
          ],
        },
      ],
      annotations: [],
    });
    const coloredOverlay = {
      ...overlay("lead:sam3-1", 1),
      target_id: "lead",
      target_label: "Extension lead",
      target_color: target.color,
    };
    const [layer] = createTrackingLayers({
      jobId: "job-color",
      round: 1,
      prompt: target.prompt,
      overlays: [coloredOverlay],
    });

    expect(target.color).toBe("#ffd43b");
    expect(target.annotations[0]).toMatchObject({
      color: "#ffd43b",
      tracking_target_id: "lead",
    });
    expect(layer.color).toBe("#ffd43b");
    expect(layer.overlays[0].color).toBe("#ffd43b");
  });

  it("groups every polygon belonging to one SAM3 object into one removable layer", () => {
    const layers = createTrackingLayers({
      jobId: "job-1",
      round: 1,
      prompt: "Track the controls",
      overlays: [overlay("sam3-1", 1), overlay("sam3-1", 1, 20), overlay("sam3-2", 1)],
    });

    expect(layers).toHaveLength(2);
    expect(layers[0].overlays).toHaveLength(2);
    expect(layers.map((layer) => layer.label)).toEqual([
      "Track the controls - Object 1",
      "Track the controls - Object 2",
    ]);
    expect(layers[0].color).not.toBe(layers[1].color);
  });

  it("keeps one stable class layer when SAM3 object IDs change", () => {
    const first = {
      ...overlay("ams:frame-object-4", 1),
      target_id: "ams",
      target_label: "AMS unit",
      class_id: 1,
    };
    const second = {
      ...overlay("ams:frame-object-9", 1.1),
      target_id: "ams",
      target_label: "AMS unit",
      class_id: 1,
    };

    const layers = createTrackingLayers({
      jobId: "job-classes",
      round: 1,
      prompt: "the man and black AMS unit",
      overlays: [first, second],
    });

    expect(layers).toHaveLength(1);
    expect(layers[0].label).toBe("AMS unit");
    expect(layers[0].targetId).toBe("ams");
    expect(new Set(layers[0].overlays.map((item) => item.color))).toEqual(
      new Set([layers[0].color])
    );
  });

  it("renders the nearest frame only from visible layers", () => {
    const [first] = createTrackingLayers({
      jobId: "job-1",
      round: 1,
      prompt: "Lever",
      overlays: [overlay("sam3-1", 1), overlay("sam3-1", 1.1)],
    });
    const [second] = createTrackingLayers({
      jobId: "job-2",
      round: 2,
      prompt: "Hand",
      overlays: [overlay("sam3-2", 1.1)],
      colorOffset: 1,
    });

    expect(visibleOverlaysAtTime([first, second], 1.09)).toHaveLength(2);
    expect(visibleOverlaysAtTime([first, { ...second, visible: false }], 1.09)).toHaveLength(1);
    expect(visibleOverlaysAtTime([first, second], 3)).toEqual([]);
  });
});
