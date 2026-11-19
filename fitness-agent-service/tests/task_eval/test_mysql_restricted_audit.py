from types import SimpleNamespace

import pytest

from app.evaluation.task_eval import mysql_restricted_audit as audit
from app.evaluation.task_eval.sandbox_preflight import PreflightError


def responses(*, changed_grant=False, changed_trigger=False, changed_owner=False):
    triggers = [
        f"{table}\t{event}\t{audit._OWNER}@localhost\t"
        + audit._BODY.format(table=table, event=event)
        for table in audit._TABLES
        for event in audit._EVENTS
    ]
    if changed_trigger:
        triggers.pop()

    def sql(_cid, command):
        if "information_schema.TRIGGERS" in command:
            return "\n".join(triggers)
        if "account_locked" in command:
            return f"{len(audit._TABLES)}\n1\n0\nY"
        if "SHOW GRANTS" in command:
            if audit._OWNER in command:
                grants = {
                    "GRANT USAGE ON *.* TO `eval_audit_owner`@`localhost`",
                    "GRANT INSERT ON `fitness_eval_audit`.`events` TO `eval_audit_owner`@`localhost`",
                } | {
                    f"GRANT TRIGGER ON `fitness_eval`.`{table}` TO `eval_audit_owner`@`localhost`"
                    for table in audit._TABLES
                }
                if changed_owner:
                    grants.add("GRANT ALL PRIVILEGES ON *.* TO `eval_audit_owner`@`localhost`")
                return "\n".join(grants)
            user = audit._BOOKING if audit._BOOKING in command else audit._GATEWAY
            grants = {
                f"GRANT USAGE ON *.* TO `{user}`@`%`",
                f"GRANT SELECT ON `fitness_eval`.* TO `{user}`@`%`",
            }
            if user == audit._BOOKING:
                grants |= {
                    f"GRANT INSERT, UPDATE, DELETE ON `fitness_eval`.`{table}` TO `{user}`@`%`"
                    for table in audit._TABLES
                }
                if changed_grant:
                    grants.add(f"GRANT UPDATE ON `fitness_eval_audit`.* TO `{user}`@`%`")
            return "\n".join(grants)
        raise AssertionError("unexpected SQL")

    return sql


def test_verify_rejects_modified_trigger_and_extra_privilege(monkeypatch):
    monkeypatch.setattr(audit, "_container", lambda: ("run", "container"))
    monkeypatch.setattr(audit, "_sql", responses())
    calls = []

    def client(_cid, user, _sql, *, deny=False):
        calls.append((user, deny))
        return "" if deny else f"{user}@%"

    monkeypatch.setattr(audit, "_client", client)
    result = audit.verify()
    assert result["status"] == "VERIFIED" and result["trigger_count"] == 30
    assert sum(denied for _, denied in calls) == 8
    monkeypatch.setattr(audit, "_sql", responses(changed_trigger=True))
    with pytest.raises(PreflightError, match="TRIGGER_INTEGRITY"):
        audit.verify()
    monkeypatch.setattr(audit, "_sql", responses(changed_grant=True))
    with pytest.raises(PreflightError, match="GRANTS_MISMATCH"):
        audit.verify()
    monkeypatch.setattr(audit, "_sql", responses(changed_owner=True))
    with pytest.raises(PreflightError, match="AUDIT_OWNER_GRANTS_MISMATCH"):
        audit.verify()


def test_observations_explicitly_do_not_claim_complete_window(monkeypatch):
    monkeypatch.setattr(audit, "_container", lambda: ("run", "container"))
    monkeypatch.setattr(
        audit,
        "_sql",
        lambda _cid, query: "RUNNING\t0\t1" if "innodb_trx" in query else "appointment\tINSERT\t1",
    )
    observed = audit.observe()
    assert observed["records"] == [{"table": "appointment", "operation": "INSERT", "count": 1}]
    assert observed["audit_window_complete"] is False
    assert observed["cross_database_causality"] == "UNKNOWN"
    assert observed["transaction_sample"] == {
        "state_counts": [{"state": "RUNNING", "read_only": False, "count": 1}],
        "freshness": "NOT_GUARANTEED",
        "writers_drained": "UNKNOWN",
        "blocks_future_writes": False,
    }
    monkeypatch.setattr(audit, "_sql", lambda *_args: "arbitrary_table\tINSERT\t1")
    with pytest.raises(PreflightError, match="INVALID_AUDIT_OBSERVATION"):
        audit.observe()


def test_empty_transaction_sample_never_proves_writers_drained(monkeypatch):
    monkeypatch.setattr(audit, "_sql", lambda *_args: "")
    sample = audit._observe_transactions("container")
    assert sample["state_counts"] == []
    assert sample["writers_drained"] == "UNKNOWN"
    assert sample["blocks_future_writes"] is False


@pytest.mark.parametrize(
    "raw", ["INVALID\t0\t1", "RUNNING\t2\t1", "RUNNING\t0\t0", "RUNNING\t0\t1\nRUNNING\t0\t1"]
)
def test_invalid_transaction_samples_are_rejected(monkeypatch, raw):
    monkeypatch.setattr(audit, "_sql", lambda *_args: raw)
    with pytest.raises(PreflightError, match="INVALID_TRANSACTION_OBSERVATION"):
        audit._observe_transactions("container")


def test_login_failure_is_not_proof_of_permission_denial(monkeypatch):
    monkeypatch.setenv("TASK_EVAL_BOOKING_DB_PASSWORD", "a" * 48)
    monkeypatch.setattr(
        audit.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stderr="ERROR 1045 (28000): authentication failed", stdout=""
        ),
    )
    with pytest.raises(PreflightError, match="REQUIRED_DENIAL_MISSING"):
        audit._client("container", audit._BOOKING, "DROP TRIGGER x", deny=True)
