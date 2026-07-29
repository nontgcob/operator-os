import type {
  AnswerLabel,
  ComparisonAnswer,
  ComparisonStreamEvent,
} from "@/lib/types";

function isLabel(value: unknown): value is AnswerLabel {
  return value === "A" || value === "B";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function decodePayload(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export function normalizeComparisonEvent(
  eventType: string,
  rawData: string
): ComparisonStreamEvent | null {
  if (rawData.trim() === "[DONE]") return null;
  const decoded = decodePayload(rawData);
  const payload = asRecord(decoded);
  const type = (eventType === "message" && typeof payload.type === "string"
    ? payload.type
    : eventType) as ComparisonStreamEvent["type"];

  if (type === "comparison_started") {
    const comparisonId = payload.comparison_id;
    return typeof comparisonId === "string"
      ? { type, comparison_id: comparisonId }
      : null;
  }

  const label = payload.label;
  if ((type === "answer_delta" || type === "answer_complete" || type === "answer_error") && !isLabel(label)) {
    return null;
  }

  if (type === "answer_delta") {
    const delta = payload.delta ?? payload.text ?? payload.content;
    return typeof delta === "string" ? { type, label: label as AnswerLabel, delta } : null;
  }

  if (type === "answer_complete") {
    const nestedAnswer = asRecord(payload.answer);
    const source = Object.keys(nestedAnswer).length ? nestedAnswer : payload;
    const answer = {
      answer_id: typeof source.answer_id === "string" ? source.answer_id : undefined,
      label: label as AnswerLabel,
      text: typeof source.text === "string" ? source.text : undefined,
      provenance: source.provenance,
      citations: source.citations,
      annotations: source.annotations,
      tracking_prompt: source.tracking_prompt,
      tracking_annotations: source.tracking_annotations,
      error: typeof source.error === "string" ? source.error : undefined,
    } as Partial<ComparisonAnswer>;
    return { type, label: label as AnswerLabel, answer };
  }

  if (type === "answer_error") {
    const error = asRecord(payload.error);
    const message = payload.message ?? error.message ?? payload.detail;
    return {
      type,
      label: label as AnswerLabel,
      message: typeof message === "string" ? message : "This answer is unavailable.",
    };
  }

  if (type === "comparison_complete") {
    return {
      type,
      comparison_id:
        typeof payload.comparison_id === "string" ? payload.comparison_id : undefined,
    };
  }

  return null;
}

export async function readComparisonSSE(
  response: Response,
  onEvent: (event: ComparisonStreamEvent) => void
): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Missing comparison stream body");
  const decoder = new TextDecoder();
  let buffer = "";

  function processEvent(rawEvent: string) {
    let eventType = "message";
    const data: string[] = [];
    for (const rawLine of rawEvent.split(/\r?\n/)) {
      const line = rawLine.trimEnd();
      if (line.startsWith("event:")) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        data.push(line.slice(5).trimStart());
      }
    }
    const event = normalizeComparisonEvent(eventType, data.join("\n"));
    if (event) onEvent(event);
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split(/\r?\n\r?\n/);
    buffer = events.pop() ?? "";
    events.forEach(processEvent);
  }
  buffer += decoder.decode();
  if (buffer.trim()) processEvent(buffer);
}
