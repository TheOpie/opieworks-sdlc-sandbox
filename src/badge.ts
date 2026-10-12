export type BadgeTone = "neutral" | "success" | "warning" | "danger";

/** A small status label. */
export function createBadge(text: string, tone: BadgeTone = "neutral"): HTMLSpanElement {
  const badge = document.createElement("span");
  badge.className = `badge badge-${tone}`;
  badge.textContent = text;
  return badge;
}
