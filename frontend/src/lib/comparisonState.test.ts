import {
  canVote,
  comparisonExportLines,
  createComparisonTurn,
  reduceComparisonEvent,
  revealTurn,
} from "@/lib/comparisonState";

describe("comparison state", () => {
  it("assembles interleaved answer chunks and enables voting only when both complete", () => {
    let turn = createComparisonTurn();
    turn = reduceComparisonEvent(turn, { type: "answer_delta", label: "A", delta: "one " });
    turn = reduceComparisonEvent(turn, { type: "answer_delta", label: "B", delta: "two" });
    turn = reduceComparisonEvent(turn, { type: "answer_delta", label: "A", delta: "answer" });
    turn = reduceComparisonEvent(turn, {
      type: "answer_complete",
      label: "A",
      answer: { text: "one answer", pipeline: "multimodal_rag" },
    });
    turn = reduceComparisonEvent(turn, {
      type: "answer_complete",
      label: "B",
      answer: { text: "two" },
    });
    turn = reduceComparisonEvent(turn, { type: "comparison_complete" });

    expect(turn.answers.A.text).toBe("one answer");
    expect(turn.answers.A.pipeline).toBeUndefined();
    expect(JSON.stringify(turn.answers.A)).not.toContain("multimodal_rag");
    expect(canVote(turn)).toBe(true);
  });

  it("disables voting after a partial failure", () => {
    let turn = createComparisonTurn();
    turn = reduceComparisonEvent(turn, {
      type: "answer_error",
      label: "B",
      message: "timed out",
    });
    turn = reduceComparisonEvent(turn, { type: "comparison_complete" });
    expect(turn.status).toBe("partial");
    expect(turn.retryable).toBe(true);
    expect(canVote(turn)).toBe(false);
  });

  it("does not export pipeline identities until reveal", () => {
    const hidden = {
      ...createComparisonTurn(),
      selected_label: "A" as const,
      answers: {
        A: { ...createComparisonTurn().answers.A, text: "A text", status: "complete" as const },
        B: { ...createComparisonTurn().answers.B, text: "B text", status: "complete" as const },
      },
    };
    expect(comparisonExportLines(hidden).join("\n")).not.toContain("text_rag");

    const revealed = revealTurn(hidden, { A: "text_rag", B: "multimodal_rag" });
    expect(comparisonExportLines(revealed).join("\n")).toContain("text_rag");
  });
});
