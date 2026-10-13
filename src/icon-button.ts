export interface IconButtonOptions {
  /** An SVG path for the icon. */
  iconPath: string;
  /** What the button does, for example "Close". */
  label: string;
  onClick?: () => void;
}

const SVG_NS = "http://www.w3.org/2000/svg";

/** A button that shows only an icon. */
export function createIconButton(options: IconButtonOptions): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-button";
  button.setAttribute("aria-label", options.label);

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", options.iconPath);
  svg.appendChild(path);
  button.appendChild(svg);

  if (options.onClick) {
    button.addEventListener("click", options.onClick);
  }
  return button;
}
