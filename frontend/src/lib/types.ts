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
}

export interface DocumentIngestResponse {
  document_id: string;
  filename: string;
  chunk_count: number;
}

export interface TrackingOverlay {
  track_id: string;
  label: string;
  color: string;
  points: Point[];
  timestamp: number;
}
