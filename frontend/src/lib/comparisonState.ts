import type {
  AnswerLabel,
  ComparisonAnswer,
  ComparisonStreamEvent,
  ComparisonTurn,
} from "@/lib/types";

export function emptyComparisonAnswer(label: AnswerLabel): ComparisonAnswer {
  return {
    label,
    status: "pending",
    text: "",
    citations: [],
    annotations: [],
    tracking_annotations: [],
  };
}

export function createComparisonTurn(): ComparisonTurn {
  return {
    status: "streaming",
    answers: {
      A: emptyComparisonAnswer("A"),
      B: emptyComparisonAnswer("B"),
    },
    revealed: false,
  };
}

function sanitizeAnswer(
  current: ComparisonAnswer,
  update: Partial<ComparisonAnswer>
): ComparisonAnswer {
  return {
    answer_id: update.answer_id ?? current.answer_id,
    label: current.label,
    status: "complete",
    text: update.text ?? current.text,
    provenance: update.provenance ?? current.provenance,
    citations: Array.isArray(update.citations) ? update.citations : current.citations,
    annotations: Array.isArray(update.annotations) ? update.annotations : current.annotations,
    tracking_prompt: update.tracking_prompt ?? current.tracking_prompt,
    tracking_annotations: Array.isArray(update.tracking_annotations)
      ? update.tracking_annotations
      : current.tracking_annotations,
    error: update.error,
    // A pipeline identity received before reveal is ignored defensively.
    pipeline: undefined,
  };
}

export function reduceComparisonEvent(
  turn: ComparisonTurn,
  event: ComparisonStreamEvent
): ComparisonTurn {
  if (event.type === "comparison_started") {
    return { ...turn, comparison_id: event.comparison_id };
  }
  if (event.type === "answer_delta") {
    const answer = turn.answers[event.label];
    return {
      ...turn,
      answers: {
        ...turn.answers,
        [event.label]: {
          ...answer,
          status: "streaming",
          text: answer.text + event.delta,
        },
      },
    };
  }
  if (event.type === "answer_complete") {
    return {
      ...turn,
      answers: {
        ...turn.answers,
        [event.label]: sanitizeAnswer(turn.answers[event.label], event.answer),
      },
    };
  }
  if (event.type === "answer_error") {
    return {
      ...turn,
      status: "partial",
      retryable: true,
      answers: {
        ...turn.answers,
        [event.label]: {
          ...turn.answers[event.label],
          status: "error",
          error: event.message,
        },
      },
    };
  }
  const statuses = Object.values(turn.answers).map((answer) => answer.status);
  const hasError = statuses.includes("error");
  return {
    ...turn,
    comparison_id: event.comparison_id ?? turn.comparison_id,
    status: hasError ? "partial" : "complete",
    retryable: hasError,
  };
}

export function canVote(turn: ComparisonTurn): boolean {
  return (
    turn.status === "complete" &&
    !turn.revealed &&
    turn.answers.A.status === "complete" &&
    turn.answers.B.status === "complete"
  );
}

export function revealTurn(
  turn: ComparisonTurn,
  mapping: Record<AnswerLabel, string>
): ComparisonTurn {
  return {
    ...turn,
    status: "revealed",
    revealed: true,
    reveal_error: undefined,
    answers: {
      A: { ...turn.answers.A, pipeline: mapping.A },
      B: { ...turn.answers.B, pipeline: mapping.B },
    },
  };
}

export function comparisonExportLines(turn: ComparisonTurn): string[] {
  const lines = ["### Blinded answer comparison", ""];
  for (const label of ["A", "B"] as const) {
    const answer = turn.answers[label];
    const pipeline = turn.revealed && answer.pipeline ? ` (${answer.pipeline})` : "";
    lines.push(`#### Answer ${label}${pipeline}`, "", answer.text || answer.error || "No answer.", "");
    if (answer.citations.length) {
      lines.push(
        "Sources:",
        ...answer.citations.map((citation) => {
          const page = citation.page ? `, page ${citation.page}` : "";
          const location = citation.filename ?? citation.source_kind;
          return `- ${location}${page}${citation.excerpt ? ` — ${citation.excerpt}` : ""}`;
        }),
        ""
      );
    }
  }
  if (turn.selected_label) lines.push(`Selected answer: ${turn.selected_label}`, "");
  if (!turn.revealed) lines.push("Pipeline mapping: not revealed", "");
  return lines;
}
