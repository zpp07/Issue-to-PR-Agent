# Phase 3 benchmark dashboard

| Method | Cases | Hidden pass | Pass rate | Avg tokens | Avg cost (RMB) | Avg seconds | Test tampering |
|---|---:|---:|---:|---:|---:|---:|---:|
| planner | 22 | 21 | 95.5% | 11271 | 0.0321 | 67.70 | 0 |
| reviewer | 22 | 21 | 95.5% | 9725 | 0.0264 | 60.22 | 0 |
| single | 22 | 22 | 100.0% | 3586 | 0.0095 | 27.21 | 0 |

## Integrity

- Result rows / unique jobs: 66 / 66
- Public-test tampering attempts: 0
- Container timeouts: 0
- Total model cost: ¥1.4955

## Hidden-test failures

| Case | Method | Agent claimed pass | Tokens | Verification |
|---|---|---:|---:|---|
| case12_safe_divide | planner | True | 16664 | FAILED ../hidden/test_hidden.py::test_type_errors_propagate - Failed: DID NOT... |
| case22_transpose | reviewer | True | 16509 | FAILED ../hidden/test_hidden.py::test_empty - ValueError: matrix must be non-... |
