import { excludeTrackedTargets, trackingLabelsMatch } from "@/lib/trackingDedup";
import type { TrackingTarget } from "@/lib/types";

function target(label: string): TrackingTarget {
  return { id: label, label, prompt: label, annotations: [] };
}

describe("tracking target deduplication", () => {
  it("matches harmless label variations", () => {
    expect(trackingLabelsMatch("The red power switches", "Red power switch")).toBe(true);
  });

  it("keeps genuinely different objects", () => {
    expect(trackingLabelsMatch("Power socket", "Power switch")).toBe(false);
  });

  it("excludes active or completed targets and leaves new targets", () => {
    const result = excludeTrackedTargets(
      [target("AMS unit"), target("Power socket")],
      ["Black AMS unit"]
    );

    expect(result.duplicates.map((item) => item.label)).toEqual(["AMS unit"]);
    expect(result.targets.map((item) => item.label)).toEqual(["Power socket"]);
  });
});
