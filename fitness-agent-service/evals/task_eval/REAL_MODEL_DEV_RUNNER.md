# Fixed real-model development trials

The standard task runner can launch the existing isolated BOOK-018, BOOK-026 or BOOK-027
development harness with the fixed synthetic fixture and an explicit real model
configuration. It emits `manifest.json`, `trials.jsonl`, `summary.json` and
`report.md`, and returns 3 for an incomplete task or 2 for a verified task
failure. Each run executes only one named case. It creates disposable local
services and requires a new output directory.

```sh
.venv/bin/python -m app.evaluation.task_eval.cli run \
  --dataset evals/task_eval/datasets/v1.jsonl --split dev \
  --mode integration_sandbox --execute --dev-real-model-case BOOK-018 \
  --credential-file /absolute/private/key-file \
  --budget-file /absolute/existing-budget.sqlite \
  --budget-limit-nano EXISTING_LIMIT --budget-opening-nano EXISTING_OPENING \
  --minimum-reserved-nano CONSERVATIVE_MINIMUM --confirm-current-pricing \
  --output-dir /absolute/new-report-directory
```

Use `BOOK-026` or `BOOK-027` for the other fixed cases. Supply the existing ledger identity and
minimum reserved history; the program does not create or reset a budget. The
credential file must be private, and its value is never a command-line argument
or included in the report. Confirm current provider pricing before use. The
local reservation is not a provider-side spending cap.

For `ROUT-003`, `ROUT-005`, `ROUT-009`, `ROUT-010`, `ROUT-011`,
`ROUT-012`, `ROUT-017`, or `ROUT-019`,
use the same options with that case ID and `--mode model_sandbox`. Each run
observes the real Supervisor and routing model with an empty tool registry and
the domain model disabled. It is a routing component observation, not evidence
that the full task or business tools succeeded.

The manifest records the configured model and whether a call was attempted;
configuration alone does not establish a successful response. Results remain
draft and incomplete because these fixtures, causal audit coverage and required
human reviews are not release evidence. BOOK-026's supplemental snapshot
assertion references are hashes of the recorded local snapshot checks, not
independent audit attestations.

The fixed KNOW-020 development semantic judge can be run with
`python -m app.evaluation.task_eval.dev_semantic_judge_cli --help`. It requires
a private prior observation and answer file, the existing budget, an explicit
execution switch and confirmed current pricing. The resulting model judgment
is an uncalibrated review proposal: the observation is an unauthenticated
caller claim and the task remains UNKNOWN until independently verified and
reviewed. The command writes a new private output file and returns 3 even when
the model provides a valid proposal.

For `KNOW-001` through `KNOW-007`, `KNOW-020`, `SAFE-013` and `SAFE-014`,
`python -m app.evaluation.task_eval.dev_review_probe --help` describes the
separate fixed-input Supervisor observation. Each invocation runs one case,
requires an existing private `0700` review directory and a new report file, and
uses the existing budget ledger. The empty registry prevents business tool
calls; it also means this observation does not exercise retrieval or establish
that a knowledge answer is grounded. The answer is stored in a private file for
review, never published in the report. Deterministic safety guards can complete
without a model call; consult `attempts` instead of assuming a provider call.
An observed response is not an assessment pass: the task remains UNKNOWN and
requires the case's actual evidence and human review.

The isolated cross-service launcher supports `--booking-positive-real-model-trial`
for v1 cases BOOK-001, BOOK-002, BOOK-003, BOOK-005 and BOOK-007. Supply the
case ID, a new absolute report path and the same private credential, existing
budget and current-price confirmation used by the other real-model trials.
Each trial uses a fixed synthetic setup conversation, a fixture-bound unreviewed
grading proposal and an added repeat of the same approval request. BOOK-001,
BOOK-002 and BOOK-003 in v1 differ from the identically numbered original-plan
examples; their reports explicitly retain that distinction. The trials do not
establish independent audit completeness or release acceptance.
