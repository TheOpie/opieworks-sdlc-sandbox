# Sample policy

This is the sample policy of the opieworks-sdlc design stage. The spec-writer checks every spec against each policy file in `[design].policies` of `sdlc/config.toml`. Replace these rules with the rules of your team, or add more policy files.

Each rule has an ID. A flag in a spec names the policy file and the rule ID.

## Rules

- POL-1 Accessibility: Every interactive element has an accessible name and works with a keyboard only. Text has a contrast ratio of 4.5:1 or more.
- POL-2 Public API: Do not remove or rename a public function, prop, endpoint, or field in a minor release. Mark it as deprecated for one minor release first.
- POL-3 Personal data: Do not show, log, or export personal data (names, email addresses, phone numbers) to a user who is not signed in. Do not write personal data to logs.
- POL-4 Dependencies: Do not add a runtime dependency without a named owner and a reason in the spec.
- POL-5 Secrets: Do not put a secret, token, or password in code, config, or a test fixture. Read secrets from the environment.
