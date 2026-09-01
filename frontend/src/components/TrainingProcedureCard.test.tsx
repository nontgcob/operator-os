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
  it("starts at step 0 and reveals one gated step at a time", () => {
    const showStep = vi.fn();
    render(
      <TrainingProcedureCard
        procedure={procedure}
        storageKey="training-test"
        onShowStep={showStep}
        onRegenerateStep={vi.fn().mockResolvedValue(undefined)}
      />
    );

    expect(screen.getByText("Step 0 · Introduction")).toBeInTheDocument();
    expect(screen.queryByText("Turn on power")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("progressbar", { name: "Training progress" })).toHaveAttribute("aria-valuenow", "0");

    fireEvent.click(screen.getByRole("button", { name: "Start step 1" }));
    expect(screen.getByText("Inspect the switch")).toBeInTheDocument();
    expect(screen.queryByText("Turn on power")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next step" })).toBeDisabled();
    expect(showStep).toHaveBeenCalledWith(procedure.steps[0]);

    fireEvent.click(screen.getByRole("checkbox", { name: "Mark this step complete" }));
    expect(screen.getByRole("progressbar", { name: "Training progress" })).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByRole("button", { name: "Next step" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    expect(screen.getByText("Turn on power")).toBeInTheDocument();
    expect(screen.queryByText("Inspect the switch")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
    expect(showStep).toHaveBeenLastCalledWith(procedure.steps[1]);
  });

  it("shows a disabled loading action while the active step visual is still generating", () => {
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
        onRegenerateStep={vi.fn().mockResolvedValue(undefined)}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Start step 1" }));

    expect(screen.getByText("Generating annotation")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Preparing visual/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Mark this step complete" })).toBeDisabled();
    expect(showStep).not.toHaveBeenCalled();
  });
});
