"use client";

import { useEffect, useMemo, useState } from "react";

import { DocumentCitations } from "@/components/DocumentCitations";
import type { DocumentCitation, TrainingProcedure, TrainingStep } from "@/lib/types";

export function TrainingProcedureCard({
  procedure,
  storageKey,
  onShowStep,
  onRegenerateStep,
}: {
  procedure: TrainingProcedure;
  storageKey: string;
  onShowStep: (step: TrainingStep) => void | Promise<void>;
  onRegenerateStep: (step: TrainingStep) => Promise<void>;
}) {
  const [activePart, setActivePart] = useState(0);
  const [completed, setCompleted] = useState<string[]>([]);
  const [hydratedStorageKey, setHydratedStorageKey] = useState("");
  const [regeneratingStepId, setRegeneratingStepId] = useState("");
  const [regenerationError, setRegenerationError] = useState("");

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(storageKey) || "[]");
      if (Array.isArray(saved)) setCompleted(saved.filter((value): value is string => typeof value === "string"));
    } catch {
      setCompleted([]);
    } finally {
      setActivePart(0);
      setHydratedStorageKey(storageKey);
    }
  }, [storageKey]);

  useEffect(() => {
    if (hydratedStorageKey !== storageKey) return;
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(completed));
    } catch {
      // Training remains usable when browser storage is unavailable.
    }
  }, [completed, hydratedStorageKey, storageKey]);

  const isIntroduction = activePart === 0;
  const step = isIntroduction ? null : procedure.steps[activePart - 1];
  const completedCount = procedure.steps.filter((candidate) => completed.includes(candidate.id)).length;
  const progress = procedure.steps.length
    ? Math.round((completedCount / procedure.steps.length) * 100)
    : 0;
  const stepComplete = step ? completed.includes(step.id) : false;
  const isFinalStep = activePart === procedure.steps.length;
  const visualStatus = step?.visual_status ?? (step?.annotations.length ? "ready" : "pending");
  const visualReady = visualStatus === "ready";
  const visualPending = visualStatus === "pending" || visualStatus === "selecting_frame" || visualStatus === "generating_annotation";

  const citation = useMemo<DocumentCitation[]>(() => {
    if (!step?.document_id || !step.filename) return [];
    return [{
      citation_id: `${step.id}-source`,
      document_id: step.document_id,
      filename: step.filename,
      page: step.page,
      section: step.section,
      excerpt: step.instruction,
    }];
  }, [step]);

  function openPart(nextPart: number) {
    const boundedPart = Math.max(0, Math.min(procedure.steps.length, nextPart));
    setActivePart(boundedPart);
    const nextStep = boundedPart > 0 ? procedure.steps[boundedPart - 1] : null;
    if (nextStep && (nextStep.visual_status === "ready" || (!nextStep.visual_status && nextStep.annotations.length))) {
      void onShowStep(nextStep);
    }
  }

  function advance() {
    if (isIntroduction) {
      openPart(1);
      return;
    }
    if (!visualReady || !stepComplete || isFinalStep) return;
    openPart(activePart + 1);
  }

  async function regenerate() {
    if (!step || typeof step.timestamp !== "number" || regeneratingStepId) return;
    setRegeneratingStepId(step.id);
    setRegenerationError("");
    try {
      await onRegenerateStep(step);
    } catch (error) {
      setRegenerationError(error instanceof Error ? error.message : "Unable to regenerate this annotation.");
    } finally {
      setRegeneratingStepId("");
    }
  }

  return (
    <section className="op-training-procedure">
      <header>
        <div>
          <span className="op-training-kicker">Guided training</span>
          <h3>{procedure.title}</h3>
        </div>
        <span className={procedure.manual_verified ? "op-manual-verified" : "op-manual-unverified"}>
          {procedure.manual_verified ? "Manual verified" : "Video-only guidance"}
        </span>
      </header>

      <div className="op-training-progress-heading">
        <strong>{isIntroduction ? "Step 0 · Introduction" : `Step ${activePart} of ${procedure.steps.length}`}</strong>
        <span>{progress}% complete</span>
      </div>
      <div
        className="op-training-progress-bar"
        role="progressbar"
        aria-label="Training progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
      >
        <span style={{ width: `${progress}%` }} />
      </div>

      {isIntroduction ? (
        <article className="op-training-step op-training-introduction">
          <span className="op-training-step-label">Step 0</span>
          <h4>Before you begin</h4>
          <p>{procedure.objective || `Learn how to complete ${procedure.title}.`}</p>

          {procedure.safety_warnings.length ? (
            <div className="op-training-warning">
              <strong>Safety</strong>
              <ul>{procedure.safety_warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </div>
          ) : null}

          {procedure.prerequisites.length ? (
            <p><strong>What you should know:</strong> {procedure.prerequisites.join(", ")}</p>
          ) : null}
          {procedure.materials.length ? (
            <p><strong>What you need:</strong> {procedure.materials.join(", ")}</p>
          ) : null}

          <p className="op-training-intro-note">
            The procedure is ready. OperatorOS will reveal one step at a time and show visual guidance when the video supports it.
          </p>
        </article>
      ) : step ? (
        <article className="op-training-step">
          <div className="op-training-step-heading">
            <span className="op-training-step-label">Step {activePart}</span>
            {visualReady ? <span className="op-visual-guidance-badge">Visual guidance ready</span> : null}
            {visualStatus === "selecting_frame" ? <span className="op-visual-guidance-pending"><span className="op-inline-spinner" /> Selecting frame</span> : null}
            {visualStatus === "generating_annotation" ? <span className="op-visual-guidance-pending"><span className="op-inline-spinner" /> Generating annotation</span> : null}
            {visualStatus === "pending" ? <span className="op-visual-guidance-pending">Waiting for visual processing</span> : null}
            {visualStatus === "error" ? <span className="op-visual-guidance-error">Visual guidance needs attention</span> : null}
          </div>
          <h4>{step.title}</h4>
          <p>{step.instruction}</p>
          {step.expected_result ? <p><strong>Expected result:</strong> {step.expected_result}</p> : null}
          {step.components.length ? <p><strong>Components:</strong> {step.components.join(", ")}</p> : null}
          {step.warnings.length ? <ul className="op-step-warnings">{step.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
          <div className="op-training-visual-actions">
            {visualReady ? (
              <button type="button" className="op-jump-button" onClick={() => void onShowStep(step)}>
                Show this step in video
              </button>
            ) : null}
            {typeof step.timestamp === "number" ? (
              <button
                type="button"
                className="op-jump-button"
                disabled={regeneratingStepId === step.id}
                onClick={() => void regenerate()}
              >
                {regeneratingStepId === step.id
                  ? <><span className="op-inline-spinner" /> Regenerating…</>
                  : visualStatus === "error" ? "Retry annotation" : "Regenerate annotation"}
              </button>
            ) : null}
          </div>
          {step.visual_error ? <p className="op-training-visual-error">{step.visual_error}</p> : null}
          {regenerationError ? <p className="op-training-visual-error">{regenerationError}</p> : null}
          <DocumentCitations citations={citation} />
          <label className="op-training-complete-control">
            <input
              type="checkbox"
              checked={stepComplete}
              disabled={!visualReady}
              onChange={(event) => setCompleted((current) =>
                event.target.checked
                  ? Array.from(new Set([...current, step.id]))
                  : current.filter((id) => id !== step.id)
              )}
            />
            <span>Mark this step complete</span>
          </label>
        </article>
      ) : null}

      <footer>
        <button
          type="button"
          disabled={activePart <= 1}
          onClick={() => openPart(activePart - 1)}
          title={activePart <= 1 ? "Previous is unavailable on the introduction and first step" : "Go to previous step"}
        >
          Previous
        </button>
        <span className="op-training-navigation-hint">
          {isIntroduction
            ? "Start when you are ready."
            : isFinalStep
              ? stepComplete ? "Training complete." : "Complete this final step."
              : stepComplete ? "Next step unlocked." : "Complete this step to continue."}
        </span>
        <button
          type="button"
          disabled={!isIntroduction && (!visualReady || !stepComplete || isFinalStep)}
          onClick={advance}
        >
          {isIntroduction
            ? "Start step 1"
            : visualPending
              ? <><span className="op-inline-spinner" /> Preparing visual…</>
              : visualStatus === "error"
                ? "Visual unavailable"
                : isFinalStep
              ? stepComplete ? "Training complete" : "Complete final step"
              : "Next step"}
        </button>
      </footer>
    </section>
  );
}
