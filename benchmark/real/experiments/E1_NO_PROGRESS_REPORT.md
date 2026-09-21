# E1: no-progress intervention off/on smoke (2026-09-21)

## Decision

**Do not enable W5 by default.** This four-run smoke does not show that the
intervention improves convergence. It is a case-level diagnostic, not a success-rate
estimate or a statistical comparison.

## Frozen setup

- Runtime code: `08a9d2d1d556284d1b97e45dbbfb4fa0249587a8`
- Model: `deepseek-chat`
- Per-run limits: 25 agent turns, 1,000,000 tokens, RMB 2
- Target pair: `pydantic__pydantic-8977`, W5 off versus W5 on (`N=9`)
- Regression smoke: `iterative__dvc-4124` and `iterative__dvc-4185`, W5 on
- The original append-only ledger was not modified. Raw rows are in
  `e1_no_progress_2026-09-21.jsonl`.

## Results

| Case | W5 | Tests | Exit | Tokens | First successful mutation | Intervention |
|---|---|---:|---|---:|---:|---|
| pydantic-8977 | off | pass | `max_steps` | 575,993 | step 13 | not applicable |
| pydantic-8977 | on | pass | `max_steps` | 585,192 | step 23 | step 9: `targeted_read`; next action was `search_code` |
| dvc-4124 | on | fail | `finish` | 265,896 | step 3 | not triggered |
| dvc-4185 | on | pass | `finish` | 95,284 | step 2 | not triggered |

Total: **1,522,365 tokens**, estimated **RMB 3.1633**, four runs in 12.6 minutes.

## Interpretation

1. The primary endpoint cannot support a W5 benefit: both Pydantic runs produced
   successful production mutations and passed the evaluator.
2. W5 did not solve convergence in this pair. Both runs exhausted all 25 turns;
   the on run used 9,199 more tokens and made its first successful mutation 10 turns later.
3. The intervention exposed an enforcement gap: after choosing `targeted_read`, the
   model issued another broad `search_code` call. The implementation has since been
   hardened locally so the decision tool is hidden before the trigger and the immediate
   next action is constrained to `read_file`/`read_symbol`. That change has only local
   fake-client coverage; it has not been tested with another paid run.
4. DVC regression smoke is mixed (1/2). Neither DVC run triggered W5, so the DVC-4124
   failure cannot be attributed to the injected prompt. The on protocol still exposed an
   extra tool schema, and model sampling is stochastic; n=1 cannot distinguish those
   effects from ordinary run variance.
5. The Pydantic off result also overturns the idea that the earlier zero-edit outcome was
   stable: under the frozen v2 protocol, an off run edited the gold file and passed.

## Provenance caveat

The first row records `worktree_dirty=false`. Rows 2-4 record `true` because the first
JSONL result and generated protocol manifests were then untracked files. Their
`tracked_diff_hash` is the SHA-256 of empty content, and all four rows have the same
`code_revision`; there were no tracked code changes between runs. Future provenance now
records tracked and untracked state separately. Raw rows remain unchanged.

## Next action

Keep W5 behind an opt-in flag. Do not spend more on repeated Pydantic trials. Before any
new paid validation, choose an unseen B-class target or an expanded multi-seed design and
freeze a new protocol containing the enforced post-decision state machine.
