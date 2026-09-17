# Real-repository benchmark results

- Runs: 11
- Passed: 3/11
- Tokens: 2,743,288
- Estimated cost: RMB 5.7008
- Cumulative runtime: 39.3 minutes

| Case | Strategy | Trial | Passed | Completed | Modified files | Gold recall | Tokens | Cost (RMB) |
|---|---|---:|---:|---:|---|---:|---:|---:|
| `iterative__dvc-4124` | `single` | 1 | true | true | `dvc/utils/diff.py` | 100% | 53,220 | 0.1144 |
| `facebookresearch__hydra-1791` | `single` | 1 | false | false | - | 0% | 262,798 | 0.5407 |
| `iterative__dvc-4185` | `single` | 1 | true | true | `dvc/dependency/param.py` | 100% | 234,436 | 0.4894 |
| `pydantic__pydantic-8977` | `single` | 1 | false | false | - | 0% | 282,613 | 0.5910 |
| `facebookresearch__hydra-1006` | `single` | 1 | false | false | `hydra/_internal/utils.py` | 100% | 357,154 | 0.7347 |
| `dask__dask-7894` | `single` | 1 | true | true | `dask/array/overlap.py` | 100% | 159,097 | 0.3349 |
| `facebookresearch__hydra-1791` | `single` | 2 | false | false | - | 0% | 250,183 | 0.5138 |
| `pydantic__pydantic-8977` | `single` | 2 | false | false | - | 0% | 288,847 | 0.5976 |
| `facebookresearch__hydra-1006` | `single` | 2 | false | false | `hydra/_internal/utils.py` | 100% | 315,230 | 0.6457 |
| `facebookresearch__hydra-1791` | `plan_execute` | 1 | false | false | - | 0% | 60,914 | 0.1274 |
| `facebookresearch__hydra-1791` | `plan_execute` | 2 | false | false | `hydra/core/default_element.py` | 100% | 478,796 | 1.0112 |
