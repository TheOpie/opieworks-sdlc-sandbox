export interface ButtonOptions {
  label: string;
  onClick?: () => void;
  disabled?: boolean;
}

/** A text button. The label is the accessible name. */
export function createButton(options: ButtonOptions): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = options.label;
  button.disabled = options.disabled ?? false;
  if (options.onClick) {
    button.addEventListener("click", options.onClick);
  }
  return button;
}
