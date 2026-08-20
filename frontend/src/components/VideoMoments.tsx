import type { VideoMoment } from "@/lib/types";

function formatTimestamp(timestamp: number) {
  const minutes = Math.floor(timestamp / 60);
  const seconds = Math.floor(timestamp % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

export function VideoMoments({
  moments,
  onSeek,
}: {
  moments: VideoMoment[];
  onSeek: (timestamp: number) => void;
}) {
  if (!moments.length) return null;
  return (
    <section className="op-video-moments" aria-label="Relevant video moments">
      <strong>Relevant video moments</strong>
      <div className="op-video-moment-list">
        {[...moments].sort((left, right) => left.timestamp - right.timestamp).map((moment, index) => (
          <button
            type="button"
            key={`${moment.timestamp}-${moment.label}-${index}`}
            onClick={() => onSeek(moment.timestamp)}
            title={moment.reason || `Jump to ${formatTimestamp(moment.timestamp)}`}
          >
            <span>{formatTimestamp(moment.timestamp)}</span>
            {moment.label}
          </button>
        ))}
      </div>
    </section>
  );
}
