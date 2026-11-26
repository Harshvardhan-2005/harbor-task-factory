"""Automated Harbor (Docker) and local git-worktree verifier for nop=0.0 and oracle=1.0."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from .models import VerificationResult
from .runtime import resolve_python_with_pytest


def verify_task_package(
    task_dir: str | Path,
    *,
    mode: str = "auto",
    repo_path: str | Path | None = None,
    out_root: str | Path | None = None,
    python_bin: str | None = None,
) -> VerificationResult:
    """Validate a Harbor task directory against baseline (nop=0.0) and oracle (1.0) checks."""
    task_path = Path(task_dir).resolve()
    task_id = task_path.name

    selected_mode = mode
    if selected_mode == "auto":
        selected_mode = "harbor" if _harbor_and_docker_ready() else "local"

    if selected_mode == "harbor":
        return _verify_with_harbor_cli(task_path, task_id=task_id, out_root=out_root)
    if selected_mode == "local":
        if repo_path is None:
            raise ValueError("repo_path is required for local worktree verification mode.")
        return _verify_local_worktree(
            task_path,
            task_id=task_id,
            repo_root=Path(repo_path).resolve(),
            python_bin=resolve_python_with_pytest(python_bin),
        )
    raise ValueError(f"Unsupported verification mode '{mode}'. Choose auto, harbor, or local.")


def verify_and_repair_task(
    task_dir: str | Path,
    *,
    proposal: "FeatureProposal",
    profile: "RepoProfile",
    output_root: str | Path,
    mode: str = "auto",
    llm_client: "LLMClient | None" = None,
    max_repairs: int = 2,
    python_bin: str | None = None,
) -> tuple[Path, VerificationResult]:
    """Run verification and, when an LLM client is provided, auto-repair failing tasks."""
    from .packager import package_harbor_task
    from .synthesizer import synthesize_task_artifact

    current_dir = Path(task_dir).resolve()
    result = verify_task_package(
        current_dir,
        mode=mode,
        repo_path=profile.repo_path,
        python_bin=python_bin,
    )
    if result.passed or llm_client is None:
        return current_dir, result

    for attempt in range(1, max_repairs + 1):
        diag = (
            f"Verification failed (attempt {attempt}): "
            f"nop_reward={result.nop_reward}, oracle_reward={result.oracle_reward}, "
            f"error={result.error_message}, oracle_details={json.dumps(result.oracle_details)}"
        )
        repaired_proposal = proposal
        repaired_proposal.summary = f"{proposal.summary}\n[Repair Note: {diag[:400]}]"
        artifact = synthesize_task_artifact(repaired_proposal, profile, llm_client=llm_client)
        current_dir = package_harbor_task(artifact, profile, output_root=output_root)
        result = verify_task_package(
            current_dir,
            mode=mode,
            repo_path=profile.repo_path,
            python_bin=python_bin,
        )
        if result.passed:
            return current_dir, result

    return current_dir, result


def _harbor_and_docker_ready() -> bool:
    if shutil.which("harbor") is None or shutil.which("docker") is None:
        return False
    proc = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    return proc.returncode == 0


def _verify_with_harbor_cli(
    task_path: Path,
    *,
    task_id: str,
    out_root: str | Path | None,
) -> VerificationResult:
    base_out = Path(out_root).resolve() if out_root else task_path.parent / "_harbor_runs" / task_id
    nop_out = base_out / "nop"
    oracle_out = base_out / "oracle"
    nop_out.mkdir(parents=True, exist_ok=True)
    oracle_out.mkdir(parents=True, exist_ok=True)

    nop_cmd = ["harbor", "run", "-p", str(task_path), "-a", "nop", "-k", "1", "-o", str(nop_out), "--no-delete", "-y"]
    oracle_cmd = [
        "harbor",
        "run",
        "-p",
        str(task_path),
        "-a",
        "oracle",
        "-k",
        "1",
        "-o",
        str(oracle_out),
        "--no-delete",
        "-y",
    ]

    nop_proc = subprocess.run(nop_cmd, capture_output=True, text=True, check=False)
    oracle_proc = subprocess.run(oracle_cmd, capture_output=True, text=True, check=False)

    nop_reward, nop_details = _extract_harbor_reward(nop_out)
    oracle_reward, oracle_details = _extract_harbor_reward(oracle_out)

    passed = (
        nop_proc.returncode == 0
        and oracle_proc.returncode == 0
        and nop_reward == 0.0
        and oracle_reward == 1.0
    )
    err_parts = []
    if nop_proc.returncode != 0:
        err_parts.append(f"harbor nop exited {nop_proc.returncode}: {nop_proc.stderr[-300:]}")
    if oracle_proc.returncode != 0:
        err_parts.append(f"harbor oracle exited {oracle_proc.returncode}: {oracle_proc.stderr[-300:]}")
    if nop_reward != 0.0:
        err_parts.append(f"expected nop_reward=0.0, got {nop_reward}")
    if oracle_reward != 1.0:
        err_parts.append(f"expected oracle_reward=1.0, got {oracle_reward}")

    return VerificationResult(
        task_id=task_id,
        mode="harbor",
        nop_reward=nop_reward,
        oracle_reward=oracle_reward,
        passed=passed,
        nop_details=nop_details,
        oracle_details=oracle_details,
        error_message="; ".join(err_parts),
    )


def _verify_local_worktree(
    task_path: Path,
    *,
    task_id: str,
    repo_root: Path,
    python_bin: str,
) -> VerificationResult:
    meta = tomllib.loads((task_path / "task.toml").read_text(encoding="utf-8")).get("metadata", {})
    base_commit = str(meta.get("base_commit", "")).strip()
    if len(base_commit) != 40:
        raise ValueError(f"Invalid base_commit in {task_path / 'task.toml'}: {base_commit!r}")

    nop_reward, nop_details, nop_err = _run_worktree_pass(
        task_path=task_path,
        repo_root=repo_root,
        base_commit=base_commit,
        apply_solution=False,
        python_bin=python_bin,
    )
    oracle_reward, oracle_details, oracle_err = _run_worktree_pass(
        task_path=task_path,
        repo_root=repo_root,
        base_commit=base_commit,
        apply_solution=True,
        python_bin=python_bin,
    )

    passed = nop_reward == 0.0 and oracle_reward == 1.0
    errors = [e for e in (nop_err, oracle_err) if e]
    if nop_reward != 0.0:
        errors.append(f"expected nop_reward=0.0, got {nop_reward}")
    if oracle_reward != 1.0:
        errors.append(f"expected oracle_reward=1.0, got {oracle_reward}")

    return VerificationResult(
        task_id=task_id,
        mode="local",
        nop_reward=nop_reward,
        oracle_reward=oracle_reward,
        passed=passed,
        nop_details=nop_details,
        oracle_details=oracle_details,
        error_message="; ".join(errors),
    )


def _run_worktree_pass(
    *,
    task_path: Path,
    repo_root: Path,
    base_commit: str,
    apply_solution: bool,
    python_bin: str,
) -> tuple[float | None, dict[str, object], str]:
    with tempfile.TemporaryDirectory(prefix="hf_verify_") as tmp_dir:
        tmp = Path(tmp_dir)
        wt = tmp / "app"
        log_dir = tmp / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), base_commit],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if add.returncode != 0:
            return None, {}, f"git worktree add failed: {add.stderr.strip()}"

        try:
            env = {
                **os.environ,
                "HARBORFORGE_WORKDIR": str(wt),
                "HARBORFORGE_VERIFIER_LOG_DIR": str(log_dir),
                "HARBORFORGE_PYTHON": python_bin,
            }
            if apply_solution:
                sol = subprocess.run(
                    ["bash", str(task_path / "solution" / "solve.sh")],
                    cwd=wt,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if sol.returncode != 0:
                    return None, {}, f"solve.sh failed ({sol.returncode}): {sol.stderr.strip()}"

            test_proc = subprocess.run(
                ["bash", str(task_path / "tests" / "test.sh")],
                cwd=wt,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if test_proc.returncode != 0:
                return None, {}, f"test.sh failed ({test_proc.returncode}): {test_proc.stderr.strip()}"

            reward_txt = log_dir / "reward.txt"
            reward_json = log_dir / "reward.json"
            reward = float(reward_txt.read_text(encoding="utf-8").strip()) if reward_txt.is_file() else None
            details = json.loads(reward_json.read_text(encoding="utf-8")) if reward_json.is_file() else {}
            return reward, details, ""
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt)],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )


def _extract_harbor_reward(out_dir: Path) -> tuple[float | None, dict[str, object]]:
    reward_json_files = sorted(out_dir.rglob("reward.json"))
    if reward_json_files:
        data = json.loads(reward_json_files[0].read_text(encoding="utf-8"))
        val = data.get("reward")
        return (float(val) if val is not None else None), data

    reward_txt_files = sorted(out_dir.rglob("reward.txt"))
    if reward_txt_files:
        raw = reward_txt_files[0].read_text(encoding="utf-8").strip()
        try:
            return float(raw), {"reward": float(raw)}
        except ValueError:
            return None, {"raw_reward_txt": raw}

    result_files = sorted(out_dir.rglob("result.json"))
    if result_files:
        data = json.loads(result_files[0].read_text(encoding="utf-8"))
        return None, data
    return None, {}
