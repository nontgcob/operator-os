import type {
  Annotation,
  DocumentCitation,
  TrackingTarget,
  TrainingProcedure,
  TrainingStep,
  VideoMoment,
} from "@/lib/types";

interface ParsedModelResponse {
  answer: string;
  annotations: Annotation[];
  trackingPrompt: string;
  trackingAnnotations: Annotation[];
  trackingTargets: TrackingTarget[];
  citations: DocumentCitation[];
  videoMoments: VideoMoment[];
  trainingProcedure: TrainingProcedure | null;
}

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
}

function parseStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(cleanString).filter(Boolean) : [];
}

function parseCitations(value: unknown): DocumentCitation[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry, index) => {
    if (!entry || typeof entry !== "object") return [];
    const citation = entry as Record<string, unknown>;
    const documentId = cleanString(citation.document_id);
    const filename = cleanString(citation.filename);
    if (!documentId || !filename) return [];
    const page = isFiniteNumber(citation.page) && citation.page >= 1 ? Math.floor(citation.page) : null;
    return [{
      citation_id: cleanString(citation.citation_id) || `document-citation-${index + 1}`,
      document_id: documentId,
      filename,
      page,
      section: cleanString(citation.section),
      excerpt: cleanString(citation.excerpt),
    }];
  });
}

function parseVideoMoments(value: unknown): VideoMoment[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const moment = entry as Record<string, unknown>;
    if (!isFiniteNumber(moment.timestamp) || moment.timestamp < 0) return [];
    const sourceValues = new Set(["video_index", "transcript", "tracking", "annotation"]);
    const confidenceValues = new Set(["high", "medium", "low"]);
    const source = sourceValues.has(String(moment.source))
      ? String(moment.source) as VideoMoment["source"]
      : "video_index";
    const confidence = confidenceValues.has(String(moment.confidence))
      ? String(moment.confidence) as VideoMoment["confidence"]
      : "medium";
    return [{
      timestamp: moment.timestamp,
      end_timestamp: isFiniteNumber(moment.end_timestamp) ? moment.end_timestamp : null,
      label: cleanString(moment.label) || "Relevant video moment",
      reason: cleanString(moment.reason),
      source,
      confidence,
    }];
  });
}

function parseTrainingStep(value: unknown, index: number): TrainingStep | null {
  if (!value || typeof value !== "object") return null;
  const step = value as Record<string, unknown>;
  const title = cleanString(step.title);
  const instruction = cleanString(step.instruction);
  if (!title || !instruction) return null;
  return {
    id: cleanString(step.id) || `step-${index + 1}`,
    title,
    instruction,
    expected_result: cleanString(step.expected_result),
    timestamp: isFiniteNumber(step.timestamp) && step.timestamp >= 0 ? step.timestamp : null,
    end_timestamp: isFiniteNumber(step.end_timestamp) && step.end_timestamp >= 0 ? step.end_timestamp : null,
    document_id: cleanString(step.document_id),
    filename: cleanString(step.filename),
    page: isFiniteNumber(step.page) && step.page >= 1 ? Math.floor(step.page) : null,
    section: cleanString(step.section),
    components: parseStringArray(step.components),
    warnings: parseStringArray(step.warnings),
  };
}

function parseTrainingProcedure(value: unknown): TrainingProcedure | null {
  if (!value || typeof value !== "object") return null;
  const procedure = value as Record<string, unknown>;
  const title = cleanString(procedure.title);
  const steps = Array.isArray(procedure.steps)
    ? procedure.steps.map(parseTrainingStep).filter((step): step is TrainingStep => Boolean(step))
    : [];
  if (!title || !steps.length) return null;
  return {
    title,
    objective: cleanString(procedure.objective),
    prerequisites: parseStringArray(procedure.prerequisites),
    materials: parseStringArray(procedure.materials),
    safety_warnings: parseStringArray(procedure.safety_warnings),
    manual_verified: procedure.manual_verified === true,
    steps,
  };
}

function parseTrackingTargets(value: unknown): TrackingTarget[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry, index) => {
    if (!entry || typeof entry !== "object") return [];
    const target = entry as {
      id?: unknown;
      label?: unknown;
      prompt?: unknown;
      annotations?: unknown;
      color?: unknown;
    };
    const label = typeof target.label === "string" ? target.label.replace(/\s+/g, " ").trim() : "";
    const prompt = typeof target.prompt === "string" ? target.prompt.replace(/\s+/g, " ").trim() : "";
    if (!label || !prompt) return [];
    return [{
      id: typeof target.id === "string" && target.id.trim() ? target.id.trim() : `target-${index + 1}`,
      label,
      prompt,
      annotations: parseAnnotationArray(target.annotations),
      ...(typeof target.color === "string" ? { color: target.color } : {}),
    }];
  });
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

function normalizeModelAnnotation(value: unknown): unknown {
  if (!value || typeof value !== "object") return value;
  const candidate = value as Record<string, unknown>;
  const color = typeof candidate.color === "string" ? candidate.color : "#8b5cf6";
  const coordinates = Array.isArray(candidate.coordinates) ? candidate.coordinates : null;
  if (candidate.type === "rect" && coordinates?.length === 4 && coordinates.every(isFiniteNumber)) {
    const [x1, y1, x2, y2] = coordinates as number[];
    return { ...candidate, color, x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
  }
  if (candidate.type === "arrow" && coordinates?.length === 4 && coordinates.every(isFiniteNumber)) {
    const [x1, y1, x2, y2] = coordinates as number[];
    return { ...candidate, color, x1, y1, x2, y2 };
  }
  const box = Array.isArray(candidate.box_2d) ? candidate.box_2d : null;
  if (candidate.type === "rect" && box?.length === 4 && box.every(isFiniteNumber)) {
    const [y1, x1, y2, x2] = box as number[];
    return { ...candidate, color, x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
  }
  if (
    candidate.type === "rect" &&
    [candidate.x_min, candidate.y_min, candidate.x_max, candidate.y_max].every(isFiniteNumber)
  ) {
    const x1 = candidate.x_min as number;
    const y1 = candidate.y_min as number;
    return {
      ...candidate,
      color,
      x: x1,
      y: y1,
      width: (candidate.x_max as number) - x1,
      height: (candidate.y_max as number) - y1,
    };
  }
  return { ...candidate, color };
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
  return value.map(normalizeModelAnnotation).filter(isValidAnnotation);
}

function repairBareTokenBeforeObjectKey(candidate: string): string {
  let repaired = "";
  let inString = false;
  let escaped = false;
  for (let index = 0; index < candidate.length; index += 1) {
    const char = candidate[index];
    if (inString) {
      repaired += char;
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') {
      inString = true;
      repaired += char;
      continue;
    }
    if (char !== ",") {
      repaired += char;
      continue;
    }

    repaired += char;
    let tokenStart = index + 1;
    while (/\s/.test(candidate[tokenStart] ?? "")) tokenStart += 1;
    if (!/[A-Za-z_]/.test(candidate[tokenStart] ?? "")) continue;
    let tokenEnd = tokenStart + 1;
    while (/[A-Za-z0-9_-]/.test(candidate[tokenEnd] ?? "")) tokenEnd += 1;
    let keyStart = tokenEnd;
    while (/\s/.test(candidate[keyStart] ?? "")) keyStart += 1;
    if (candidate[keyStart] !== '"') continue;

    let keyEnd = keyStart + 1;
    let keyEscaped = false;
    for (; keyEnd < candidate.length; keyEnd += 1) {
      const keyChar = candidate[keyEnd];
      if (keyEscaped) keyEscaped = false;
      else if (keyChar === "\\") keyEscaped = true;
      else if (keyChar === '"') break;
    }
    let colonIndex = keyEnd + 1;
    while (/\s/.test(candidate[colonIndex] ?? "")) colonIndex += 1;
    if (candidate[colonIndex] !== ":") continue;

    repaired += candidate.slice(index + 1, tokenStart);
    index = keyStart - 1;
  }
  return repaired;
}

function parseJsonCandidate(candidate: string): unknown {
  try {
    return JSON.parse(candidate);
  } catch {
    const repaired = repairBareTokenBeforeObjectKey(candidate);
    if (repaired === candidate) throw new Error("Invalid model JSON");
    return JSON.parse(repaired);
  }
}

function responseFromJson(value: unknown, fallback: string): ParsedModelResponse | null {
  if (!value || typeof value !== "object") return null;
  const data = value as {
    answer?: unknown;
    annotations?: unknown;
    tracking_prompt?: unknown;
    tracking_annotations?: unknown;
    tracking_targets?: unknown;
    citations?: unknown;
    video_moments?: unknown;
    training_procedure?: unknown;
  };
  if (
    data.answer === undefined &&
    data.annotations === undefined &&
    data.tracking_prompt === undefined &&
    data.tracking_annotations === undefined &&
    data.tracking_targets === undefined &&
    data.citations === undefined &&
    data.video_moments === undefined &&
    data.training_procedure === undefined
  ) {
    return null;
  }
  return {
    answer: typeof data.answer === "string" ? data.answer : fallback,
    annotations: parseAnnotationArray(data.annotations),
    trackingPrompt: typeof data.tracking_prompt === "string" ? data.tracking_prompt : "",
    trackingAnnotations: parseAnnotationArray(data.tracking_annotations),
    trackingTargets: parseTrackingTargets(data.tracking_targets),
    citations: parseCitations(data.citations),
    videoMoments: parseVideoMoments(data.video_moments),
    trainingProcedure: parseTrainingProcedure(data.training_procedure),
  };
}

const EMPTY_RESPONSE: ParsedModelResponse = {
  answer: "",
  annotations: [],
  trackingPrompt: "",
  trackingAnnotations: [],
  trackingTargets: [],
  citations: [],
  videoMoments: [],
  trainingProcedure: null,
};

export function parseModelResponse(raw: string): ParsedModelResponse {
  if (!raw) return { ...EMPTY_RESPONSE };

  const fenceMatch = raw.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
  if (fenceMatch) {
    try {
      const parsed = responseFromJson(parseJsonCandidate(fenceMatch[1]), raw);
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
            const parsed = responseFromJson(parseJsonCandidate(raw.slice(start, index + 1)), raw);
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
    const parsed = responseFromJson(parseJsonCandidate(raw.trim()), raw);
    if (parsed) return parsed;
  } catch {
    // Non-JSON fallback.
  }

  const recoveredAnswer = partialAnswerFromModelResponse(raw);
  const looksStructured = raw.trimStart().startsWith("{") || raw.trimStart().startsWith("```");
  return {
    ...EMPTY_RESPONSE,
    answer: recoveredAnswer || (looksStructured
      ? "The response arrived in an invalid internal format. Please retry the question."
      : raw),
  };
}

export function partialAnswerFromModelResponse(raw: string): string {
  const match = /"answer"\s*:\s*"/.exec(raw);
  if (!match) return raw.trimStart().startsWith("{") ? "" : raw;

  let result = "";
  let escaped = false;
  for (let index = match.index + match[0].length; index < raw.length; index += 1) {
    const char = raw[index];
    if (escaped) {
      const escapes: Record<string, string> = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        b: "\b",
        f: "\f",
        n: "\n",
        r: "\r",
        t: "\t",
      };
      if (char === "u") {
        const code = raw.slice(index + 1, index + 5);
        if (/^[0-9a-f]{4}$/i.test(code)) {
          result += String.fromCharCode(Number.parseInt(code, 16));
          index += 4;
        }
      } else {
        result += escapes[char] ?? char;
      }
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (char === '"') break;
    result += char;
  }
  return result;
}
