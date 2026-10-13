---
work_item: 20261013-icon-button-is-not-accessible-or-labelled
upstream:
  - "intent.md"
stage: design
policies:
  - "sdlc/policies/sample-policy.md"
---

# Spec: 20261013-icon-button-is-not-accessible-or-labelled

## Source

Upstream artifacts:

- [intent.md](intent.md)

## Summary
`createIconButton` in `src/icon-button.ts` builds a `<div>` and declares the return type `HTMLElement`. The element has no button role, Tab cannot reach it, and it has no accessible name. It also never reads the `label` option. The fix builds a native `<button type="button">` the same way `createButton` in `src/button.ts` does. It sets `aria-label` to `label`, marks the `<svg>` with `aria-hidden="true"`, and narrows the return type to `HTMLButtonElement`.

## Requirements
- SPEC-1: `createIconButton` returns an element with tagName "BUTTON" and `type` "button". Traces to: Constraints ("Use a native `<button type="button">` like createButton"); Done when 1.
- SPEC-2: The returned button has the attribute `aria-label` set to `options.label`. Traces to: Constraints ("The accessible name comes from aria-label set to `label`"); Problem (`label` is never read); Done when 1.
- SPEC-3: The `<svg>` inside the button has the attribute `aria-hidden="true"`. Traces to: Constraints; Done when 1.
- SPEC-4: The declared return type of `createIconButton` is `HTMLButtonElement`. Traces to: Constraints ("Narrow the return type from HTMLElement to HTMLButtonElement"); Done when 3.
- SPEC-5: The `IconButtonOptions` interface stays the same: `iconPath: string`, `label: string`, `onClick?: () => void`. Traces to: Constraints ("The IconButtonOptions shape does not change").
- SPEC-6: The button still renders the icon path (`svg path` with `d` = `iconPath`, viewBox "0 0 24 24"). When `onClick` is given, a click event still calls it. Traces to: Done when 2.
- SPEC-7: The fix adds no custom focus style and no custom visual style. The button uses the browser's default button look and default focus ring. Tab reaches the button, and Enter and Space activate it, through native `<button>` behavior. Traces to: Outcome; Constraints ("No custom focus style").
- SPEC-8: Whether the returned button keeps `className = "icon-button"` is not decided. See FLAG-1. Traces to: Open questions.

## Design
**Parts that change**
- `src/icon-button.ts`, `createIconButton`:
  - Replace `document.createElement("div")` with `document.createElement("button")`, then set `button.type = "button"`, as `createButton` does at `src/button.ts:9-10`.
  - Add `button.setAttribute("aria-label", options.label)`.
  - Add `svg.setAttribute("aria-hidden", "true")`. The `viewBox` and the `<path d>` code stay as they are.
  - Change the signature to `createIconButton(options: IconButtonOptions): HTMLButtonElement`.
  - The `button.className = "icon-button"` line stays or goes depending on FLAG-1. The spec does not decide this.
  - The click listener for `options.onClick` stays as it is.
- `src/components.test.ts`: in `describe("createIconButton")`, add a test that calls `createIconButton({ iconPath: "M0 0", label: "Close" })` and asserts:
  - `tagName === "BUTTON"`
  - `type === "button"`
  - `getAttribute("aria-label") === "Close"`
  - `querySelector("svg")?.getAttribute("aria-hidden") === "true"`

  The existing tests "renders the icon" and "calls onClick" do not change.
- `src/index.ts`: no change. It already re-exports `createIconButton` and `IconButtonOptions`.

**Public API**
- `createIconButton(options: IconButtonOptions): HTMLButtonElement`. The return type is narrower than before. `HTMLButtonElement` extends `HTMLElement`, so callers that typed the result as `HTMLElement` still type-check.
- `IconButtonOptions` does not change.
- Runtime behavior visible to callers changes:
  - The element is now `<button>`, not `<div>`.
  - It has `type="button"` and an `aria-label`.
  - The svg has `aria-hidden`.
  - Inside a `<form>`, `type="button"` stops the button from submitting the form.
- The `icon-button` class may change. See FLAG-1.

**Data**
None. No stored or sent data.

**Errors**
No new error paths. The function does not validate `label`. The intent asks for no validation, and `label` is a required `string` in the type.

## Acceptance checks
- Run `pnpm test`. The new createIconButton test passes: tagName "BUTTON", type "button", aria-label "Close", svg aria-hidden "true".
- Run the new test against the current code before the fix. It fails (tagName is "DIV").
- Run `pnpm test`. The existing tests "renders the icon" and "calls onClick" pass with no change.
- Run `pnpm verify` (`pnpm typecheck && pnpm test`). It passes with `createIconButton` declared as returning `HTMLButtonElement`.
- Read `src/icon-button.ts`. `IconButtonOptions` has the same three members as before, and the file adds no focus or visual styles.
- Manual check in a browser:
  1. Render `createIconButton({ iconPath: "M0 0h24v24H0z", label: "Close", onClick })` in a page.
  2. Press Tab. The button gets focus and shows the browser's default focus ring.
  3. Press Enter, then Space. Each key calls `onClick` once.
  4. With a screen reader (for example VoiceOver), the button is announced as "Close, button", and the svg is not announced.
- After FLAG-1 is resolved, a test or a code read confirms the chosen `className` behavior.

## Out of scope
- Any custom focus style, visual style, or CSS for icon buttons.
- Changes to `IconButtonOptions`, `createButton`, `createBadge`, or `src/index.ts`.
- Validation of an empty `label`.

## Policy check
- sdlc/policies/sample-policy.md:
  - POL-1 Accessibility: the change makes the icon button have an accessible name (`aria-label`) and work with a keyboard only (native `<button>`). The contrast rule covers text. The icon button has no visible text, and the fix keeps the browser default look. No conflict.
  - POL-2 Public API: `createIconButton` and `IconButtonOptions` are not removed or renamed. Narrowing the return type to a subtype is not a removal or a rename. No conflict for those. Removing the `icon-button` class could count as removing a public part of the output that outside callers may select. Whether POL-2 covers a CSS class is not clear. See FLAG-1.
  - POL-3 Personal data: the change shows, logs, and exports no personal data. No conflict.
  - POL-4 Dependencies: the change adds no runtime dependency. No conflict.
  - POL-5 Secrets: the change adds no secret, token, or password. No conflict.

## Flags
- FLAG-1 (intent): The intent leaves open whether the returned `<button>` keeps the class "icon-button". No CSS in this repo uses it, but outside callers may select it. Removing it could count as removing a public part of the API under policy sdlc/policies/sample-policy.md, POL-2, which requires one minor release of deprecation first. The inputs do not say which release this fix ships in.
  Question: Should the `<button>` keep `className = "icon-button"`? If it is removed, does POL-2 apply to the class, so that it must be deprecated for one minor release first?
  Resolution: open
