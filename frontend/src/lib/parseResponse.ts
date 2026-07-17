import type { Annotation } from "@/lib/types";

interface ParsedModelResponse {
  answer: string;
  annotations: Annotation[];
  trackingPrompt: string;
  trackingAnnotations: Annotation[];
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function inRange(value: unknown, min: number, max: number): value is number {
  return isFiniteNumber(value) && value >= min && value <= max;
}

function isPoint(value: unknown): value is { x: number; y: number } {
  return (
    typeof value === "object" &&
    value !== null &&
    inRange((value as { x?: unknown }).x, 0, 1000) &&
    inRange((value as { y?: unknown }).y, 0, 1000)
  );
}

function isPointTuple(value: unknown): value is number[] {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    inRange(value[0], 0, 1000) &&
    inRange(value[1], 0, 1000)
  );
}

function hasValidPoints(value: unknown, minPoints = 1): boolean {
  return (
    Array.isArray(value) &&
    value.length >= minPoints &&
    value.every((point) => isPoint(point) || isPointTuple(point))
  );
}

function isValidAnnotation(annotation: unknown): annotation is Annotation {
  if (typeof annotation !== "object" || annotation === null) return false;
  const candidate = annotation as Annotation;
  if (typeof candidate.type !== "string") return false;

  switch (candidate.type) {
    case "rect":
      return (
        inRange(candidate.x, 0, 1000) &&
        inRange(candidate.y, 0, 1000) &&
        inRange(candidate.width, 0, 1000) &&
        inRange(candidate.height, 0, 1000) &&
        (candidate.width ?? 0) > 0 &&
        (candidate.height ?? 0) > 0
      );
    case "circle":
      return (
        inRange(candidate.cx ?? candidate.x, 0, 1000) &&
        inRange(candidate.cy ?? candidate.y, 0, 1000) &&
        inRange(candidate.r ?? candidate.radius, 0, 1000) &&
        ((candidate.r ?? candidate.radius) ?? 0) > 0
      );
    case "arrow":
      return (
        inRange(candidate.x1, 0, 1000) &&
        inRange(candidate.y1, 0, 1000) &&
        inRange(candidate.x2, 0, 1000) &&
        inRange(candidate.y2, 0, 1000)
      );
    case "path":
      return typeof candidate.d === "string" || hasValidPoints(candidate.points, 2);
    case "polygon":
      return hasValidPoints(candidate.points, 3);
    case "text":
    case "number":
      return (
        inRange(candidate.x, 0, 1000) &&
        inRange(candidate.y, 0, 1000) &&
        (typeof candidate.text === "string" ||
          typeof candidate.content === "string" ||
          typeof candidate.value === "number")
      );
    default:
      return false;
  }
}

function parseAnnotationArray(value: unknown): Annotation[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isValidAnnotation);
}

function responseFromJson(value: unknown, fallback: string): ParsedModelResponse | null {
  if (!value || typeof value !== "object") return null;
  const data = value as {
    answer?: unknown;
    annotations?: unknown;
    tracking_prompt?: unknown;
    tracking_annotations?: unknown;
  };
  if (
    data.answer === undefined &&
    data.annotations === undefined &&
    data.tracking_prompt === undefined &&
    data.tracking_annotations === undefined
  ) {
    return null;
  }
  return {
    answer: typeof data.answer === "string" ? data.answer : fallback,
    annotations: parseAnnotationArray(data.annotations),
    trackingPrompt: typeof data.tracking_prompt === "string" ? data.tracking_prompt : "",
    trackingAnnotations: parseAnnotationArray(data.tracking_annotations),
  };
}

export function parseModelResponse(raw: string): ParsedModelResponse {
  if (!raw) return { answer: "", annotations: [], trackingPrompt: "", trackingAnnotations: [] };

  const fenceMatch = raw.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
  if (fenceMatch) {
    try {
      const parsed = responseFromJson(JSON.parse(fenceMatch[1]), raw);
      if (parsed) return parsed;
    } catch {
      // Try the next strategy.
    }
  }

  const start = raw.indexOf("{");
  if (start !== -1) {
    let depth = 0;
    let inString = false;
    let escape = false;

    for (let index = start; index < raw.length; index += 1) {
      const char = raw[index];
      if (escape) {
        escape = false;
        continue;
      }
      if (char === "\\") {
        escape = true;
        continue;
      }
      if (char === '"') {
        inString = !inString;
        continue;
      }
      if (inString) continue;

      if (char === "{") {
        depth += 1;
      } else if (char === "}") {
        depth -= 1;
        if (depth === 0) {
          try {
            const parsed = responseFromJson(JSON.parse(raw.slice(start, index + 1)), raw);
            if (parsed) return parsed;
          } catch {
            // Fall through to whole-string parsing.
          }
          break;
        }
      }
    }
  }

  try {
    const parsed = responseFromJson(JSON.parse(raw.trim()), raw);
    if (parsed) return parsed;
  } catch {
    // Non-JSON fallback.
  }

  return { answer: raw, annotations: [], trackingPrompt: "", trackingAnnotations: [] };
}
