---
work_item: 20261013-icon-button-is-not-accessible-or-labelled
upstream:
  - "intent.md"
  - "spec.md"
  - "plan.md"
stage: testing
verdict: PASS
---

# Evidence: 20261013-icon-button-is-not-accessible-or-labelled

## Verify result

- Command: `pnpm verify`
- Runs: 1 of 5 allowed. The loop stops at the first pass.
- Verdict of the engine: **PASS**

## Verifier report (summary, written by the verifier role)

This section is a summary. It is not raw output. The verifier role wrote it with a fresh context. The verifier does not edit code.

```text
Verdict: PASS
Exit codes: 0 (Run 1 of 5, from evidence.md; the engine stopped after this run because it passed. I re-ran `pnpm verify` myself as a read-only check and it also exited 0 with 8 of 8 tests passing. That re-run is not one of the 5 counted runs.)
Covered: `pnpm verify` runs `tsc --noEmit` and then `vitest run`. The typecheck covers the new return type of `createIconButton`, which changed from `HTMLElement` to `HTMLButtonElement`, in src/icon-button.ts:12. The new test "is a labelled native button" in src/components.test.ts checks each change in src/icon-button.ts:
  - the element is a native `<button>` (`tagName === "BUTTON"`)
  - `type === "button"`
  - `aria-label` is set from `options.label`
  - the svg has `aria-hidden="true"`
  - `className` is still "icon-button"
  The tests "renders the icon" and "calls onClick" also still pass on the new `<button>` element, so the icon and click behaviour did not regress.
Not covered: No test checks that a keyboard can focus or activate the button (Tab, Enter or Space). A native `<button>` gives this behaviour, but no test shows it. The plan.md change (status draft → accepted, plus the acceptance record) is a doc change and needs no test.
Failures: none
Notes: The PR description input (pr.md) has only the two commit subject lines and no body, so I used `git diff main...HEAD` as the description of the change. The diff touches three files: plan.md, src/components.test.ts and src/icon-button.ts. In evidence.md the verifier summary still says "PENDING: the verifier report goes here." Working tree: evidence.md is untracked. No other changes.
```

## Raw output (verbatim, written by the engine)

The engine pasted the output of each run here. Nobody changed it.

### Run 1 of 5: exit code 0, 1.7 s

```text

> opieworks-sdlc-sandbox@0.0.0 verify /Users/dorian/project/opieworks-sdlc-sandbox
> pnpm typecheck && pnpm test


> opieworks-sdlc-sandbox@0.0.0 typecheck /Users/dorian/project/opieworks-sdlc-sandbox
> tsc --noEmit


> opieworks-sdlc-sandbox@0.0.0 test /Users/dorian/project/opieworks-sdlc-sandbox
> vitest run


 RUN  v3.2.4 /Users/dorian/project/opieworks-sdlc-sandbox

 ✓ src/components.test.ts (8 tests) 5ms

 Test Files  1 passed (1)
      Tests  8 passed (8)
   Start at  11:08:50
   Duration  325ms (transform 24ms, setup 0ms, collect 26ms, tests 5ms, environment 107ms, prepare 35ms)

```
