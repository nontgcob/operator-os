import type { AnswerCitation, AnswerLabel, ComparisonAnswer, ComparisonTurn } from "@/lib/types";
import { canVote } from "@/lib/comparisonState";

function provenanceLabel(answer: ComparisonAnswer): string {
  switch (answer.provenance) {
    case "document":
      return "Document grounded";
    case "video_frame":
      return "Video frame";
    case "transcript":
      return "Transcript";
    case "model_knowledge":
      return "Model internal knowledge";
    case "mixed":
      return "Documents + model knowledge";
    case "insufficient":
      return "Insufficient evidence";
    default:
      return "Source pending";
  }
}

function citationLabel(citation: AnswerCitation): string {
  const source =
    citation.filename ??
    (citation.source_kind === "model_knowledge"
      ? "Model internal knowledge"
      : citation.source_kind.replace("_", " "));
  const parts = [source];
  if (citation.page) parts.push(`page ${citation.page}`);
  if (citation.section) parts.push(citation.section);
  if (typeof citation.timestamp === "number") {
    const minutes = Math.floor(citation.timestamp / 60);
    const seconds = Math.floor(citation.timestamp % 60).toString().padStart(2, "0");
    parts.push(`${minutes}:${seconds}`);
  }
  return parts.join(" · ");
}

function AnswerCard({
  answer,
  selected,
  selectable,
  revealed,
  onSelect,
}: {
  answer: ComparisonAnswer;
  selected: boolean;
  selectable: boolean;
  revealed: boolean;
  onSelect: () => void;
}) {
  const isLoading = answer.status === "pending" || answer.status === "streaming";
  return (
    <article
      className={`op-answer-card ${selected ? "op-answer-card-selected" : ""}`}
      aria-label={`Answer ${answer.label}`}
    >
      <div className="op-answer-heading">
        <div>
          <h3>Answer {answer.label}</h3>
          <span className={`op-provenance-badge op-provenance-${answer.provenance ?? "pending"}`}>
            {provenanceLabel(answer)}
          </span>
        </div>
        {revealed && answer.pipeline ? (
          <span className="op-pipeline-badge">
            {answer.pipeline === "text_rag" ? "Text RAG" : answer.pipeline === "multimodal_rag" ? "Multimodal RAG" : answer.pipeline}
          </span>
        ) : null}
      </div>

      {answer.status === "error" ? (
        <div className="op-answer-error" role="alert">
          <strong>Answer unavailable</strong>
          <span>{answer.error ?? "The pipeline did not return an answer."}</span>
        </div>
      ) : (
        <div className={`op-answer-text ${isLoading && !answer.text ? "op-answer-loading" : ""}`}>
          {answer.text || (isLoading ? "Generating answer…" : "No answer returned.")}
        </div>
      )}

      {answer.citations.length ? (
        <div className="op-citations">
          <h4>Sources</h4>
          <ol>
            {answer.citations.map((citation) => (
              <li key={citation.citation_id}>
                <strong>{citationLabel(citation)}</strong>
                {citation.excerpt ? <blockquote>{citation.excerpt}</blockquote> : null}
              </li>
            ))}
          </ol>
        </div>
      ) : answer.status === "complete" ? (
        <p className="op-no-citations">
          {answer.provenance === "model_knowledge"
            ? "No document citation — this answer uses model internal knowledge."
            : "No source citation was returned."}
        </p>
      ) : null}

      {!revealed ? (
        <button
          type="button"
          className={`op-answer-select ${selected ? "op-answer-select-active" : ""}`}
          disabled={!selectable}
          aria-pressed={selected}
          onClick={onSelect}
        >
          {selected ? "Selected" : `Choose Answer ${answer.label}`}
        </button>
      ) : null}
    </article>
  );
}

export function ComparisonTurnCard({
  turn,
  revealing,
  onSelect,
  onReveal,
  onRetry,
}: {
  turn: ComparisonTurn;
  revealing: boolean;
  onSelect: (label: AnswerLabel) => void;
  onReveal: () => void;
  onRetry: () => void;
}) {
  const selectable = canVote(turn);
  return (
    <section className="op-comparison-turn">
      <div className="op-comparison-intro">
        <div>
          <strong>Blind RAG comparison</strong>
          <span>Review both answers and their evidence before choosing.</span>
        </div>
        {turn.status === "streaming" ? <span className="op-live-badge">Generating</span> : null}
      </div>
      <div className="op-answer-grid">
        {(["A", "B"] as const).map((label) => (
          <AnswerCard
            key={label}
            answer={turn.answers[label]}
            selected={turn.selected_label === label}
            selectable={selectable}
            revealed={turn.revealed}
            onSelect={() => onSelect(label)}
          />
        ))}
      </div>
      {turn.status === "partial" || turn.status === "error" ? (
        <div className="op-comparison-footer">
          <p>Both answers are required for a fair vote. This run is excluded from preference results.</p>
          <button type="button" className="op-secondary-button" onClick={onRetry}>
            Retry both answers
          </button>
        </div>
      ) : !turn.revealed ? (
        <div className="op-comparison-footer">
          <p>
            {turn.selected_label
              ? `Answer ${turn.selected_label} selected. Reveal to submit and lock your choice.`
              : "Choose the more useful answer. Pipeline identities stay hidden until reveal."}
          </p>
          <button
            type="button"
            className="op-primary-button"
            disabled={!turn.selected_label || !selectable || revealing}
            onClick={onReveal}
          >
            {revealing ? "Revealing…" : "Reveal pipelines"}
          </button>
          {turn.reveal_error ? <p className="op-error-text">{turn.reveal_error}</p> : null}
        </div>
      ) : (
        <div className="op-comparison-footer op-comparison-revealed">
          Preference recorded: Answer {turn.selected_label}
        </div>
      )}
    </section>
  );
}
