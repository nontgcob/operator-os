export type AnnotationType = "rect" | "circle" | "path" | "text";

export interface Point {
  x: number;
  y: number;
}

export interface Annotation {
  type: AnnotationType;
  color: string;
  points?: Point[];
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  radius?: number;
  text?: string;
}

export interface TranscriptWindowResponse {
  timestamp: number;
  start: number;
  end: number;
  segments: Array<{ start: number; end: number; text: string }>;
}

export interface MediaIngestResponse {
  video_id: string;
}

export interface TrackingOverlay {
  track_id: string;
  label: string;
  color: string;
  points: Point[];
  timestamp: number;
}
