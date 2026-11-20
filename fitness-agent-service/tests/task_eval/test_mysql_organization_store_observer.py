import json
from types import SimpleNamespace

import pytest

from app.evaluation.task_eval import mysql_organization_store_observer as observer
from app.evaluation.task_eval.sandbox_preflight import PreflightError


def test_store_organization_is_scoped_to_verified_disposable_mysql(monkeypatch):
    calls = []
    monkeypatch.setattr(observer, "_inspect", lambda cid: {"id": cid})
    monkeypatch.setattr(
        observer, "verify_container", lambda value, run_id: calls.append((value, run_id))
    )

    def execute(command, **kwargs):
        assert "SET TRANSACTION READ ONLY" in command[-1]
        assert "WHERE id=CONVERT(0x" in command[-1]
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "database": "fitness_eval",
                    "identity": "root@localhost",
                    "organization": {"id": "org-1", "name": "test organization"},
                }
            )
        )

    monkeypatch.setattr(observer.subprocess, "run", execute)
    result = observer.read_organization_store(
        run_id="run-1", container_id="cid-1", organization_id="org-1"
    )
    assert calls == [({"id": "cid-1"}, "run-1")]
    assert result["organization"]["id"] == "org-1"
    assert result["mapping_independently_reviewed"] is False


def test_store_observer_rejects_wrong_scope(monkeypatch):
    monkeypatch.setattr(observer, "_inspect", lambda cid: {"id": cid})
    monkeypatch.setattr(observer, "verify_container", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        observer.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(
                {
                    "database": "fitness_eval",
                    "identity": "root@localhost",
                    "organization": {"id": "other", "name": "other store"},
                }
            )
        ),
    )
    with pytest.raises(PreflightError, match="ORGANIZATION_STORE_ROW_INVALID"):
        observer.read_organization_store(
            run_id="run-1", container_id="cid-1", organization_id="org-1"
        )
