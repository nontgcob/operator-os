"use client";

import { useState } from "react";

import { getDocumentFileUrl } from "@/lib/api";
import type { DocumentCitation } from "@/lib/types";

function citationLabel(citation: DocumentCitation) {
  return [
    citation.filename,
    citation.page ? `page ${citation.page}` : "",
    citation.section || "",
  ].filter(Boolean).join(" · ");
}

export function DocumentCitations({ citations }: { citations: DocumentCitation[] }) {
  const [previewId, setPreviewId] = useState<string | null>(null);
  if (!citations.length) return null;

  return (
    <section className="op-document-citations" aria-label="Document sources">
      <strong>Document sources</strong>
      <ol>
        {citations.map((citation) => {
          const sourceUrl = getDocumentFileUrl(citation.document_id, citation.page);
          const previewing = previewId === citation.citation_id;
          return (
            <li
              key={citation.citation_id}
              className="op-document-citation"
              onMouseEnter={() => setPreviewId(citation.citation_id)}
              onMouseLeave={() => setPreviewId(null)}
              onFocus={() => setPreviewId(citation.citation_id)}
              onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setPreviewId(null);
              }}
            >
              <a href={sourceUrl} target="_blank" rel="noreferrer">
                {citationLabel(citation)}
              </a>
              {citation.excerpt ? <blockquote>{citation.excerpt}</blockquote> : null}
              {previewing ? (
                <div className="op-citation-preview" role="dialog" aria-label={`Preview ${citation.filename}`}>
                  <iframe title={`Preview ${citationLabel(citation)}`} src={sourceUrl} />
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
