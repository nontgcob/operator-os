import type { TrackingLayer, TrackingOverlay } from "@/lib/types";

export const TRACKING_LAYER_COLORS = [
  "#22c55e",
  "#8b5cf6",
  "#0ea5e9",
  "#f97316",
  "#ec4899",
  "#eab308",
  "#14b8a6",
  "#ef4444",
];

function compactLabel(value: string) {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) return "Tracked object";
  return normalized.length > 56 ? `${normalized.slice(0, 53)}...` : normalized;
}

export function createTrackingLayers(input: {
  jobId: string;
  round: number;
  prompt: string;
  overlays: TrackingOverlay[];
  colorOffset?: number;
}): TrackingLayer[] {
  const grouped = new Map<string, TrackingOverlay[]>();
  for (const overlay of input.overlays) {
    const existing = grouped.get(overlay.track_id) ?? [];
    existing.push(overlay);
    grouped.set(overlay.track_id, existing);
  }

  const baseLabel = compactLabel(input.prompt);
  const multipleObjects = grouped.size > 1;
  return Array.from(grouped.entries()).map(([trackId, overlays], index) => {
    const color = TRACKING_LAYER_COLORS[((input.colorOffset ?? 0) + index) % TRACKING_LAYER_COLORS.length];
    return {
      id: `${input.jobId}:${trackId}`,
      jobId: input.jobId,
      round: input.round,
      label: multipleObjects ? `${baseLabel} - Object ${index + 1}` : baseLabel,
      color,
      visible: true,
      overlays: overlays
        .map((overlay) => ({ ...overlay, color }))
        .sort((a, b) => a.timestamp - b.timestamp),
    };
  });
}

function lowerBound(overlays: TrackingOverlay[], timestamp: number) {
  let low = 0;
  let high = overlays.length - 1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if (overlays[middle].timestamp < timestamp) low = middle + 1;
    else high = middle - 1;
  }
  return low;
}

function nearestTimestamp(overlays: TrackingOverlay[], timestamp: number) {
  const low = lowerBound(overlays, timestamp);
  const candidates = [overlays[low], overlays[low - 1]].filter(Boolean) as TrackingOverlay[];
  return candidates.reduce<TrackingOverlay | null>((nearest, candidate) => {
    if (!nearest) return candidate;
    return Math.abs(candidate.timestamp - timestamp) < Math.abs(nearest.timestamp - timestamp)
      ? candidate
      : nearest;
  }, null)?.timestamp;
}

export function visibleOverlaysAtTime(
  layers: TrackingLayer[],
  timestamp: number,
  toleranceSeconds = 0.1
): TrackingOverlay[] {
  return layers.flatMap((layer) => {
    if (!layer.visible || !layer.overlays.length) return [];
    const nearest = nearestTimestamp(layer.overlays, timestamp);
    if (nearest === undefined || Math.abs(nearest - timestamp) > toleranceSeconds) return [];
    const start = lowerBound(layer.overlays, nearest - 0.0001);
    const frameOverlays: TrackingOverlay[] = [];
    for (let index = start; index < layer.overlays.length; index += 1) {
      const overlay = layer.overlays[index];
      if (Math.abs(overlay.timestamp - nearest) >= 0.0001) break;
      frameOverlays.push(overlay);
    }
    return frameOverlays;
  });
}
