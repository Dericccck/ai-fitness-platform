# OPER-009–016 synthetic notification probes

Run the local, network-free development observations from the service directory:

```sh
.venv/bin/python -m app.evaluation.task_eval.outreach_draft_fixtures
```

The command emits JSON; exit status 1 means an available component probe observed a
failure. Exit status 0 only means no component failure was observed; it is not an
acceptance result. Each task remains `UNKNOWN`. The original planning dataset is
unchanged, and the fixture and each corresponding source case are hash-bound.

The eight distinct synthetic recipients and organizations and expected counts are
unreviewed proposals. For OPER-009–013 the production worker and preference policy
are exercised against in-memory databases and a recording fake delivery adapter.
OPER-013 injects the stale signal; it does not test how a candidate becomes stale.
The other three cases expose existing partial checks while their component grades
remain `UNKNOWN`: deduplicated end-to-end delivery, the mapping between a withdrawn
consent and notification preference, and recipient eligibility require further
evidence. No production data, real notification channel, database transaction,
or distributed delivery window is exercised.

The OPER-014–016 source cases are in the test split and these proposals were made
after inspecting them. They must receive independent review for test-set leakage
before any use in a release evaluation. No automated result here substitutes for
case approval or establishes a release gate pass.

The draft parameter proposal incorporates the synthetic recipient and expected
immediate-window count for OPER-009–013. For OPER-010, zero means no delivery
inside the local quiet-hours observation window; a later allowed delivery is not
ruled out. OPER-012's consent and eligibility remain unverified. OPER-014–016 do
not gain a recipient or an automatically passing eligibility contract.

To reproduce the checked-in proposal, write to a new directory and compare both
files byte for byte:

```sh
.venv/bin/python -m app.evaluation.task_eval.draft_parameters \
  --dataset evals/task_eval/datasets/v1.jsonl --output-dir /tmp/outreach-proposal-new
cmp /tmp/outreach-proposal-new/cases.jsonl evals/task_eval/draft_parameter_proposal_v1/cases.jsonl
cmp /tmp/outreach-proposal-new/manifest.json evals/task_eval/draft_parameter_proposal_v1/manifest.json
.venv/bin/pytest -q tests/task_eval/test_draft_parameters.py
```

The output directory must not already exist. Matching hashes establish only that
the proposed parameters and fixture are unchanged, not that the proposal was
reviewed or that a task passed.
