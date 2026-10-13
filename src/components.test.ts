import { describe, expect, it, vi } from "vitest";
import { createBadge, createButton, createIconButton } from "./index";

describe("createButton", () => {
  it("renders the label as text", () => {
    const button = createButton({ label: "Save" });
    expect(button.tagName).toBe("BUTTON");
    expect(button.textContent).toBe("Save");
    expect(button.type).toBe("button");
  });

  it("calls onClick", () => {
    const onClick = vi.fn();
    createButton({ label: "Save", onClick }).click();
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("can be disabled", () => {
    expect(createButton({ label: "Save", disabled: true }).disabled).toBe(true);
  });
});

describe("createIconButton", () => {
  it("renders the icon", () => {
    const button = createIconButton({ iconPath: "M0 0h24v24H0z", label: "Close" });
    expect(button.querySelector("svg path")?.getAttribute("d")).toBe("M0 0h24v24H0z");
  });

  it("calls onClick", () => {
    const onClick = vi.fn();
    const button = createIconButton({ iconPath: "M0 0", label: "Close", onClick });
    button.dispatchEvent(new Event("click"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("is a labelled native button", () => {
    const button = createIconButton({ iconPath: "M0 0", label: "Close" });
    expect(button.tagName).toBe("BUTTON");
    expect(button.type).toBe("button");
    expect(button.getAttribute("aria-label")).toBe("Close");
    expect(button.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    expect(button.className).toBe("icon-button");
  });
});

describe("createBadge", () => {
  it("uses the tone class", () => {
    const badge = createBadge("New", "success");
    expect(badge.className).toBe("badge badge-success");
    expect(badge.textContent).toBe("New");
  });

  it("defaults to neutral", () => {
    expect(createBadge("Draft").className).toBe("badge badge-neutral");
  });
});
