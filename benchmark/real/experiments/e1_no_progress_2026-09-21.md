# Real-repository benchmark results

- Runs: 4
- Passed: 3/4
- Tokens: 1,522,365
- Estimated cost: RMB 3.1633
- Cumulative runtime: 12.6 minutes

| Case | Strategy | Trial | Passed | Completed | Modified files | Gold recall | Tokens | Cost (RMB) |
|---|---|---:|---:|---:|---|---:|---:|---:|
| `pydantic__pydantic-8977` | `single` | 1 | true | false | `pydantic/_internal/_std_types_schema.py` | 100% | 575,993 | 1.1832 |
| `pydantic__pydantic-8977` | `single` | 1 | true | false | `pydantic/_internal/_std_types_schema.py` | 100% | 585,192 | 1.2213 |
| `iterative__dvc-4124` | `single` | 1 | false | true | `dvc/command/diff.py`, `dvc/command/metrics.py`, `dvc/command/params.py`, `dvc/utils/diff.py` | 100% | 265,896 | 0.5596 |
| `iterative__dvc-4185` | `single` | 1 | true | true | `dvc/dependency/param.py` | 100% | 95,284 | 0.1992 |
