export interface IconButtonOptions {
  /** An SVG path for the icon. */
  iconPath: string;
  /** What the button does, for example "Close". */
  label: string;
  onClick?: () => void;
}

const SVG_NS = "http://www.w3.org/2000/svg";

/** A button that shows only an icon. */
export function createIconButton(options: IconButtonOptions): HTMLElement {
  const button = document.createElement("div");
  button.className = "icon-button";

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", options.iconPath);
  svg.appendChild(path);
  button.appendChild(svg);

  if (options.onClick) {
    button.addEventListener("click", options.onClick);
  }
  return button;
}
