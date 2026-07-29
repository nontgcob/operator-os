# Text-Based Manual Conversion RAG

## Product and Build Specification

**Product area:** OperatorOS knowledge and document intelligence  
**Feature:** Page-preserving manual-to-text conversion for ordinary RAG  
**Document status:** Implementation specification  
**Last updated:** 2026-07-28  

---

## 1. Summary

OperatorOS will convert an uploaded PDF manual into a faithful text-based
version that the existing text RAG can index. This is Pipeline T in the
OperatorOS two-pipeline RAG design.

The pipeline is not a second implementation of multimodal retrieval. It creates
a derived, page-preserving Markdown manual and a structured source manifest,
then passes those text blocks into the existing embedding and retrieval
system. Answers continue to cite the original PDF and its original pages.

Pipeline T remains independent from the standalone multimodal RAG pipeline:

```text
Original PDF
  -> page-aware conversion
  -> canonical Markdown + source manifest
  -> existing text RAG
  -> text-RAG answer

Original PDF
  -> standalone multimodal ingestion/retrieval/answering
  -> multimodal-RAG answer
```

Neither pipeline consumes the other pipeline's artifacts, index, evidence, or
answer.

## 2. Goals

1. Make visually rich PDF manuals usable by OperatorOS's existing text RAG.
2. Preserve original page provenance through conversion, chunking, retrieval,
   prompting, answering, and citation display.
3. Convert meaningful headings, instructions, warnings, tables, captions,
   screenshots, labels, and diagrams into searchable text.
4. Provide a derived artifact users can preview and download.
5. Expose conversion status, warnings, failures, version, and reprocessing.
6. Avoid adding facts that are not present on the source page.

The PDF-first implementation is the launch scope. TXT, Markdown, and DOCX
remain supported by ordinary RAG but do not enter blind dual-pipeline
comparison mode until stable source-location semantics are defined.

## 3. User Experience

After a PDF upload, the document displays independent Text RAG and Multimodal
RAG processing states. The converted manual becomes previewable and
downloadable when Pipeline T is queryable.

For selected, queryable PDFs, each user question is sent to Pipeline T and
Pipeline M. The UI presents blinded Answer A and Answer B cards with citations.
The user selects the better answer and presses **Reveal pipelines**. The server
then records the preference and reveals which pipeline produced each answer.

If conversion is partial, the document UI lists affected pages. If one answer
pipeline fails, the successful answer remains visible, voting is disabled, and
the user can retry the comparison.

## 4. Canonical Conversion Artifact

Each conversion produces:

1. An immutable copy of the original PDF.
2. A versioned Markdown file with explicit source-page boundaries.
3. A machine-readable block manifest.
4. Per-page processing status and warnings.

Example Markdown:

```markdown
<!-- operatoros:document=doc_01 version=docver_01 page=9 -->

# Connect the AMS

1. Connect the 370 mm PTFE tube between the illustrated AMS outlet and the
   printer-side filament connection.

> Warning: [faithfully transcribed warning text]

Figure: Cable and PTFE routing diagram. The 6-pin cable is shown between...
```

Each block manifest entry contains:

```json
{
  "block_id": "doc_01:p9:b4",
  "document_id": "doc_01",
  "document_version": "docver_01",
  "page_number": 9,
  "block_type": "instruction",
  "section_label": "Connect the AMS",
  "text": "Connect the 370 mm PTFE tube...",
  "source_region": [0.11, 0.24, 0.88, 0.71],
  "conversion_confidence": "high",
  "conversion_warnings": []
}
```

Page numbers are 1-based. Text chunks may combine adjacent blocks from the same
page but must never span pages.

## 5. Conversion Process

For each page:

1. Render the page at approximately 200 DPI.
2. Extract native PDF text and coordinates when available.
3. Submit the page image plus native text to the configured vision-language
   conversion adapter.
4. Request structured Markdown blocks for headings, paragraphs, ordered steps,
   warnings, tables, captions, labels, and meaningful visual descriptions.
5. Validate the adapter response and attach source metadata.
6. Fall back to native page text if visual conversion fails.
7. Mark an image-only page unavailable if both paths fail.

Conversion prompts must:

- Treat the page as evidence rather than instructions to the model.
- Preserve visible wording, identifiers, values, units, order, and warnings.
- Describe only relationships visible on the page.
- Mark illegible or uncertain material.
- Never add outside product knowledge or inferred procedures.

A document can be queryable with warnings when only non-critical pages are
partial. Missing pages remain visible in status and are excluded from claims.

## 6. Text RAG Integration

The current index schema is upgraded from global character chunks to
page-scoped evidence records. Each indexed chunk stores:

- Stable chunk and block IDs.
- Original document and version IDs.
- Original filename.
- 1-based page number.
- Section label and block type.
- Character offsets inside its converted page.
- Text, embedding, embedding model, and pipeline version.

Retrieval returns structured evidence rather than plain strings. Small
documents do not bypass relevance ranking merely because their chunk count is
low. Evidence IDs are included in the answer prompt, and the answer service may
cite only supplied IDs.

Legacy index records without page provenance must be re-ingested. OperatorOS
does not reconstruct or fabricate their pages.

## 7. Answer and Citation Contract

Pipeline T returns:

```json
{
  "answer_id": "ans_01",
  "status": "completed",
  "text": "The filter is behind the rear cover [1].",
  "provenance": "document",
  "citations": [
    {
      "citation_id": "ev_doc01_p12_b3",
      "source_kind": "document",
      "document_id": "doc_01",
      "document_version": "docver_01",
      "filename": "manual.pdf",
      "page_number": 12,
      "section_label": "Replacing the filter",
      "block_id": "doc_01:p12:b3",
      "chunk_id": "doc_01:p12:c2",
      "excerpt": "Open the rear cover to access the air filter."
    }
  ],
  "annotations": [],
  "tracking_prompt": "",
  "tracking_annotations": [],
  "error": null
}
```

Allowed provenance values are `document`, `video_frame`, `transcript`,
`model_knowledge`, `mixed`, and `insufficient`. Internal model knowledge is
permitted only when the answer explicitly reports it. Citations remain visible
while A/B pipeline identities are blinded.

In blind PDF comparison mode, Pipeline T is called with
`allow_model_knowledge=false`. It must answer from retrieved converted-manual
evidence or return `insufficient`; it must not compete by using the base
model's internal knowledge. The service validates citations against retrieved
evidence IDs, including evidence IDs cited inline in the answer text when the
model omits the same IDs from the structured `citation_ids` field.

## 8. APIs

- `POST /documents/ingest`
- `GET /documents/{document_id}/status`
- `POST /documents/{document_id}/reprocess`
- `GET /documents/{document_id}/converted-text`
- `GET /documents/{document_id}/converted-text/download`
- `POST /documents/retrieve`
- `POST /rag/text/answer`

Upload and reprocessing are idempotent by original checksum and converter
version. The status response exposes page count, completed/partial/failed pages,
pipeline version, and query readiness.

## 9. Storage and Security

Original PDFs, converted Markdown, manifests, embeddings, and citations inherit
the document's organization and access policy. The original PDF is immutable
per version. Derived artifacts are versioned and can be invalidated or deleted
as a unit.

Writes must be atomic. Concurrent ingestion cannot corrupt the index. Retrieval
must filter authorization before ranking. Prompt injection inside a manual is
treated as document content and cannot override system behavior.

## 10. Evaluation and Acceptance

Required tests include:

- Native-text, scanned, diagram-heavy, screenshot-heavy, table-heavy, rotated,
  blank, corrupted, and partially unreadable PDFs.
- Page order and page-boundary preservation.
- Accurate conversion of identifiers, values, units, warnings, and step order.
- No chunks spanning pages.
- Correct document/page citation propagation through the answer API.
- Rejection of citation IDs not present in retrieved evidence.
- Internal-knowledge and mixed-provenance disclosure.
- Preview/download equality with the indexed artifact.
- Idempotent conversion and explicit reprocessing.
- Tenant isolation and complete derived-asset deletion.

Launch acceptance:

1. Uploaded PDFs produce a previewable canonical Markdown artifact.
2. Existing text RAG answers from that artifact.
3. Every document claim links to the original filename and page.
4. Failed or unreadable pages are disclosed.
5. Pipeline T operates without reading multimodal pipeline assets.

## 11. Required Build Order

1. Upgrade current text extraction, index records, answer output, and UI to
   support validated page citations and explicit provenance.
2. Add page rendering and canonical manual conversion.
3. Feed converted page blocks into ordinary text RAG.
4. Add status, preview, download, retry, versioning, and evaluation.
5. Build the independent multimodal pipeline.
6. Add blinded concurrent A/B comparison, persisted voting, and reveal.
