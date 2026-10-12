---
work_item: 20260101-eval-fail
upstream: "none, used PR description and diff"
stage: testing
verdict: FAIL
---

# Evidence: 20260101-eval-fail

## Verify result

- Command: `python3 -m unittest calc_test`
- Runs: 2 of 2 allowed. The loop stops at the first pass.
- Verdict of the engine: **FAIL**

## Verifier report (summary, written by the verifier role)

PENDING: the verifier report goes here.

## Raw output (verbatim, written by the engine)

### Run 1 of 2: exit code 1, 0.2 s

```text
F
======================================================================
FAIL: test_add (calc_test.AddTest.test_add)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "calc_test.py", line 7, in test_add
    self.assertEqual(add(2, 3), 5, "expected 5, got 4")
AssertionError: 4 != 5 : expected 5, got 4

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (failures=1)
```

### Run 2 of 2: exit code 1, 0.2 s

```text
F
======================================================================
FAIL: test_add (calc_test.AddTest.test_add)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "calc_test.py", line 7, in test_add
    self.assertEqual(add(2, 3), 5, "expected 5, got 4")
AssertionError: 4 != 5 : expected 5, got 4

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (failures=1)
```
