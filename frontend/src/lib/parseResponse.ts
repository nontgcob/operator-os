export function parseModelResponse(raw: string): { answer: string } {
  if (!raw) return { answer: "" };
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed.answer === "string") {
      return { answer: parsed.answer };
    }
  } catch {
    // non-json fallback
  }
  return { answer: raw };
}
