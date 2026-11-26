"""AST-based repository analyzer and baseline pytest test collector."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from .models import RepoProfile, SubsystemProfile, SymbolSummary
from .runtime import resolve_python_with_pytest

AXIS_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("type", "param", "schema", "validate"), "input_validation"),
    (("format", "help", "render", "doc"), "output_formatting"),
    (("decorator", "hook", "plugin", "callback"), "lifecycle_decorators"),
    (("core", "command", "group", "context", "option"), "command_execution"),
    (("term", "util", "stream", "file", "completion", "io"), "runtime_utilities"),
)


def analyze_repository(
    repo_path: str | Path,
    *,
    collect_tests: bool = True,
    python_bin: str | None = None,
) -> RepoProfile:
    """Inspect a local git clone and return an AST + git + pytest baseline profile."""
    root = Path(repo_path).resolve()
    if not (root / ".git").exists():
        raise ValueError(f"Path is not a git repository: {root}")

    base_commit = _run_git(root, ["rev-parse", "HEAD"]).strip()
    if len(base_commit) != 40:
        raise RuntimeError(f"Expected 40-char commit hash, got: {base_commit!r}")

    status_out = _run_git(root, ["status", "--porcelain"]).strip()
    is_clean = len(status_out) == 0

    remote_url = _try_git(root, ["config", "--get", "remote.origin.url"]).strip() or f"file://{root}"
    branch_or_tag = (
        _try_git(root, ["describe", "--tags", "--exact-match", "HEAD"]).strip()
        or _try_git(root, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        or "HEAD"
    )
    recent_raw = _try_git(root, ["log", "-n", "25", "--pretty=format:%h %s"])
    recent_commits = [line.strip() for line in recent_raw.splitlines() if line.strip()]

    license_name = _detect_license(root)
    subsystems = _scan_python_subsystems(root)
    existing_test_nodeids = _collect_pytest_nodeids(root, python_bin=python_bin) if collect_tests else []

    return RepoProfile(
        repo_path=str(root),
        repo_name=root.name,
        remote_url=remote_url,
        base_commit=base_commit,
        branch_or_tag=branch_or_tag,
        license_name=license_name,
        is_clean=is_clean,
        subsystems=subsystems,
        recent_commits=recent_commits,
        existing_test_nodeids=existing_test_nodeids,
    )


def _scan_python_subsystems(root: Path) -> list[SubsystemProfile]:
    src_root = root / "src" if (root / "src").is_dir() else root
    py_files = sorted(
        path
        for path in src_root.rglob("*.py")
        if not any(part in {"tests", "test", ".venv", "__pycache__", "build", "dist"} for part in path.parts)
    )

    profiles: list[SubsystemProfile] = []
    for path in py_files:
        if path.name == "__init__.py" and path.stat().st_size < 150:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
        except (OSError, SyntaxError):
            continue

        symbols: list[SymbolSummary] = []
        imports: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                methods = [
                    child.name
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and (not child.name.startswith("_") or child.name == "__init__")
                ]
                bases = [ast.unparse(b) for b in node.bases]
                sig = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
                symbols.append(
                    SymbolSummary(
                        kind="class",
                        name=node.name,
                        signature=sig,
                        lineno=node.lineno,
                        docstring=_short_doc(ast.get_docstring(node)),
                        methods=methods[:15],
                    )
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                symbols.append(
                    SymbolSummary(
                        kind="function",
                        name=node.name,
                        signature=_format_func_sig(node),
                        lineno=node.lineno,
                        docstring=_short_doc(ast.get_docstring(node)),
                    )
                )

        if not symbols:
            continue

        module_name = rel.removesuffix(".py").replace("/", ".")
        if module_name.startswith("src."):
            module_name = module_name[4:]

        profiles.append(
            SubsystemProfile(
                module_name=module_name,
                rel_path=rel,
                line_count=len(source.splitlines()),
                imports=sorted(set(imports))[:20],
                symbols=symbols[:30],
                candidate_axis=_infer_axis(rel),
            )
        )

    return profiles


def _collect_pytest_nodeids(root: Path, *, python_bin: str | None = None) -> list[str]:
    env = {**os.environ, "PYTHONPATH": str(root / "src") if (root / "src").is_dir() else str(root)}
    py = resolve_python_with_pytest(python_bin)
    proc = subprocess.run([py, "-m", "pytest", "--collect-only", "-q"], cwd=root, env=env, capture_output=True, text=True, check=False)
    if proc.returncode in (0, 5):
        nodeids = [
            line.strip()
            for line in proc.stdout.splitlines()
            if "::" in line.strip() and not line.strip().startswith("<")
        ]
        if nodeids:
            return nodeids
    return _discover_test_files_fallback(root)


def _discover_test_files_fallback(root: Path) -> list[str]:
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in tests_dir.rglob("test_*.py")
    )


def _detect_license(root: Path) -> str:
    for candidate in ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENSE.rst"):
        lic = root / candidate
        if lic.is_file():
            text = lic.read_text(encoding="utf-8", errors="ignore")[:400]
            if "BSD 3-Clause" in text or "Redistribution and use in source and binary forms" in text:
                return "BSD-3-Clause"
            if "MIT License" in text:
                return "MIT"
            if "Apache License" in text:
                return "Apache-2.0"
            return candidate
    return "BSD-3-Clause"


def _infer_axis(rel_path: str) -> str:
    lower = rel_path.lower()
    for keywords, axis in AXIS_HINTS:
        if any(kw in lower for kw in keywords):
            return axis
    return "runtime_utilities"


def _format_func_sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [arg.arg for arg in node.args.posonlyargs + node.args.args]
    kwonly = [arg.arg for arg in node.args.kwonlyargs]
    parts = [*args]
    if kwonly:
        parts.append("*")
        parts.extend(kwonly)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(parts)})"


def _short_doc(doc: str | None) -> str:
    if not doc:
        return ""
    first_line = doc.strip().splitlines()[0].strip()
    return first_line[:160]


def _run_git(root: Path, args: list[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return proc.stdout


def _try_git(root: Path, args: list[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else ""
