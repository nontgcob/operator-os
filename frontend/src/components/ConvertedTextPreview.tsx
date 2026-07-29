import { useEffect, useState } from "react";
import { getConvertedText, getConvertedTextDownloadUrl } from "@/lib/api";

export function ConvertedTextPreview({
  documentId,
  filename,
  onClose,
}: {
  documentId: string;
  filename: string;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    getConvertedText(documentId)
      .then((value) => {
        if (active) setText(value);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Converted text unavailable.");
      });
    return () => {
      active = false;
    };
  }, [documentId]);

  return (
    <div className="op-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="op-converted-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="converted-text-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="op-card-heading">
          <div>
            <h2 id="converted-text-title" className="op-card-title">Converted manual</h2>
            <p className="op-help-text">{filename}</p>
          </div>
          <div className="op-inline-actions">
            <a
              className="op-secondary-button op-link-button"
              href={getConvertedTextDownloadUrl(documentId)}
              download
            >
              Download Markdown
            </a>
            <button type="button" className="op-secondary-button" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
        {error ? <p className="op-error-text" role="alert">{error}</p> : null}
        <pre className="op-converted-text">
          {text || (!error ? "Loading converted manual…" : "")}
        </pre>
      </section>
    </div>
  );
}
