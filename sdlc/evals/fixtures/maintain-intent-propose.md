---
work_item: 20260901-test-seconds-propose-breach
upstream:
  - "issue #8"
  - "sdlc-data:sdlc/maintain/readings/test-seconds.csv"
stage: intent
source: maintain
lane: full
tier: propose
---

# Intent: test-seconds left its band

The maintain stage wrote this intent from a detect finding. The gate owner accepts it with a merge, or rejects it with a close and a reason.

## Signal (written by the engine)

- Metric: `test-seconds`
- Command: `python3 scripts/test_seconds.py`
- Reading: 6.9 at 2026-09-01T06:23:00Z
- Baseline: mean 6.023333, sigma 0.156873
- Deviation: 5.588378 sigma
- Tier: **propose**
- Readings: `sdlc-data:sdlc/maintain/readings/test-seconds.csv`
- Detect issue: #8

## Diagnosis (summary, written by the diagnoser role)

This section is a summary. The diagnoser role wrote it with a fresh context. The diagnoser reads only.

PENDING: the diagnoser report goes here.

## Problem

PENDING: what is wrong, in one or two sentences. Use the signal and the diagnosis.

## Outcome

PENDING: the result that the fix must give. Name the metric and its band.

## Affected users and systems

PENDING: who and what the problem affects.

## Constraints

PENDING: limits on the fix, or "none found".

## Open questions

PENDING: questions for the gate owner, or "none".

## Lane

Proposed lane: full. The gate owner sets the lane at the Plan gate. The fast lane is only for a bug with no API, token, or visual change.

## Done when

PENDING: the check that proves the fix. Include: the metric `test-seconds` is back inside its band for the next detect run.
