"""Read one organization row from a verified disposable booking database.

The business owner stated that each organization corresponds to one store;
this snapshot does not independently attest that policy or user intent.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .mysql_booking_observer import _COMMAND, _ID, _literal
from .mysql_sandbox_preflight import verify_container
from .sandbox_preflight import PreflightError, _inspect


def read_organization_store(
    *, run_id: str, container_id: str, organization_id: str
) -> dict[str, Any]:
    organization = _literal(organization_id, _ID)
    verify_container(_inspect(container_id), run_id=run_id)
    sql = f"""
SET TRANSACTION READ ONLY;
START TRANSACTION WITH CONSISTENT SNAPSHOT;
SELECT JSON_OBJECT('database', DATABASE(), 'identity', CURRENT_USER(), 'organization',
 (SELECT JSON_OBJECT('id', id, 'name', name) FROM organization WHERE id={organization} LIMIT 1));
COMMIT;
"""
    try:
        output = subprocess.run(
            ["docker", "exec", container_id, "sh", "-c", _COMMAND, "organization-store", sql],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        value = json.loads(output.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PreflightError("ORGANIZATION_STORE_READ_FAILED") from exc
    if (
        not isinstance(value, dict)
        or value.get("database") != "fitness_eval"
        or value.get("identity") != "root@localhost"
    ):
        raise PreflightError("ORGANIZATION_STORE_IDENTITY_MISMATCH")
    row = value.get("organization")
    if row is not None and (
        not isinstance(row, dict)
        or set(row) != {"id", "name"}
        or row["id"] != organization_id
        or not isinstance(row["name"], str)
        or not row["name"].strip()
    ):
        raise PreflightError("ORGANIZATION_STORE_ROW_INVALID")
    return {
        "origin": "disposable_mysql_consistent_read",
        "organization": row,
        "snapshot_complete": True,
        "mapping_basis": "user_stated_one_store_per_organization",
        "mapping_independently_reviewed": False,
    }
