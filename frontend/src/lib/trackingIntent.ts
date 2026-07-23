export function explicitlyRequestsTracking(question: string): boolean {
  const normalized = question.trim().toLowerCase();
  if (!normalized) return false;

  return (
    /\b(track|tracking|follow|following|trace|tracing|monitor|monitoring)\b/.test(normalized) ||
    /\bkeep\s+(?:an\s+eye\s+on|watching|in\s+view)\b/.test(normalized)
  );
}
