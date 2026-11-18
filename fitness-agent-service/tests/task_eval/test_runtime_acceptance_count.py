from pathlib import Path

import pytest

from scripts.runtime_delivery_live_check import _unskipped_test_count


@pytest.mark.parametrize(
    ("xml", "count"),
    [
        ('<testsuites><testsuite tests="23" skipped="0"/></testsuites>', 23),
        ('<testsuites><testsuite tests="1" skipped="1"/></testsuites>', None),
        ('<testsuites><testsuite tests="0" skipped="0"/></testsuites>', None),
    ],
)
def test_acceptance_requires_executed_unskipped_tests(tmp_path: Path, xml: str, count: int | None):
    report = tmp_path / "report.xml"
    report.write_text(xml)
    if count is None:
        with pytest.raises(RuntimeError):
            _unskipped_test_count(report)
    else:
        assert _unskipped_test_count(report) == count
