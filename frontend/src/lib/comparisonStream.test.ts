import { normalizeComparisonEvent } from "@/lib/comparisonStream";

describe("normalizeComparisonEvent", () => {
  it("parses typed delta and nested answer events", () => {
    expect(
      normalizeComparisonEvent(
        "answer_delta",
        JSON.stringify({ comparison_id: "cmp-1", label: "A", delta: "hello" })
      )
    ).toEqual({ type: "answer_delta", label: "A", delta: "hello" });

    expect(
      normalizeComparisonEvent(
        "answer_complete",
        JSON.stringify({
          label: "B",
          answer: {
            text: "Use the reset switch.",
            provenance: "document",
            citations: [{ citation_id: "c1", source_kind: "document", page: 3 }],
          },
        })
      )
    ).toMatchObject({
      type: "answer_complete",
      label: "B",
      answer: { text: "Use the reset switch.", provenance: "document" },
    });
  });

  it("ignores malformed or unblinded labels", () => {
    expect(
      normalizeComparisonEvent("answer_delta", JSON.stringify({ label: "text_rag", delta: "x" }))
    ).toBeNull();
    expect(normalizeComparisonEvent("answer_delta", "plain text")).toBeNull();
  });
});
