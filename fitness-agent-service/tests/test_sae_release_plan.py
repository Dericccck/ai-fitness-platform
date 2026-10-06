import pytest

from scripts.sae_release_plan import IMAGES, REQUIRED, build_plan


def config() -> dict:
    return {
        "region_id": "cn-hangzhou",
        "images": {name: f"registry.test/fitness/{name}@sha256:" + "a" * 64 for name in IMAGES},
        "applications": {
            name: f"00000000-0000-0000-0000-{index:012d}"
            for index, name in enumerate(sorted(REQUIRED | {"notification-worker"}))
        },
    }


def test_offline_plan_uses_topology_and_independent_worker_entrypoint() -> None:
    plan = build_plan(config())
    assert plan["executed"] is False and plan["cloud_verified"] is False
    assert [stage["parallel_updates"][0]["application"] for stage in plan["stages"][1:]] == [
        "gateway",
        "agent-api",
        "notification-worker",
        "web",
    ]
    worker = plan["stages"][3]["parallel_updates"][0]
    assert worker["parameters"]["CommandArgs"] == '["-m", "app.notification_worker_main"]'


@pytest.mark.parametrize("problem", ["tag", "placeholder", "same_app", "secret", "missing"])
def test_release_plan_rejects_unsafe_or_incomplete_config(problem: str) -> None:
    data = config()
    if problem == "tag":
        data["images"]["agent"] = "registry.test/agent:latest"
    elif problem == "placeholder":
        data["applications"]["web"] = "REPLACE_SAE_APP_ID"
    elif problem == "same_app":
        data["applications"]["web"] = data["applications"]["gateway"]
    elif problem == "secret":
        data["token"] = "must-not-be-in-plan"
    else:
        del data["applications"]["training"]
    with pytest.raises(ValueError):
        build_plan(data)
