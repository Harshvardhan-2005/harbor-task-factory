"""Compute exact Innodata/Harbor reward from pytest outcomes and write reward.txt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def compute_reward(
    outcomes: dict[str, str],
    collection_errors: list[str],
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> tuple[float, dict[str, object]]:
    """Apply the official grading matrix:

    1. If any regression test (pass_to_pass) fails/errors or is missing -> 0.0
    2. If all feature tests (fail_to_pass) pass -> 1.0
    3. Otherwise -> fraction of passing feature tests
    """
    if collection_errors:
        return 0.0, {
            "reason": "collection_errors",
            "collection_errors": collection_errors,
            "reward": 0.0,
        }

    regression_failures: list[dict[str, str]] = []
    for entry in pass_to_pass:
        if "::" in entry:
            status = outcomes.get(entry, "missing")
            if status not in ("passed", "skipped"):
                regression_failures.append({"nodeid": entry, "status": status})
        else:
            prefix = f"{entry}::"
            matched = [(nid, st) for nid, st in outcomes.items() if nid.startswith(prefix)]
            if not matched:
                regression_failures.append({"nodeid": entry, "status": "missing"})
            else:
                for nid, st in matched:
                    if st not in ("passed", "skipped"):
                        regression_failures.append({"nodeid": nid, "status": st})

    if regression_failures:
        return 0.0, {
            "reason": "regression_failure",
            "regression_failures": regression_failures,
            "reward": 0.0,
        }

    if not fail_to_pass:
        return 0.0, {"reason": "empty_fail_to_pass", "reward": 0.0}

    f2p_passed = [nodeid for nodeid in fail_to_pass if outcomes.get(nodeid) == "passed"]
    f2p_failed = [
        {"nodeid": nodeid, "status": outcomes.get(nodeid, "missing")}
        for nodeid in fail_to_pass
        if outcomes.get(nodeid) != "passed"
    ]

    if len(f2p_passed) == len(fail_to_pass):
        reward = 1.0
    else:
        reward = round(len(f2p_passed) / len(fail_to_pass), 4)

    return reward, {
        "reason": "scored",
        "reward": reward,
        "fail_to_pass_passed": len(f2p_passed),
        "fail_to_pass_total": len(fail_to_pass),
        "fail_to_pass_failed": f2p_failed,
        "pass_to_pass_total": len(pass_to_pass),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--fail-to-pass", required=True)
    parser.add_argument("--pass-to-pass", required=True)
    parser.add_argument("--reward-txt", required=True)
    parser.add_argument("--reward-json", required=True)
    args = parser.parse_args()

    res_path = Path(args.results)
    if res_path.is_file():
        data = json.loads(res_path.read_text(encoding="utf-8"))
    else:
        data = {"outcomes": {}, "collection_errors": ["missing_pytest_results_json"]}

    f2p = json.loads(Path(args.fail_to_pass).read_text(encoding="utf-8"))
    p2p = json.loads(Path(args.pass_to_pass).read_text(encoding="utf-8"))

    reward, details = compute_reward(
        data.get("outcomes", {}),
        data.get("collection_errors", []),
        f2p,
        p2p,
    )

    reward_txt = Path(args.reward_txt)
    reward_json = Path(args.reward_json)
    reward_txt.parent.mkdir(parents=True, exist_ok=True)
    reward_txt.write_text(f"{reward}\n", encoding="utf-8")
    reward_json.write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
