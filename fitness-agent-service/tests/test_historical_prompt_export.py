import pytest

from scripts.export_historical_prompt_baseline import historical_function


def test_pure_prompt_can_be_extracted_without_importing_historical_app() -> None:
    source = """
raise RuntimeError("historical startup must not run")
def _system_prompt(route: str, locale: str) -> str:
    spec = domain_agent_spec(route)
    return f"{spec.display_name}: {locale}"
"""
    build = historical_function(source)
    assert "zh-CN" in build("FITNESS_COACHING", "zh-CN")


@pytest.mark.parametrize(
    "body",
    ["import os", "return open('/tmp/forbidden')", "return spec.__class__", "while True: pass"],
)
def test_non_pure_historical_prompt_is_rejected(body: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        historical_function(f"def _system_prompt(route, locale):\n    {body}\n")
