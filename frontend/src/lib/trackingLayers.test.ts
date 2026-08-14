import { createTrackingLayers, visibleOverlaysAtTime } from "@/lib/trackingLayers";
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
