export type AnnotationType = "rect" | "circle" | "path" | "text";

export interface Annotation {
  type: AnnotationType;
  color: string;
  points?: Array<{ x: number; y: number }>;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  text?: string;
}

export interface TranscriptWindowResponse {
  timestamp: number;
  start: number;
  end: number;
  segments: Array<{ start: number; end: number; text: string }>;
}

export interface TrackingOverlay {
  track_id: string;
  label: string;
  color: string;
  points: Array<{ x: number; y: number }>;
  timestamp: number;
}
