# REVIEW.md

This file tells the reviewer role how to review a pull request. The opieworks-sdlc deploy stage installs it one time. After that, the file belongs to you. Setup never changes it. Change it to fit your repo.

The reviewer reads this file, the diff, the PR description, and the upstream artifacts of the work item (`intent.md`, `spec.md`, `plan.md`, `evidence.md`) when they exist.

## Passes

Do the three passes in this order. Each finding names its pass.

### 1. Bug pass

Find changes that do the wrong thing. Examples:

- Logic errors, wrong conditions, off-by-one errors, wrong operators.
- Unhandled errors, null or undefined values, and empty inputs that the change can get.
- Race conditions and wrong async or await use.
- A changed behavior that a caller depends on, with no update to that caller.
- A test that does not test what its name says, or that cannot fail.

### 2. Security pass

Find changes that make the code less safe. Examples:

- Secrets, tokens, or personal data in code, logs, or test fixtures.
- User input that reaches a shell, a query, a file path, or HTML with no check or escape.
- Missing or weaker authentication or permission checks.
- New dependencies from unknown sources, or versions that are not pinned.
- Changes to CI workflows, `CODEOWNERS`, or gate rules.

### 3. Compliance pass

Compare the change with the upstream artifacts and the repo rules. Examples:

- The change does not do what `intent.md` or `spec.md` asks, or does more than it asks.
- The change differs from `plan.md` and `plan.md` was not updated.
- The change breaks a rule in `AGENTS.md` or in a policy file of the repo.
- The PR description does not match the diff.

With no upstream artifact, compare the change with the PR description.

## Important

A finding is **important** when one of these is true:

- It is a bug that a user or a caller can hit.
- It is a security problem of any size.
- The change does not do what the intent, the spec, or the PR description says.
- It breaks a rule in `AGENTS.md`.

The gate owner must read every important finding before the approval.

## Nits

A **nit** is a small point that does not change behavior: a name, a comment, a clearer form of the same code. Report a nit only when it helps the next reader.

The nit cap is in `sdlc/config.toml` (key `nit_cap` in the `[deploy]` table). The engine shows nits up to the cap and counts the rest. List the most useful nits first.

## Do not report

Do not report these items. Count them in the `Not reported` line.

- Style that a formatter or a linter in the repo checks.
- Code that the pull request does not change, unless the change makes it wrong.
- Personal taste in names or structure when the current form is clear.
- Missing tests for code that the pull request does not change.
- Generated files and lock files, unless the change in them is a security problem.
- Points that an earlier review comment already raised and the author answered.

## Rank

Put important findings first. Inside a group, put the finding with the largest effect first.
