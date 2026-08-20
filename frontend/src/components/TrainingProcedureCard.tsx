"use client";

import { useEffect, useMemo, useState } from "react";

import { DocumentCitations } from "@/components/DocumentCitations";
import type { DocumentCitation, TrainingProcedure } from "@/lib/types";

export function TrainingProcedureCard({
  procedure,
  storageKey,
  onSeek,
}: {
  procedure: TrainingProcedure;
  storageKey: string;
  onSeek: (timestamp: number) => void;
}) {
  const [activeStep, setActiveStep] = useState(0);
  const [completed, setCompleted] = useState<string[]>([]);
  const [hydratedStorageKey, setHydratedStorageKey] = useState("");

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(storageKey) || "[]");
      if (Array.isArray(saved)) setCompleted(saved.filter((value): value is string => typeof value === "string"));
    } catch {
      setCompleted([]);
    } finally {
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

  const step = procedure.steps[Math.min(activeStep, procedure.steps.length - 1)];
  const citation = useMemo<DocumentCitation[]>(() => {
    if (!step.document_id || !step.filename) return [];
    return [{
      citation_id: `${step.id}-source`,
      document_id: step.document_id,
      filename: step.filename,
      page: step.page,
      section: step.section,
      excerpt: step.instruction,
    }];
  }, [step]);

  return (
    <section className="op-training-procedure">
      <header>
        <div>
          <span className="op-training-kicker">Training procedure</span>
          <h3>{procedure.title}</h3>
          {procedure.objective ? <p>{procedure.objective}</p> : null}
        </div>
        <span className={procedure.manual_verified ? "op-manual-verified" : "op-manual-unverified"}>
          {procedure.manual_verified ? "Manual verified" : "Video-only guidance"}
        </span>
      </header>

      {procedure.safety_warnings.length ? (
        <div className="op-training-warning">
          <strong>Safety</strong>
          <ul>{procedure.safety_warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </div>
      ) : null}

      {(procedure.prerequisites.length || procedure.materials.length) ? (
        <div className="op-training-preparation">
          {procedure.prerequisites.length ? <p><strong>Before starting:</strong> {procedure.prerequisites.join(", ")}</p> : null}
          {procedure.materials.length ? <p><strong>Materials:</strong> {procedure.materials.join(", ")}</p> : null}
        </div>
      ) : null}

      <div className="op-training-progress">
        <span>Step {activeStep + 1} of {procedure.steps.length}</span>
        <span>{completed.length} complete</span>
      </div>

      <article className="op-training-step">
        <label>
          <input
            type="checkbox"
            checked={completed.includes(step.id)}
            onChange={(event) => setCompleted((current) =>
              event.target.checked
                ? Array.from(new Set([...current, step.id]))
                : current.filter((id) => id !== step.id)
            )}
          />
          Mark step complete
        </label>
        <h4>{step.title}</h4>
        <p>{step.instruction}</p>
        {step.expected_result ? <p><strong>Expected result:</strong> {step.expected_result}</p> : null}
        {step.components.length ? <p><strong>Components:</strong> {step.components.join(", ")}</p> : null}
        {step.warnings.length ? <ul className="op-step-warnings">{step.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
        {typeof step.timestamp === "number" ? (
          <button type="button" className="op-jump-button" onClick={() => onSeek(step.timestamp as number)}>
            Show this step in video
          </button>
        ) : null}
        <DocumentCitations citations={citation} />
      </article>

      <footer>
        <button type="button" disabled={activeStep === 0} onClick={() => setActiveStep((value) => Math.max(0, value - 1))}>
          Previous
        </button>
        <button
          type="button"
          disabled={activeStep >= procedure.steps.length - 1}
          onClick={() => setActiveStep((value) => Math.min(procedure.steps.length - 1, value + 1))}
        >
          Next
        </button>
      </footer>
    </section>
  );
}
