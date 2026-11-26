"""Shared runtime utilities for pytest interpreter discovery and git worktrees."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def resolve_python_with_pytest(explicit_bin: str | None = None) -> str:
    """Return a Python executable capable of importing pytest."""
    candidates: list[str] = []
    if explicit_bin:
        candidates.append(explicit_bin)
    env_py = os.environ.get("HARBORFORGE_PYTHON")
    if env_py:
        candidates.append(env_py)
    candidates.append(sys.executable)

    pytest_exe = shutil.which("pytest")
    if pytest_exe:
        sibling_py = Path(pytest_exe).parent / "python3"
        if sibling_py.exists():
            candidates.append(str(sibling_py))
        shebang_py = _read_shebang_python(Path(pytest_exe))
        if shebang_py:
            candidates.append(shebang_py)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _can_import_pytest(candidate):
            return candidate

    return explicit_bin or env_py or sys.executable


@contextmanager
def temp_git_worktree(repo_root: Path, base_commit: str, *, prefix: str = "hf_wt_") -> Iterator[Path]:
    """Check out `base_commit` into an isolated temporary detached git worktree."""
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp_dir:
        wt_path = Path(tmp_dir) / "wt"
        proc = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt_path), base_commit],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git worktree add failed for {base_commit}: {proc.stderr.strip()}")
        try:
            yield wt_path
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )


def _can_import_pytest(python_exe: str) -> bool:
    proc = subprocess.run(
        [python_exe, "-c", "import pytest"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _read_shebang_python(script_path: Path) -> str | None:
    try:
        first_line = script_path.read_text(encoding="utf-8", errors="ignore").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    if first_line.startswith("#!"):
        tokens = first_line[2:].strip().split()
        if tokens:
            if Path(tokens[0]).name == "env" and len(tokens) > 1:
                return shutil.which(tokens[1]) or tokens[1]
            return tokens[0]
    return None
