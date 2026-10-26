import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "scripts/verify_pytest_completion.py"
spec = importlib.util.spec_from_file_location("completion_guard", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

XML = '<testsuites><testsuite tests="2" errors="0" failures="0" skipped="1"><testcase classname="file" name="positive"/><testcase classname="file" name="skip"><skipped/></testcase></testsuite></testsuites>'


def test_complete_run_keeps_positive_and_skipped_counts():
    assert module.verify("2 tests collected in 0.1s", XML) == {
        "complete": True, "collected": 2, "passed": 1, "skipped": 1}


@pytest.mark.parametrize("collection,junit", [
    ("", XML),
    ("0 tests collected", XML),
    ("2 tests collected\n2 tests collected", XML),
    ("3 tests collected", XML),
    ("2 tests collected", XML.replace('tests="2"', 'tests="3"')),
    ("2 tests collected", XML.replace('errors="0"', 'errors="1"')),
    ("2 tests collected", XML.replace('<skipped/>', '<failure/>')),
    ("2 tests collected", XML.replace('name="skip"', 'name="positive"')),
    ("2 tests collected", XML.replace('skipped="1"', 'skipped="0"')),
    ("2 tests collected", XML.replace('name="positive"/>', 'name="positive"><skipped/></testcase>').replace('skipped="1"', 'skipped="2"')),
    ("2 tests collected", "<!DOCTYPE foo>" + XML),
])
def test_incomplete_or_falsely_green_evidence_is_rejected(collection, junit):
    with pytest.raises(ValueError):
        module.verify(collection, junit)
