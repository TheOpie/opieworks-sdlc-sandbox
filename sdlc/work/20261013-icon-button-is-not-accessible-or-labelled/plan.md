---
work_item: 20261013-icon-button-is-not-accessible-or-labelled
upstream:
  - "intent.md"
  - "spec.md"
stage: build
lane: full
status: accepted
breach: none
breach_reason: ""
locked_tests: [src/components.test.ts]
---

# Plan: 20261013-icon-button-is-not-accessible-or-labelled

- Lane: full. The fast lane is a bug fix with no API, token, or visual change. It writes a failing test first.
- Status: `draft` until the human accepts the plan. The fast lane records the acceptance in the session. In the full lane, the merge of the plan PR is the acceptance.
- The frontmatter keys `breach`, `breach_reason`, and `locked_tests` come from the planner output. The engine reads them.

## Request

See the upstream artifacts.

## Plan (written by the planner role)

This section is a summary by the planner role. The planner reads only. It does not edit code.

```text
Breach: none
Breach reason: none
Locked tests: src/components.test.ts
Files:
- src/components.test.ts: in describe("createIconButton"), add one new test (for example "is a labelled native button"). It calls createIconButton({ iconPath: "M0 0", label: "Close" }) and asserts tagName "BUTTON", type "button", getAttribute("aria-label") "Close", querySelector("svg")?.getAttribute("aria-hidden") "true", and className "icon-button". The tests "renders the icon" and "calls onClick" stay unchanged.
- src/icon-button.ts: in createIconButton, change document.createElement("div") to document.createElement("button") and add button.type = "button" (same as src/button.ts:9-10). Keep button.className = "icon-button" (line 14). Add button.setAttribute("aria-label", options.label) and svg.setAttribute("aria-hidden", "true"). Change the return type at line 12 from HTMLElement to HTMLButtonElement. IconButtonOptions, viewBox, the path code and the onClick listener stay unchanged.
Test first: The new test asserts tagName "BUTTON", type "button", aria-label "Close" and svg aria-hidden "true". Today it fails at runtime because src/icon-button.ts:13 creates a <div>, so tagName is "DIV". Also, label is never read (no aria-label) and the svg has no aria-hidden. `pnpm typecheck` also fails before the fix: tsconfig.json includes src/, and `.type` does not exist on the declared return type HTMLElement. Narrowing the return type to HTMLButtonElement (SPEC-4) fixes that error, so the locked test also checks Done-when 3.
Steps:
1. Add the new test to src/components.test.ts and commit it alone. This commit locks src/components.test.ts. Check: `pnpm test` fails only on the new test (tagName "DIV"), and "renders the icon" and "calls onClick" still pass.
2. Change createIconButton in src/icon-button.ts as listed under Files. Check: `pnpm test` passes all tests, including the new one and the two existing createIconButton tests.
3. Run `pnpm verify` (typecheck and test). Check: it passes with createIconButton declared as returning HTMLButtonElement.
4. Read src/icon-button.ts once more. Check: IconButtonOptions still has iconPath, label and onClick?, className is still "icon-button", no style or focus code was added, and src/index.ts is unchanged.
5. Do the manual browser check from spec.md "Acceptance checks": Tab focus with the default ring, Enter and Space each call onClick once, and VoiceOver announces "Close, button".
Risks: Locking the whole of src/components.test.ts also freezes the createButton and createBadge tests for the rest of this work item, so any later test change in that file needs a new work item. The test-first commit fails both `pnpm typecheck` and `pnpm test` on purpose, so CI on that commit is red until step 2. Callers that relied on the <div> (for example `div.icon-button` selectors, or the element inside a <form>) now get a <button type="button">. The spec accepts this: class "icon-button" stays, and type="button" prevents form submission.
Open questions: none
```

## Acceptance

Accepted by the merge of https://github.com/TheOpie/opieworks-sdlc-sandbox/pull/14.

## Drift log

When the implementation differs from the plan, write it here (FR-42). Name each file that the change adds or changes and that the plan does not name. Say why.

- none
