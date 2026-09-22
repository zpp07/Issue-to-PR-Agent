# E2: deterministic completion and bounded patch review

## Question

Test two mechanisms separately on one frozen real-repository protocol:

1. Does controller-driven completion reduce wasted post-verification work without
   reducing evaluator success?
2. Does one read-only Reviewer plus at most one bounded Reviser improve evaluator
   success enough to justify its extra token cost?

This experiment does not expose hidden tests, evaluator output, or gold patches to
Coder, Reviewer, or Reviser.

## Frozen tasks

- `iterative__dvc-4124`: previous candidate failure
- `iterative__dvc-4185`: previous success
- `pydantic__pydantic-8977`: long successful runs that did not call `finish`
- `facebookresearch__hydra-1791`: correct-file patch with a visible regression

The four tasks intentionally cover both prior successes and distinct failure modes.
They are selected before new results are observed.

## Arms

| Arm | Strategy | Controller auto-finish | Patch Reviewer/Reviser |
|---|---|---:|---:|
| A | `single` | off | no |
| B | `single` | on | no |
| C | `review_revise` | on | yes, at most one revision |

Each arm uses three independent provider-default stochastic trials per task. The API
does not receive an explicit random seed, so these are repeated trials rather than
claimed deterministic seeds.

## Budgets

- Coder: at most 25 LLM turns
- Reviewer: at most 6 LLM turns
- Reviser: at most 10 LLM turns and one revision pass
- Per run: at most 700,000 tokens and RMB 2.00 estimated model cost
- Maximum scheduled exposure: 36 runs and 25.2 million tokens

This maximum plus the earlier E1 usage remains below the user-approved cumulative
50 million token ceiling. Actual usage is expected to be lower because controller
completion and per-run caps stop runs early.

## Primary metrics

- independent hidden-test pass rate, reported per arm without mixing protocols;
- prompt/completion/total tokens and estimated RMB cost;
- evaluator pass-to-pass regression rate;
- exit reason and Agent completion;
- Reviewer pass rate, revision trigger rate, and revised-patch success;
- steps remaining when controller completion fires.

With 12 runs per arm, results are diagnostic rather than statistically definitive.
Raw JSONL remains append-only, and the report must retain null or negative findings.

## Pilot note

The first `review_revise-v3` smoke is retained but excluded from the planned arm C
summary. It exposed a control bug: an unstructured Reviewer timeout incorrectly
triggered a Reviser with no actionable issues. Protocol v4 restricts the Reviewer's
final turn to `review_finish` and treats any invalid conclusion as
`review_incomplete` without revision. A subsequent v4 preflight showed that the
provider can omit the required empty `issues=[]` when returning `passed=true`.
Protocol v5 normalizes only that unambiguous positive case; a negative review
without issues remains incomplete. The v5 preflight then revealed that a provider
may emit a tool call omitted from the current turn's schema. Protocol v6 rejects
all unexposed tool calls and reserves the final two Reviewer turns for
`review_finish`. Formal E2 rows use v6 only.

## Completion record (2026-09-22)

The formal v6 matrix is complete: 36/36 rows, all produced from clean revision
`189bd42542196f616072c8692a9b57036524ddb9` with one frozen evaluator protocol.

- Arm A: 7/12 hidden pass, 4,214,901 tokens, RMB 8.7336
- Arm B: 9/12 hidden pass, 2,945,377 tokens, RMB 6.1339
- Arm C: 8/12 hidden pass, 4,084,183 tokens, RMB 8.5394
- Formal total: 11,244,461 tokens, RMB 23.4069

One C run ended at 734,411 tokens because the pre-call budget check cannot stop an
already issued response. No failed infrastructure attempt was written as a formal
row. See `E2_REPORT.md` for confidence intervals, mechanism-level findings and the
negative Reviewer/Reviser result.
