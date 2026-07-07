import type { Annotation } from "@/lib/types";

interface ParsedModelResponse {
  answer: string;
  annotations: Annotation[];
}

function parseAnnotationArray(value: unknown): Annotation[] {
  if (!Array.isArray(value)) return [];
  return value.filter((annotation): annotation is Annotation => {
    return (
      typeof annotation === "object" &&
      annotation !== null &&
      "type" in annotation &&
      typeof (annotation as { type?: unknown }).type === "string"
    );
  });
}

function responseFromJson(value: unknown, fallback: string): ParsedModelResponse | null {
  if (!value || typeof value !== "object") return null;
  const data = value as { answer?: unknown; annotations?: unknown };
  if (data.answer === undefined && data.annotations === undefined) return null;
  return {
    answer: typeof data.answer === "string" ? data.answer : fallback,
    annotations: parseAnnotationArray(data.annotations),
  };
}

export function parseModelResponse(raw: string): ParsedModelResponse {
  if (!raw) return { answer: "", annotations: [] };

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

  return { answer: raw, annotations: [] };
}
