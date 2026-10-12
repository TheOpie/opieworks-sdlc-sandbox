<!-- opieworks-sdlc:testing:begin -->
## opieworks-sdlc: testing stage

Done means: the verify command in `sdlc/config.toml` (key `verify` in the `[testing]` table) exits 0, and `sdlc/work/<id>/evidence.md` holds its raw output.

- Do not call a work item done until the verify command passes. Do not change the verify command or a test to make the result pass.
- Only the engine writes raw output into evidence.md. Do not edit raw output. Label every summary as a summary.
- The verifier role reports only. It does not edit files.
- A pull request that changes an AGENTS.md file or a watched path in `sdlc/` needs a fresh eval result. A human runs `run-evals` and commits `sdlc/evals/results/latest.json`. The opieworks-sdlc-testing skill tells how.

Setup rewrites this section. Put your own rules outside the markers.
<!-- opieworks-sdlc:testing:end -->
