export type AnnotationType =
  | "cursor"
  | "select"
  | "pen"
  | "arrow"
  | "rect"
  | "circle"
  | "eraser"
  | "text";
export type AnnotationPrimitiveType =
  | "arrow"
  | "rect"
  | "circle"
  | "text"
  | "path"
  | "polygon"
  | "number";

export interface Point {
  x: number;
  y: number;
}

export interface Annotation {
  type: AnnotationPrimitiveType;
  color: string;
  coordinate_space?: "ragvlm_0_1000";
  strokeWidth?: number;
  fill?: string;
  points?: Array<Point | number[]>;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  cx?: number;
  cy?: number;
  r?: number;
  radius?: number;
  text?: string;
  content?: string;
  fontSize?: number;
  value?: number;
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;
  d?: string;
}

export type AnnotationUndoEntry =
  | { op: "pop"; count: number }
  | { op: "insert"; idx: number; annotation: Annotation }
  | { op: "replace"; idx: number; previous: Annotation };

export interface TranscriptWindowResponse {
  timestamp: number;
  start: number;
  end: number;
  segments: Array<{ start: number; end: number; text: string }>;
  source?: "whisper" | "fallback" | "empty";
  whisper_enabled?: boolean;
  model?: string | null;
  warning?: string | null;
}

export interface MediaIngestResponse {
  video_id: string;
  title?: string;
  source?: "upload" | "youtube" | "unknown";
}

export interface VideoMetadataResponse {
  video_id: string;
  title: string;
  source?: string;
  source_label?: string;
}

export interface DocumentIngestResponse {
  document_id: string;
  filename: string;
  chunk_count: number;
  status?: string;
  pipelines?: Record<string, { status?: string; chunk_count?: number; error?: string }>;
}

export interface DocumentStatusResponse {
  document_id: string;
  status: "processing" | "queryable" | "partial" | string;
  pipelines: Record<
    string,
    { status?: string; chunk_count?: number; error?: string; warnings?: unknown[] }
  >;
}

export type AnswerLabel = "A" | "B";
export type AnswerStatus = "pending" | "streaming" | "complete" | "error";
export type AnswerProvenance =
  | "document"
  | "video_frame"
  | "transcript"
  | "model_knowledge"
  | "mixed"
  | "insufficient";

export interface AnswerCitation {
  citation_id: string;
  source_kind: "document" | "video_frame" | "transcript" | "model_knowledge";
  document_id?: string;
  document_version?: string;
  filename?: string;
  page?: number;
  section?: string;
  block?: string;
  excerpt?: string;
  region_id?: string;
  figure_id?: string;
  table_id?: string;
  bbox?: [number, number, number, number];
  timestamp?: number;
}

export interface ComparisonAnswer {
  answer_id?: string;
  label: AnswerLabel;
  status: AnswerStatus;
  text: string;
  provenance?: AnswerProvenance;
  citations: AnswerCitation[];
  annotations: Annotation[];
  tracking_prompt?: string;
  tracking_annotations: Annotation[];
  error?: string;
  pipeline?: string;
}

export interface ComparisonTurn {
  comparison_id?: string;
  status: "streaming" | "complete" | "partial" | "revealed" | "error";
  answers: Record<AnswerLabel, ComparisonAnswer>;
  selected_label?: AnswerLabel;
  revealed: boolean;
  reveal_error?: string;
  retryable?: boolean;
}

export type ComparisonStreamEvent =
  | { type: "comparison_started"; comparison_id: string }
  | { type: "answer_delta"; label: AnswerLabel; delta: string }
  | { type: "answer_complete"; label: AnswerLabel; answer: Partial<ComparisonAnswer> }
  | { type: "answer_error"; label: AnswerLabel; message: string }
  | { type: "comparison_complete"; comparison_id?: string };

export interface ComparisonRevealResponse {
  comparison_id: string;
  selected_label: AnswerLabel;
  mapping: Record<AnswerLabel, string>;
}

export interface TrackingOverlay {
  track_id: string;
  label: string;
  color: string;
  points: Point[];
  timestamp: number;
}

export interface TrackingOverlayManifest {
  tracking_job_id: string;
  overlay_count: number;
  overlays: TrackingOverlay[];
}

export interface TrackingLayer {
  id: string;
  jobId: string;
  round: number;
  label: string;
  color: string;
  visible: boolean;
  overlays: TrackingOverlay[];
}
