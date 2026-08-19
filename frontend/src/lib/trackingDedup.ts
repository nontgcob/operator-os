import type { TrackingTarget } from "@/lib/types";

const IGNORED_WORDS = new Set(["a", "an", "the", "track", "tracking", "follow", "monitor", "trace"]);

function singularize(word: string): string {
  if (word.length > 4 && word.endsWith("ies")) return `${word.slice(0, -3)}y`;
  if (word.length > 4 && /(ches|shes|sses|xes|zes)$/.test(word)) return word.slice(0, -2);
  if (word.length > 3 && word.endsWith("s") && !word.endsWith("ss")) return word.slice(0, -1);
  return word;
}

export function normalizeTrackingLabel(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter((word) => word && !IGNORED_WORDS.has(word))
    .map(singularize)
    .join(" ");
}

export function trackingLabelsMatch(left: string, right: string): boolean {
  const normalizedLeft = normalizeTrackingLabel(left);
  const normalizedRight = normalizeTrackingLabel(right);
  if (!normalizedLeft || !normalizedRight) return false;
  if (normalizedLeft === normalizedRight) return true;

  const leftWords = new Set(normalizedLeft.split(" "));
  const rightWords = new Set(normalizedRight.split(" "));
  const intersection = [...leftWords].filter((word) => rightWords.has(word)).length;
  const smaller = Math.min(leftWords.size, rightWords.size);
  const union = new Set([...leftWords, ...rightWords]).size;
  return smaller >= 2 && intersection === smaller && intersection / union >= 0.5;
}

export function excludeTrackedTargets(
  targets: TrackingTarget[],
  existingLabels: string[]
): { targets: TrackingTarget[]; duplicates: TrackingTarget[] } {
  const accepted: TrackingTarget[] = [];
  const duplicates: TrackingTarget[] = [];
  for (const target of targets) {
    const seen = [...existingLabels, ...accepted.map((candidate) => candidate.label)].some((label) =>
      trackingLabelsMatch(target.label, label)
    );
    (seen ? duplicates : accepted).push(target);
  }
  return { targets: accepted, duplicates };
}
