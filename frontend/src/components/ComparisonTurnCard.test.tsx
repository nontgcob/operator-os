import { fireEvent, render, screen } from "@testing-library/react";
import { ComparisonTurnCard } from "@/components/ComparisonTurnCard";
import { createComparisonTurn } from "@/lib/comparisonState";

describe("ComparisonTurnCard", () => {
  it("shows citations, records a choice separately, and keeps pipelines blinded", () => {
    const choose = vi.fn();
    const turn = createComparisonTurn();
    turn.status = "complete";
    turn.answers.A = {
      ...turn.answers.A,
      status: "complete",
      text: "Press reset.",
      provenance: "document",
      citations: [
        {
          citation_id: "c-1",
          source_kind: "document",
          filename: "manual.pdf",
          page: 7,
          excerpt: "Press the reset switch.",
        },
      ],
    };
    turn.answers.B = {
      ...turn.answers.B,
      status: "complete",
      text: "Power cycle.",
      provenance: "model_knowledge",
    };

    render(
      <ComparisonTurnCard
        turn={turn}
        revealing={false}
        onSelect={choose}
        onReveal={vi.fn()}
        onRetry={vi.fn()}
      />
    );

    expect(screen.getByText("manual.pdf · page 7")).toBeInTheDocument();
    expect(screen.queryByText(/Multimodal RAG/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reveal pipelines" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Choose Answer A" }));
    expect(choose).toHaveBeenCalledWith("A");
  });
});
