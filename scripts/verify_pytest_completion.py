"""Reject a truncated/partial pytest run even if its process exited zero."""
import argparse
import re
from pathlib import Path
from defusedxml import ElementTree


def verify(collection: str, junit: str) -> dict:
    counts = re.findall(r"^(\d+) tests? collected\b", collection, re.MULTILINE)
    if len(counts) != 1 or int(counts[0]) < 1:
        raise ValueError("Missing or ambiguous pytest collection count")
    expected = int(counts[0])
    # CI-generated evidence only. Refuse DTD/entities rather than parsing them.
    if "<!DOCTYPE" in junit.upper() or "<!ENTITY" in junit.upper():
        raise ValueError("DTD/entities forbidden")
    root = ElementTree.fromstring(junit)
    cases = list(root.iter("testcase"))
    suites = list(root.iter("testsuite"))
    if not suites or len(cases) != expected:
        raise ValueError("JUnit does not cover every collected test")
    if sum(int(suite.attrib["tests"]) for suite in suites) != expected:
        raise ValueError("JUnit summary/count mismatch")
    if any(int(suite.attrib.get(key, "0")) for suite in suites for key in ("errors", "failures")):
        raise ValueError("JUnit reports failed/error outcomes")
    if any(case.find(key) is not None for case in cases for key in ("error", "failure")):
        raise ValueError("JUnit contains failed/error cases")
    identities = [(case.get("classname"), case.get("name")) for case in cases]
    if any(not all(identity) for identity in identities) or len(set(identities)) != expected:
        raise ValueError("Missing or duplicate testcase identity")
    skipped = sum(case.find("skipped") is not None for case in cases)
    if sum(int(suite.attrib.get("skipped", "0")) for suite in suites) != skipped:
        raise ValueError("JUnit skipped-count mismatch")
    if skipped == expected:
        raise ValueError("No executed passing tests")
    return dict(complete=True, collected=expected, passed=expected-skipped, skipped=skipped)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--collection", type=Path, required=True)
    cli.add_argument("--junit", type=Path, required=True)
    args = cli.parse_args()
    try:
        print(verify(args.collection.read_text(), args.junit.read_text()))
    except (OSError, ValueError, KeyError, ElementTree.ParseError) as error:
        cli.exit(1, f"Incomplete or invalid test evidence: {error}\n")
