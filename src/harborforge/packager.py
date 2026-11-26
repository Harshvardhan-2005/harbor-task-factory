"""Harbor v0.25.x compliant task package emitter and TaskConfig validator."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .models import RepoProfile, SynthesizedArtifact


def package_harbor_task(
    artifact: SynthesizedArtifact,
    profile: RepoProfile,
    *,
    output_root: str | Path,
) -> Path:
    """Emit a self-contained Harbor v0.25.x task folder with zero test leakage."""
    task_dir = Path(output_root).resolve() / artifact.proposal.task_id
    if task_dir.exists():
        shutil.rmtree(task_dir)

    env_dir = task_dir / "environment"
    sol_dir = task_dir / "solution"
    tests_dir = task_dir / "tests"
    for folder in (env_dir, sol_dir, tests_dir):
        folder.mkdir(parents=True, exist_ok=True)

    # 1. instruction.md (the ONLY file visible to the evaluated agent)
    (task_dir / "instruction.md").write_text(artifact.instruction_md.strip() + "\n", encoding="utf-8")

    # 2. task.toml (metadata, exact 40-char base_commit, license, timeouts, resources)
    (task_dir / "task.toml").write_text(_render_task_toml(artifact, profile), encoding="utf-8")

    # 3. environment/Dockerfile (plus optional offline repo bundle if remote_url is local)
    is_local_remote = profile.remote_url.startswith("file://") or Path(profile.remote_url).exists()
    if is_local_remote:
        bundle_path = env_dir / "repo.bundle"
        subprocess.run(
            ["git", "bundle", "create", str(bundle_path), "HEAD"],
            cwd=profile.repo_path,
            check=True,
            capture_output=True,
        )
    (env_dir / "Dockerfile").write_text(
        _render_dockerfile(profile, use_bundle=is_local_remote),
        encoding="utf-8",
    )

    # 4. solution/solution.patch + solution/solve.sh (inline heredoc works for /oracle or /solution)
    patch_text = artifact.patch_diff if artifact.patch_diff.endswith("\n") else artifact.patch_diff + "\n"
    (sol_dir / "solution.patch").write_text(patch_text, encoding="utf-8")

    solve_sh = _render_solve_sh(patch_text)
    solve_path = sol_dir / "solve.sh"
    solve_path.write_text(solve_sh, encoding="utf-8")
    _make_executable(solve_path)

    # 5. tests/ (isolated feature test file, SWE-bench JSONs, collector, grader, test.sh)
    test_dest = task_dir / artifact.test_file_rel_path
    test_dest.parent.mkdir(parents=True, exist_ok=True)
    test_dest.write_text(artifact.test_file_content, encoding="utf-8")

    (tests_dir / "fail_to_pass.json").write_text(
        json.dumps(artifact.fail_to_pass, indent=2) + "\n", encoding="utf-8"
    )
    (tests_dir / "pass_to_pass.json").write_text(
        json.dumps(artifact.pass_to_pass, indent=2) + "\n", encoding="utf-8"
    )
    (tests_dir / "run_pytest_collector.py").write_text(_PYTEST_COLLECTOR_SCRIPT, encoding="utf-8")
    (tests_dir / "grade_harbor.py").write_text(_HARBOR_GRADER_SCRIPT, encoding="utf-8")

    test_sh_path = tests_dir / "test.sh"
    test_sh_path.write_text(
        _render_test_sh(artifact.test_file_rel_path),
        encoding="utf-8",
    )
    _make_executable(test_sh_path)

    # Save generation metadata alongside package? NO - keep task directory strictly Harbor-clean!
    # Store auxiliary manifest only outside or validate with Harbor TaskConfig if installed.
    validate_task_toml_schema(task_dir / "task.toml")
    return task_dir


def validate_task_toml_schema(task_toml_path: Path) -> tuple[bool, str]:
    """Validate task.toml syntax via tomllib and against harbor==0.25.* TaskConfig when installed."""
    import tomllib

    raw = tomllib.loads(task_toml_path.read_text(encoding="utf-8"))
    meta = raw.get("metadata", {})
    base_commit = meta.get("base_commit", "")
    if not isinstance(base_commit, str) or len(base_commit) != 40:
        raise ValueError(f"task.toml metadata.base_commit must be a 40-char hash, got {base_commit!r}")

    try:
        from harbor.models.task.config import TaskConfig  # type: ignore[import-not-found]
    except ImportError:
        return True, "Validated with Python tomllib (harbor package not installed locally)."

    TaskConfig.model_validate(raw)
    return True, "Validated against installed harbor.models.task.config.TaskConfig."


def _render_task_toml(artifact: SynthesizedArtifact, profile: RepoProfile) -> str:
    p = artifact.proposal
    escaped_desc = p.title.replace('"', '\\"')
    escaped_repo = profile.remote_url.replace('"', '\\"')
    escaped_model = artifact.model_used.replace('"', '\\"')
    return f'''schema_version = "1.4"

[task]
name = "harborforge/{p.slug}"
description = "{escaped_desc}"
version = "0.1.0"

[metadata]
author_name = "HarborForge Pipeline"
repository = "{escaped_repo}"
base_commit = "{profile.base_commit}"
branch_or_tag = "{profile.branch_or_tag}"
license = "{profile.license_name}"
subsystem = "{p.target_subsystem}"
behavioral_axis = "{p.behavioral_axis}"
feature_category = "feature-development"
generator_model = "{escaped_model}"
tags = ["python", "feature-development", "{p.behavioral_axis}"]

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = 600.0

[environment]
build_timeout_sec = 600.0
cpus = 2
memory_mb = 4096
storage_mb = 10240
'''


def _render_dockerfile(profile: RepoProfile, *, use_bundle: bool) -> str:
    if use_bundle:
        clone_block = (
            "COPY repo.bundle /tmp/repo.bundle\n"
            f"RUN git clone /tmp/repo.bundle /app && cd /app && git checkout {profile.base_commit} && rm -f /tmp/repo.bundle"
        )
    else:
        clone_block = (
            f"RUN git clone {profile.remote_url} /app \\\n"
            f"    && cd /app && git checkout {profile.base_commit}"
        )

    return f'''FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git \\
    patch \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
{clone_block}
RUN pip install --no-cache-dir --upgrade pip \\
    && pip install --no-cache-dir -e . "pytest<9"
'''


def _render_solve_sh(patch_text: str) -> str:
    delimiter = "HARBORFORGE_PATCH_EOF_9F3A"
    while delimiter in patch_text:
        delimiter += "_X"
    return f'''#!/usr/bin/env bash
set -euo pipefail

cd "${{HARBORFORGE_WORKDIR:-/app}}"
git apply --whitespace=nowarn - <<'{delimiter}'
{patch_text.rstrip()}
{delimiter}
'''


def _render_test_sh(test_file_rel_path: str) -> str:
    # CRITICAL: Do NOT use `set -e` around pytest! During `nop` evaluation, pytest
    # returns non-zero because feature tests fail; aborting early would omit
    # `/logs/verifier/reward.txt` and cause Harbor to record a `null` crash reward.
    test_filename = Path(test_file_rel_path).name
    return f'''#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
APP_DIR="${{HARBORFORGE_WORKDIR:-/app}}"
LOG_DIR="${{HARBORFORGE_VERIFIER_LOG_DIR:-/logs/verifier}}"
PY_BIN="${{HARBORFORGE_PYTHON:-python3}}"
RESULTS_JSON="$LOG_DIR/pytest_results.json"

mkdir -p "$LOG_DIR"
mkdir -p "$APP_DIR/tests"

# Inject the hidden feature test file strictly at verification time
cp "$SCRIPT_DIR/{test_filename}" "$APP_DIR/{test_file_rel_path}"

if [ -d "$APP_DIR/src" ]; then
    export PYTHONPATH="$APP_DIR/src:$APP_DIR:${{PYTHONPATH:-}}"
else
    export PYTHONPATH="$APP_DIR:${{PYTHONPATH:-}}"
fi

cd "$APP_DIR"
"$PY_BIN" "$SCRIPT_DIR/run_pytest_collector.py" \\
    --output "$RESULTS_JSON" \\
    --target "tests" || true

"$PY_BIN" "$SCRIPT_DIR/grade_harbor.py" \\
    --results "$RESULTS_JSON" \\
    --fail-to-pass "$SCRIPT_DIR/fail_to_pass.json" \\
    --pass-to-pass "$SCRIPT_DIR/pass_to_pass.json" \\
    --reward-txt "$LOG_DIR/reward.txt" \\
    --reward-json "$LOG_DIR/reward.json"

exit 0
'''


_PYTEST_COLLECTOR_SCRIPT = '''"""Execute pytest and emit exact nodeid outcomes to JSON without crashing test.sh."""

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
'''


_HARBOR_GRADER_SCRIPT = '''"""Compute exact Innodata/Harbor reward from pytest outcomes and write reward.txt."""

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
    reward_txt.write_text(f"{reward}\\n", encoding="utf-8")
    reward_json.write_text(json.dumps(details, indent=2) + "\\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _make_executable(path: Path) -> None:
    mode = os.stat(path).st_mode
    os.chmod(path, mode | 0o111)
