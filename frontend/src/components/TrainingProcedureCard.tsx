"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { DocumentCitations } from "@/components/DocumentCitations";
import type { DocumentCitation, TrainingProcedure, TrainingStep } from "@/lib/types";

const CONFETTI_COLORS = ["#6bbcff", "#1e3a8a", "#22c55e", "#facc15", "#fb7185", "#f8fafc"];
const CONFETTI_PIECES = Array.from({ length: 48 }, (_, index) => {
  const side = index % 2 === 0 ? "left" : "right";
  const direction = side === "left" ? 1 : -1;
  const endY = ((index * 47) % 70) - 35;
  const rotation = direction * (220 + ((index * 53) % 540));

  return {
    id: index,
    side,
    top: 46 + ((index * 13) % 9),
    delay: (index % 8) * 28,
    duration: 1000 + (index % 7) * 80,
    midX: direction * (32 + ((index * 11) % 8)),
    endX: direction * (50 + ((index * 17) % 12)),
    midY: Math.round(endY * 0.45 - 5),
    endY,
    midRotation: Math.round(rotation * 0.55),
    rotation,
    color: CONFETTI_COLORS[index % CONFETTI_COLORS.length],
  };
});

function resolvedVisualStatus(step: TrainingStep | undefined): NonNullable<TrainingStep["visual_status"]> {
  if (!step) return "pending";
  return step.visual_status ?? (step.annotations.length ? "ready" : "pending");
}

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
  const [confettiRun, setConfettiRun] = useState<number | null>(null);
  const waitingForVisualStepId = useRef("");

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(storageKey) || "[]");
      if (Array.isArray(saved)) setCompleted(saved.filter((value): value is string => typeof value === "string"));
    } catch {
      setCompleted([]);
    } finally {
      setActivePart(0);
      waitingForVisualStepId.current = "";
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

  useEffect(() => {
    if (confettiRun === null) return;
    const timeout = window.setTimeout(() => setConfettiRun(null), 2100);
    return () => window.clearTimeout(timeout);
  }, [confettiRun]);

  const isIntroduction = activePart === 0;
  const step = isIntroduction ? null : procedure.steps[activePart - 1];
  const completedCount = procedure.steps.filter((candidate) => completed.includes(candidate.id)).length;
  const progress = procedure.steps.length
    ? Math.round((completedCount / procedure.steps.length) * 100)
    : 0;
  const stepComplete = step ? completed.includes(step.id) : false;
  const isFinalStep = activePart === procedure.steps.length;
  const visualStatus = resolvedVisualStatus(step ?? undefined);
  const exactFrameVisualBusy = Boolean(step && regeneratingStepId === step.id);
  const visualReady = visualStatus === "ready" && !exactFrameVisualBusy;
  const visualPending = exactFrameVisualBusy || visualStatus === "pending" || visualStatus === "selecting_frame" || visualStatus === "generating_annotation";
  const readyVisualCount = procedure.steps.filter((candidate) => resolvedVisualStatus(candidate) === "ready").length;
  const failedVisualCount = procedure.steps.filter((candidate) => resolvedVisualStatus(candidate) === "error").length;
  const visualPreparationProgress = procedure.steps.length
    ? Math.round((readyVisualCount / procedure.steps.length) * 100)
    : 0;
  const firstStepStatus = resolvedVisualStatus(procedure.steps[0]);
  const firstStepAvailable = firstStepStatus === "ready" || firstStepStatus === "error";
  const nextStep = !isIntroduction && !isFinalStep ? procedure.steps[activePart] : null;
  const nextStepStatus = resolvedVisualStatus(nextStep ?? undefined);
  const nextStepAvailable = !nextStep || nextStepStatus === "ready" || nextStepStatus === "error";

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

  useEffect(() => {
    if (!step || !visualReady || waitingForVisualStepId.current !== step.id) return;
    waitingForVisualStepId.current = "";
    void onShowStep(step);
  }, [onShowStep, step, visualReady]);

  function openPart(nextPart: number) {
    const boundedPart = Math.max(0, Math.min(procedure.steps.length, nextPart));
    setActivePart(boundedPart);
    const nextStep = boundedPart > 0 ? procedure.steps[boundedPart - 1] : null;
    if (!nextStep) return;
    if (nextStep.visual_status === "ready" || (!nextStep.visual_status && nextStep.annotations.length)) {
      waitingForVisualStepId.current = "";
      void onShowStep(nextStep);
    } else {
      waitingForVisualStepId.current = nextStep.id;
    }
  }

  function advance() {
    if (isIntroduction) {
      if (!firstStepAvailable) return;
      openPart(1);
      return;
    }
    if (!visualReady || !stepComplete || isFinalStep || !nextStepAvailable) return;
    openPart(activePart + 1);
  }

  function setStepCompletion(checked: boolean) {
    if (!step) return;
    setCompleted((current) => checked
      ? Array.from(new Set([...current, step.id]))
      : current.filter((id) => id !== step.id));
    if (checked && isFinalStep && !stepComplete) {
      setConfettiRun((current) => (current ?? 0) + 1);
    }
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
      {confettiRun !== null ? (
        <div key={confettiRun} className="op-training-confetti" data-testid="training-confetti" aria-hidden="true">
          {CONFETTI_PIECES.map((piece) => (
            <i
              key={piece.id}
              className={`op-training-confetti-piece op-training-confetti-piece--${piece.side}`}
              data-side={piece.side}
              style={{
                "--op-confetti-top": `${piece.top}%`,
                "--op-confetti-delay": `${piece.delay}ms`,
                "--op-confetti-duration": `${piece.duration}ms`,
                "--op-confetti-mid-x": `${piece.midX}vw`,
                "--op-confetti-end-x": `${piece.endX}vw`,
                "--op-confetti-mid-y": `${piece.midY}vh`,
                "--op-confetti-end-y": `${piece.endY}vh`,
                "--op-confetti-mid-rotation": `${piece.midRotation}deg`,
                "--op-confetti-rotation": `${piece.rotation}deg`,
                "--op-confetti-color": piece.color,
              } as CSSProperties}
            />
          ))}
        </div>
      ) : null}
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

      <div className="op-training-visual-progress" aria-live="polite">
        <div className="op-training-visual-progress-heading">
          <strong>
            {readyVisualCount === procedure.steps.length
              ? "All visual guidance ready"
              : "Preparing visual guidance"}
          </strong>
          <span>{readyVisualCount} of {procedure.steps.length} ready</span>
        </div>
        <div
          className="op-training-visual-progress-bar"
          role="progressbar"
          aria-label="Visual guidance preparation"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={visualPreparationProgress}
        >
          <span style={{ width: `${visualPreparationProgress}%` }} />
        </div>
        {readyVisualCount < procedure.steps.length ? (
          <small>
            {failedVisualCount
              ? `${failedVisualCount} visual${failedVisualCount === 1 ? " needs" : "s need"} attention; the others are still preparing.`
              : "Earlier steps are prepared first while you review the introduction."}
          </small>
        ) : null}
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
            {exactFrameVisualBusy ? <span className="op-visual-guidance-pending"><span className="op-inline-spinner" /> Generating annotation</span> : null}
            {visualStatus === "selecting_frame" ? <span className="op-visual-guidance-pending"><span className="op-inline-spinner" /> Selecting frame</span> : null}
            {visualStatus === "generating_annotation" && !exactFrameVisualBusy ? <span className="op-visual-guidance-pending"><span className="op-inline-spinner" /> Generating annotation</span> : null}
            {visualStatus === "pending" && !exactFrameVisualBusy ? <span className="op-visual-guidance-pending">Waiting for visual processing</span> : null}
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
            {typeof step.timestamp === "number" && (visualReady || visualStatus === "error" || exactFrameVisualBusy) ? (
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
              onChange={(event) => setStepCompletion(event.target.checked)}
            />
            <span>Mark this step complete</span>
          </label>
        </article>
      ) : null}

      <footer>
        <button
          type="button"
          disabled={activePart <= 1 || exactFrameVisualBusy}
          onClick={() => openPart(activePart - 1)}
          title={activePart <= 1 ? "Previous is unavailable on the introduction and first step" : "Go to previous step"}
        >
          Previous
        </button>
        <span className="op-training-navigation-hint">
          {isIntroduction
            ? firstStepAvailable ? "Start when you are ready." : "Step 1 visual is being prepared."
            : isFinalStep
              ? stepComplete ? "Training complete." : "Complete this final step."
              : stepComplete
                ? nextStepAvailable ? "Next step unlocked." : "Preparing the next step visual."
                : "Complete this step to continue."}
        </span>
        <button
          type="button"
          disabled={isIntroduction
            ? !firstStepAvailable
            : !visualReady || !stepComplete || isFinalStep || !nextStepAvailable}
          onClick={advance}
        >
          {isIntroduction
            ? firstStepAvailable
              ? "Start step 1"
              : <><span className="op-inline-spinner" /> Preparing step 1…</>
            : visualPending
              ? <><span className="op-inline-spinner" /> Preparing visual…</>
              : visualStatus === "error"
                ? "Visual unavailable"
                : isFinalStep
                  ? stepComplete ? "Training complete" : "Complete final step"
                  : stepComplete && !nextStepAvailable
                    ? <><span className="op-inline-spinner" /> Getting next step ready…</>
                    : "Next step"}
        </button>
      </footer>
    </section>
  );
}
