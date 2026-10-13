---
work_item: 20261013-icon-button-is-not-accessible-or-labelled
upstream: "none, used free text"
stage: intent
kind: bug
source: free text
lane: full
api_change: yes
token_change: no
visual_change: yes
---

# Intent: Icon button is not accessible or labelled

This file is the intent of a bug fix. A human accepts it when the intent pull request merges. A human rejects it when the pull request closes. Then the reason goes to `sdlc/triage/`.

## Problem

createIconButton in src/icon-button.ts builds its element with `document.createElement("div")` and returns type `HTMLElement`. The element has no button role, it cannot get keyboard focus, and it has no accessible name. The `label` option in IconButtonOptions is declared, but the function never reads it.

Steps to reproduce:

1. Call `createIconButton({ iconPath: "M0 0h24v24H0z", label: "Close" })`.
2. Check the tag, role, focus, and accessible name of the returned element.
3. Press Tab to reach it, then press Enter or Space.

Expected: a native `<button type="button">` with aria-label "Close", reachable by Tab, with the `<svg>` marked `aria-hidden="true"`.

Actual: a `<div>` with no role, no tabindex, and no aria-label. The SVG is not hidden from assistive technology. Tab cannot reach it, and only a mouse click calls onClick.

## Outcome

Screen reader and keyboard users can reach and use every icon button from this library. The button is announced as a button with its label, for example "Close". Tab, Enter, and Space work with it. It looks like the browser's default button and shows the browser's default focus ring.

## Affected users and systems

Screen reader and keyboard users of any UI built with this library. Files: src/icon-button.ts (createIconButton, IconButtonOptions), src/index.ts (re-exports createIconButton), and src/components.test.ts (createIconButton tests). Reference pattern: createButton in src/button.ts.

## Constraints

Use a native `<button type="button">` like createButton. The accessible name comes from aria-label set to `label`. The `<svg>` gets `aria-hidden="true"`. No custom focus style. The browser's default button look is acceptable. Narrow the return type from HTMLElement to HTMLButtonElement. The IconButtonOptions shape does not change. No deadline and no release tie (found in the opieworks-sdlc sandbox test).

## Open questions

Does the returned `<button>` keep the class "icon-button"? No CSS uses it in this repo, but outside callers may select it. The design stage decides.

## Lane

- API change: yes
- Token change (design tokens, for example colors, spacing, or type): no
- Visual change: yes
- Proposed lane: **full**. The answer for api change, visual change is yes or unknown.

The fast lane is only for a bug with no API change, no token change, and no visual change. The fast lane skips the design stage. The human sets the lane at this gate. The `lane` key in the frontmatter holds that lane.

## Done when

1. A new test in src/components.test.ts fails now and passes after the fix: `createIconButton({ iconPath: "M0 0", label: "Close" })` returns an element with tagName "BUTTON", type "button", and aria-label "Close", and its svg has aria-hidden "true".
2. The existing createIconButton tests ("renders the icon", "calls onClick") still pass.
3. `pnpm verify` passes with createIconButton declared as returning HTMLButtonElement.
