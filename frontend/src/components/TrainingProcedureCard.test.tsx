import { fireEvent, render, screen } from "@testing-library/react";

import { TrainingProcedureCard } from "@/components/TrainingProcedureCard";
import type { TrainingProcedure } from "@/lib/types";

const procedure: TrainingProcedure = {
  title: "Machine startup",
  objective: "Learn how to start the machine safely.",
  prerequisites: ["Identify the emergency stop"],
  materials: ["Safety glasses"],
  safety_warnings: ["Keep hands clear"],
  manual_verified: true,
  steps: [
    {
      id: "step-1",
      title: "Inspect the switch",
      instruction: "Confirm the switch is off.",
      expected_result: "The switch is visibly off.",
      timestamp: 4,
      components: ["power switch"],
      warnings: [],
      annotations: [{ type: "rect", x: 100, y: 120, width: 80, height: 60, color: "#22c55e" }],
    },
    {
      id: "step-2",
      title: "Turn on power",
      instruction: "Move the switch to on.",
      timestamp: 8,
      components: ["power switch"],
      warnings: [],
      annotations: [],
      visual_status: "ready",
    },
  ],
};

describe("TrainingProcedureCard", () => {
  it("opens prepared steps instantly without making another annotation request", () => {
    const showStep = vi.fn();
    const regenerateStep = vi.fn().mockResolvedValue(undefined);
    render(
      <TrainingProcedureCard
        procedure={procedure}
        storageKey="training-test"
        onShowStep={showStep}
        onRegenerateStep={regenerateStep}
      />
    );

    expect(screen.getByText("Step 0 · Introduction")).toBeInTheDocument();
    expect(screen.queryByText("Turn on power")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("progressbar", { name: "Training progress" })).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByRole("progressbar", { name: "Visual guidance preparation" })).toHaveAttribute("aria-valuenow", "100");

    fireEvent.click(screen.getByRole("button", { name: "Start step 1" }));
    expect(screen.getByText("Inspect the switch")).toBeInTheDocument();
    expect(screen.queryByText("Turn on power")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next step" })).toBeDisabled();
    expect(showStep).toHaveBeenCalledWith(procedure.steps[0]);
    expect(regenerateStep).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Mark this step complete" }));
    expect(screen.getByRole("progressbar", { name: "Training progress" })).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByRole("button", { name: "Next step" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    expect(screen.getByText("Turn on power")).toBeInTheDocument();
    expect(screen.queryByText("Inspect the switch")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
    expect(showStep).toHaveBeenLastCalledWith(procedure.steps[1]);
    expect(regenerateStep).not.toHaveBeenCalled();
  });

  it("keeps the introduction available while step 1 prepares in the background", () => {
    const pendingProcedure: TrainingProcedure = {
      ...procedure,
      steps: [
        { ...procedure.steps[0], annotations: [], visual_status: "generating_annotation" },
        procedure.steps[1],
      ],
    };
    const showStep = vi.fn();
    render(
      <TrainingProcedureCard
        procedure={pendingProcedure}
        storageKey="training-pending-test"
        onShowStep={showStep}
        onRegenerateStep={vi.fn().mockImplementation(() => new Promise<void>(() => undefined))}
      />
    );

    expect(screen.getByText("Before you begin")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Preparing step 1/ })).toBeDisabled();
    expect(showStep).not.toHaveBeenCalled();
  });

  it("gates the next step only when its visual has not finished", () => {
    const preparingNext: TrainingProcedure = {
      ...procedure,
      steps: [
        { ...procedure.steps[0], visual_status: "ready" },
        { ...procedure.steps[1], annotations: [], visual_status: "generating_annotation" },
      ],
    };
    const showStep = vi.fn();
    const props = {
      storageKey: "training-next-prefetch-test",
      onShowStep: showStep,
      onRegenerateStep: vi.fn().mockResolvedValue(undefined),
    };
    const { rerender } = render(<TrainingProcedureCard procedure={preparingNext} {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "Start step 1" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Mark this step complete" }));
    expect(screen.getByRole("button", { name: /Getting next step ready/ })).toBeDisabled();

    const readyNext: TrainingProcedure = {
      ...preparingNext,
      steps: preparingNext.steps.map((step, index) => index === 1
        ? { ...step, annotations: [{ type: "rect", x: 200, y: 200, width: 80, height: 60 }], visual_status: "ready" }
        : step),
    };
    rerender(<TrainingProcedureCard procedure={readyNext} {...props} />);

    expect(screen.getByRole("button", { name: "Next step" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    expect(screen.getByText("Turn on power")).toBeInTheDocument();
    expect(showStep).toHaveBeenLastCalledWith(readyNext.steps[1]);
  });

  it("celebrates only when the final step is newly completed", () => {
    render(
      <TrainingProcedureCard
        procedure={procedure}
        storageKey="training-confetti-test"
        onShowStep={vi.fn()}
        onRegenerateStep={vi.fn().mockResolvedValue(undefined)}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Start step 1" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Mark this step complete" }));
    expect(screen.queryByTestId("training-confetti")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Mark this step complete" }));
    const confetti = screen.getByTestId("training-confetti");
    expect(confetti).toBeInTheDocument();
    expect(confetti.querySelectorAll('[data-side="left"]')).toHaveLength(24);
    expect(confetti.querySelectorAll('[data-side="right"]')).toHaveLength(24);
  });
});
