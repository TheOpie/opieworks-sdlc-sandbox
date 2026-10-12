<!-- opieworks-sdlc:testing:begin -->
## opieworks-sdlc: testing stage

Done means: the verify command in `sdlc/config.toml` (key `verify` in the `[testing]` table) exits 0, and `sdlc/work/<id>/evidence.md` holds its raw output.

- Do not call a work item done until the verify command passes. Do not change the verify command or a test to make the result pass.
- Only the engine writes raw output into evidence.md. Do not edit raw output. Label every summary as a summary.
- The verifier role reports only. It does not edit files.
- A pull request that changes an AGENTS.md file or a watched path in `sdlc/` needs a fresh eval result. A human runs `run-evals` and commits `sdlc/evals/results/latest.json`. The opieworks-sdlc-testing skill tells how.

Setup rewrites this section. Put your own rules outside the markers.
<!-- opieworks-sdlc:testing:end -->

<!-- opieworks-sdlc:intent:begin -->
## opieworks-sdlc: intent stage

Every work item starts with `sdlc/work/<id>/intent.md`. It states the problem, the outcome, the affected users and systems, the constraints, the open questions, the lane, and the done-when checks.

- A human accepts an intent when the intent pull request (label `sdlc:intent`) merges. A human rejects it when the pull request closes. Then the reason goes to `sdlc/triage/`. No agent accepts or rejects an intent.
- The fast lane is only for a bug with no API change, no token change, and no visual change. Every other work item uses the full lane. The human sets the lane at the intent gate.
- The intent analyst role reports only. It does not edit files.
- The opieworks-sdlc-intent skill tells how to write an intent.

Setup rewrites this section. Put your own rules outside the markers.
<!-- opieworks-sdlc:intent:end -->

<!-- opieworks-sdlc:design:begin -->
## opieworks-sdlc: design stage

The design stage turns `sdlc/work/<id>/intent.md`, or an inline problem statement, into `sdlc/work/<id>/spec.md`. It checks the spec against each policy file in `sdlc/config.toml` (key `policies` in the `[design]` table).

- Never guess past a conflict with a policy or in the intent. Write a flag in the Flags section of spec.md. Only a human resolves a flag.
- spec.md always has a Flags section. When there is no flag, the section holds the line `None.`.
- Do not weaken a policy file or change the intent to remove a flag.
- The spec merges only when `spec-check` gives no open flag. The opieworks-sdlc-design skill tells how.

Setup rewrites this section. Put your own rules outside the markers.
<!-- opieworks-sdlc:design:end -->

<!-- opieworks-sdlc:build:begin -->
## opieworks-sdlc: build stage

- Do not change code before the human accepts the plan in `sdlc/work/<id>/plan.md`. In the full lane, the merge of the plan PR is the acceptance. In the fast lane, the human accepts the plan in the session (D17).
- The planner role reads only. It does not edit files.
- The fast lane is a bug fix with no API, design token, or visual change. When the work needs one, stop, push nothing, and send the work item to the design stage.
- The fast lane commits a failing test first. Do not change a locked test (`locked_tests` in plan.md). The `sdlc-checks` job fails a pull request when a later commit changes a locked test.
- When the code differs from the plan, update the drift log in plan.md.
- The opieworks-sdlc-build skill tells how.

Setup rewrites this section. Put your own rules outside the markers.
<!-- opieworks-sdlc:build:end -->

<!-- opieworks-sdlc:deploy:begin -->
## opieworks-sdlc: deploy stage

- The reviewer role reviews a pull request with the rules in `REVIEW.md`. The bot posts the ranked findings as a comment. A comment is not an approval.
- The babysitter role fixes review comments and failed checks for at most `babysit_rounds` rounds (in the `[deploy]` table of `sdlc/config.toml`). After each push, the gate owner approves again. After the last round, the babysitter stops and hands off to the human.
- Agents may make a canary, which is a prerelease. Agents never make a full release.
- Production runs only in the workflow `sdlc-deploy-production`. Its job uses the protected `production` environment. The bot can start it. Only the gate owner approves it.
- Do not approve, merge, or start a deploy in another way. Do not edit `REVIEW.md`, the runbooks, or the workflows to pass a gate.
- To roll back, follow `sdlc/runbooks/rollback.md`.

Setup rewrites this section. Put your own rules outside the markers.
<!-- opieworks-sdlc:deploy:end -->

<!-- opieworks-sdlc:maintain:begin -->
## opieworks-sdlc: maintain stage

Bands: `sdlc/bands.toml` names each metric, the command that prints its number, its baseline, and its tiers. A human starts the detect job by hand. It uses no model. It logs at 1 sigma, opens an issue at 2 sigma (diagnose) and at 3 sigma (propose), and comments on the issue when the breach repeats. The readings are on the `sdlc-data` branch.

- Only a human starts the diagnoser, the scanner, and the closer. No agent job runs on a schedule.
- The diagnoser reads only. At 2 sigma it reports a cause. At 3 sigma it also proposes one change. A finding becomes a new intent.md pull request. Only a human accepts or rejects it.
- Every finding gets a triage record in `sdlc/triage/`: fix now, schedule, or dismiss with a reason.
- After a bug fix merges, the closer adds an eval task in `sdlc/evals/tasks/` and a lesson in `sdlc/lessons/`. Read `sdlc/lessons/` before you change code that a lesson names.
- Do not change `sdlc/bands.toml` to hide a breach. Do not change the readings on the `sdlc-data` branch.

Setup rewrites this section. Put your own rules outside the markers.
<!-- opieworks-sdlc:maintain:end -->
