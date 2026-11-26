"""Execute pytest and emit exact nodeid outcomes to JSON without crashing test.sh."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import pytest


class ResultCollector:
    """Pytest plugin recording canonical nodeid statuses across setup, call, and teardown."""

    def __init__(self) -> None:
        self.outcomes: dict[str, str] = {}
        self.collection_errors: list[str] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        nodeid = report.nodeid
        if report.when == "setup":
            if report.failed:
                self.outcomes[nodeid] = "error"
            elif report.skipped:
                self.outcomes[nodeid] = "skipped"
        elif report.when == "call":
            self.outcomes[nodeid] = report.outcome
        elif report.when == "teardown" and report.failed:
            self.outcomes[nodeid] = "error"

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_errors.append(report.nodeid)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--target", default="tests")
    args = parser.parse_args()

    collector = ResultCollector()
    pytest_args = ["-q", "--tb=short"]
    if hasattr(pytest, "PytestRemovedIn10Warning"):
        pytest_args.extend(["-W", "ignore::pytest.PytestRemovedIn10Warning"])
    pytest_args.append(args.target)
    exit_code = pytest.main(pytest_args, plugins=[collector])
    payload = {
        "pytest_exit_code": int(exit_code),
        "outcomes": collector.outcomes,
        "collection_errors": collector.collection_errors,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
