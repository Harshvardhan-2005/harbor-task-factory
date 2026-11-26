"""Artifact synthesizer producing instruction.md, unified git patch, and feature tests."""

from __future__ import annotations

import ast
import difflib
import subprocess
import tempfile
from pathlib import Path

from .llm_client import LLMClient, extract_json_object
from .models import FeatureProposal, RepoProfile, SynthesizedArtifact
from .spec_auditor import audit_artifact

SYNTH_SYSTEM_PROMPT = """You are a principal software engineer creating a high-signal benchmark task package.

Given a repository snapshot and a feature proposal, generate:
1. `instruction_md`: A crystal-clear natural-language feature specification written as a product/engineering ticket.
   - NEVER mention test file names, test function names, `fail_to_pass`, `pass_to_pass`, `solve.sh`, or grading paths.
   - Include exact public module/class/function names, signatures, edge cases, and expected behavior.
2. `modified_files`: A JSON object mapping relative source paths (e.g. "src/click/types.py") to the COMPLETE updated file contents implementing the feature cleanly.
   - Must preserve existing public APIs and keep all existing tests passing.
   - Must NOT modify any test files.
3. `test_file_content`: Complete standalone pytest file content testing ONLY the new feature thoroughly (including edge cases, error paths, and boundary checks).
4. `fail_to_pass_functions`: List of pytest function names defined in `test_file_content` (e.g. ["test_valid_case", "test_error_bounds"]).

Return ONLY valid JSON with keys: `instruction_md`, `modified_files`, `test_file_content`, `fail_to_pass_functions`.
"""


def synthesize_task_artifact(
    proposal: FeatureProposal,
    profile: RepoProfile,
    *,
    llm_client: LLMClient | None = None,
    max_attempts: int = 3,
) -> SynthesizedArtifact:
    """Synthesize and audit a task artifact, auto-retrying with audit feedback on failure."""
    repo_root = Path(profile.repo_path).resolve()

    if llm_client is None:
        artifact = _build_offline_artifact(proposal, profile)
        audit = audit_artifact(artifact, repo_path=repo_root, base_commit=profile.base_commit)
        if not audit.passed:
            raise RuntimeError(f"Offline artifact audit failed for {proposal.task_id}: {audit.errors}")
        return artifact

    feedback = ""
    last_errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        prompt = _build_synth_prompt(proposal, profile, repo_root, feedback=feedback)
        raw = llm_client.complete(SYNTH_SYSTEM_PROMPT, prompt, temperature=0.15)
        parsed = extract_json_object(raw)

        instruction_md = str(parsed.get("instruction_md", "")).strip()
        modified_files = parsed.get("modified_files") or {}
        test_file_content = str(parsed.get("test_file_content", "")).strip() + "\n"
        f2p_funcs = [str(name).strip() for name in (parsed.get("fail_to_pass_functions") or []) if str(name).strip()]

        if not isinstance(modified_files, dict) or not modified_files:
            last_errors = [f"Attempt {attempt}: LLM returned empty modified_files."]
            feedback = "\n".join(last_errors)
            continue

        patch_diff = build_unified_git_diff(repo_root, profile.base_commit, modified_files)
        test_rel_path = f"tests/test_hf_{proposal.slug.replace('-', '_')}.py"
        if not f2p_funcs:
            f2p_funcs = _extract_test_functions(test_file_content)
        fail_to_pass = [f"{test_rel_path}::{fn}" for fn in f2p_funcs]

        artifact = SynthesizedArtifact(
            proposal=proposal,
            instruction_md=instruction_md,
            patch_diff=patch_diff,
            test_file_rel_path=test_rel_path,
            test_file_content=test_file_content,
            fail_to_pass=fail_to_pass,
            pass_to_pass=list(profile.existing_test_nodeids),
            model_used=llm_client.model_label,
        )
        audit = audit_artifact(artifact, repo_path=repo_root, base_commit=profile.base_commit)
        if audit.passed:
            return artifact

        last_errors = audit.errors
        feedback = (
            f"Previous attempt #{attempt} failed automated validation with these errors:\n"
            + "\n".join(f"- {err}" for err in audit.errors)
            + "\nFix ALL of these issues strictly."
        )

    raise RuntimeError(
        f"Failed to synthesize valid artifact for '{proposal.task_id}' after {max_attempts} attempts: {last_errors}"
    )


def build_unified_git_diff(
    repo_root: Path,
    base_commit: str,
    modified_files: dict[str, str],
) -> str:
    """Compute an exact unified `git diff` against `base_commit` inside a temporary worktree."""
    with tempfile.TemporaryDirectory(prefix="hf_diff_") as tmp_dir:
        wt_path = Path(tmp_dir) / "wt"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt_path), base_commit],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        try:
            for rel_path, new_content in modified_files.items():
                dest = wt_path / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                content_str = str(new_content)
                if content_str and not content_str.endswith("\n"):
                    content_str += "\n"
                dest.write_text(content_str, encoding="utf-8")
                subprocess.run(["git", "add", rel_path], cwd=wt_path, check=True, capture_output=True)

            diff_proc = subprocess.run(
                ["git", "diff", "--cached", "--no-color"],
                cwd=wt_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return diff_proc.stdout
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )


def _build_synth_prompt(
    proposal: FeatureProposal,
    profile: RepoProfile,
    repo_root: Path,
    *,
    feedback: str,
) -> str:
    target_path = repo_root / proposal.target_file
    source_text = target_path.read_text(encoding="utf-8") if target_path.is_file() else ""
    if len(source_text) > 16000:
        source_text = source_text[:16000] + "\n# ... truncated ..."

    parts = [
        f"Repository: {profile.repo_name} (commit {profile.base_commit})",
        f"Feature Title: {proposal.title}",
        f"Target Subsystem: {proposal.target_subsystem} ({proposal.target_file})",
        f"Behavioral Axis: {proposal.behavioral_axis}",
        f"Summary: {proposal.summary}",
        "Acceptance Criteria:\n" + "\n".join(f"- {c}" for c in proposal.acceptance_criteria),
        f"\nCurrent `{proposal.target_file}` contents:\n```python\n{source_text}\n```",
    ]
    if feedback:
        parts.append(f"\nAUDIT FEEDBACK TO RESOLVE:\n{feedback}")
    return "\n\n".join(parts)


def _extract_test_functions(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            names.append(node.name)
    return names


def _build_offline_artifact(proposal: FeatureProposal, profile: RepoProfile) -> SynthesizedArtifact:
    """Deterministic feature artifact builder for offline pipeline verification & testing."""
    repo_root = Path(profile.repo_path).resolve()
    target_rel = proposal.target_file
    orig_text = (repo_root / target_rel).read_text(encoding="utf-8")
    mod_import = proposal.target_subsystem

    slug_ident = proposal.slug.replace("-", "_")
    test_rel_path = f"tests/test_hf_{slug_ident}.py"

    addition, fn_names, instruction_md, test_code = _offline_feature_template(
        proposal=proposal,
        mod_import=mod_import,
    )

    updated_text = orig_text.rstrip() + "\n\n\n" + addition.strip() + "\n"
    diff_lines = list(
        difflib.unified_diff(
            orig_text.splitlines(keepends=True),
            updated_text.splitlines(keepends=True),
            fromfile=f"a/{target_rel}",
            tofile=f"b/{target_rel}",
        )
    )
    patch_diff = f"diff --git a/{target_rel} b/{target_rel}\n" + "".join(diff_lines)

    return SynthesizedArtifact(
        proposal=proposal,
        instruction_md=instruction_md,
        patch_diff=patch_diff,
        test_file_rel_path=test_rel_path,
        test_file_content=test_code,
        fail_to_pass=[f"{test_rel_path}::{name}" for name in fn_names],
        pass_to_pass=list(profile.existing_test_nodeids),
        model_used="offline-deterministic-template",
    )


def _offline_feature_template(
    *,
    proposal: FeatureProposal,
    mod_import: str,
) -> tuple[str, list[str], str, str]:
    axis = proposal.behavioral_axis
    if axis == "input_validation":
        addition = '''
def parse_duration_seconds(value: str, *, min_seconds: int | None = None, max_seconds: int | None = None) -> int:
    """Parse composite human-readable durations such as '1h30m' or '45s' into integer seconds."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Duration string must be a non-empty string.")
    import re

    token_re = re.compile(r"(\\d+)(ms|s|m|h|d)")
    cleaned = value.strip().lower()
    pos = 0
    total_ms = 0
    multipliers = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
    for match in token_re.finditer(cleaned):
        if match.start() != pos:
            raise ValueError(f"Invalid duration segment near '{cleaned[pos:]}'")
        amount = int(match.group(1))
        unit = match.group(2)
        total_ms += amount * multipliers[unit]
        pos = match.end()
    if pos != len(cleaned) or pos == 0:
        raise ValueError(f"Invalid duration literal: {value!r}")
    seconds = total_ms // 1000
    if min_seconds is not None and seconds < min_seconds:
        raise ValueError(f"Duration {seconds}s is below minimum {min_seconds}s")
    if max_seconds is not None and seconds > max_seconds:
        raise ValueError(f"Duration {seconds}s exceeds maximum {max_seconds}s")
    return seconds
'''
        test_code = f'''import pytest
from {mod_import} import parse_duration_seconds


def test_parse_composite_duration():
    assert parse_duration_seconds("1h30m15s") == 5415
    assert parse_duration_seconds("5000ms") == 5
    assert parse_duration_seconds("2d") == 172800


def test_duration_bounds_validation():
    assert parse_duration_seconds("60s", min_seconds=60, max_seconds=60) == 60
    with pytest.raises(ValueError, match="below minimum"):
        parse_duration_seconds("59s", min_seconds=60)
    with pytest.raises(ValueError, match="exceeds maximum"):
        parse_duration_seconds("61s", max_seconds=60)


def test_duration_rejects_malformed_inputs():
    for bad in ("", "   ", "-5s", "10x", "1h+30m"):
        with pytest.raises(ValueError):
            parse_duration_seconds(bad)
'''
        instr = (
            f"# Feature Specification: {proposal.title}\n\n"
            f"Implement `parse_duration_seconds(value: str, *, min_seconds: int | None = None, max_seconds: int | None = None) -> int` "
            f"in module `{mod_import}` (`{proposal.target_file}`).\n\n"
            "## Requirements\n"
            "- Parse composite positive duration strings combining `ms`, `s`, `m`, `h`, and `d` tokens (case-insensitive) into total integer seconds.\n"
            "- Raise `ValueError` on empty strings, negative values, unknown units, or unparsed characters.\n"
            "- Enforce optional inclusive `min_seconds` and `max_seconds` bounds with descriptive error messages.\n"
            "- Preserve all existing module functionality without regressions.\n"
        )
        return addition, ["test_parse_composite_duration", "test_duration_bounds_validation", "test_duration_rejects_malformed_inputs"], instr, test_code

    if axis == "command_execution":
        addition = '''
def validate_option_constraints(
    supplied_options: set[str],
    *,
    mutually_exclusive: list[set[str]] | None = None,
    required_together: list[set[str]] | None = None,
) -> None:
    """Validate mutually exclusive and required-together option constraint sets."""
    for group in mutually_exclusive or []:
        active = sorted(supplied_options & set(group))
        if len(active) > 1:
            raise ValueError(f"Options {', '.join(active)} are mutually exclusive.")
    for group in required_together or []:
        group_set = set(group)
        present = supplied_options & group_set
        if present and present != group_set:
            missing = sorted(group_set - present)
            raise ValueError(f"Options {', '.join(sorted(present))} require missing option(s): {', '.join(missing)}.")
'''
        test_code = f'''import pytest
from {mod_import} import validate_option_constraints


def test_mutually_exclusive_allows_single_option():
    validate_option_constraints({{"json"}}, mutually_exclusive=[{{"json", "yaml", "csv"}}])
    validate_option_constraints(set(), mutually_exclusive=[{{"json", "yaml"}}])


def test_mutually_exclusive_rejects_conflict():
    with pytest.raises(ValueError, match="mutually exclusive"):
        validate_option_constraints({{"json", "yaml"}}, mutually_exclusive=[{{"json", "yaml"}}])


def test_required_together_validation():
    validate_option_constraints({{"host", "port"}}, required_together=[{{"host", "port"}}])
    validate_option_constraints(set(), required_together=[{{"host", "port"}}])
    with pytest.raises(ValueError, match="require missing"):
        validate_option_constraints({{"host"}}, required_together=[{{"host", "port"}}])
'''
        instr = (
            f"# Feature Specification: {proposal.title}\n\n"
            f"Implement `validate_option_constraints(supplied_options: set[str], *, mutually_exclusive: list[set[str]] | None = None, required_together: list[set[str]] | None = None) -> None` "
            f"in `{mod_import}` (`{proposal.target_file}`).\n\n"
            "## Requirements\n"
            "- Raise `ValueError` when more than one option from any `mutually_exclusive` group is present in `supplied_options`.\n"
            "- Raise `ValueError` when a non-empty strict subset of any `required_together` group is supplied.\n"
            "- Allow empty selections or complete `required_together` groups without error.\n"
        )
        return addition, ["test_mutually_exclusive_allows_single_option", "test_mutually_exclusive_rejects_conflict", "test_required_together_validation"], instr, test_code

    if axis == "output_formatting":
        addition = '''
def export_command_schema_json(
    command_name: str,
    options: list[dict[str, object]],
    *,
    include_hidden: bool = False,
    indent: int = 2,
) -> str:
    """Serialize CLI command metadata and visible options into deterministic JSON."""
    import json

    filtered = [
        {
            "name": str(opt["name"]),
            "type": str(opt.get("type", "str")),
            "required": bool(opt.get("required", False)),
            "default": opt.get("default"),
        }
        for opt in options
        if include_hidden or not bool(opt.get("hidden", False))
    ]
    filtered.sort(key=lambda item: item["name"])
    payload = {"command": command_name, "options": filtered}
    return json.dumps(payload, indent=indent, sort_keys=True)
'''
        test_code = f'''import json
from {mod_import} import export_command_schema_json


def test_export_schema_excludes_hidden_by_default():
    opts = [
        {{"name": "--verbose", "type": "bool", "default": False}},
        {{"name": "--debug-token", "type": "str", "hidden": True}},
    ]
    raw = export_command_schema_json("deploy", opts)
    assert '\\n  "command"' in raw
    data = json.loads(raw)
    assert data["command"] == "deploy"
    assert [o["name"] for o in data["options"]] == ["--verbose"]
    assert data["options"][0]["required"] is False


def test_export_schema_includes_hidden_when_requested_and_sorts():
    opts = [
        {{"name": "--zeta", "required": True}},
        {{"name": "--alpha", "hidden": True, "default": "x"}},
    ]
    data = json.loads(export_command_schema_json("run", opts, include_hidden=True))
    assert [o["name"] for o in data["options"]] == ["--alpha", "--zeta"]
    assert data["options"][0]["default"] == "x"
    assert data["options"][1]["required"] is True
'''
        instr = (
            f"# Feature Specification: {proposal.title}\n\n"
            f"Implement `export_command_schema_json(command_name: str, options: list[dict[str, object]], *, include_hidden: bool = False, indent: int = 2) -> str` "
            f"in `{mod_import}` (`{proposal.target_file}`).\n\n"
            "## Requirements\n"
            "- Exclude option entries where `hidden=True` unless `include_hidden=True`.\n"
            "- Sort exported option entries deterministically by `name`.\n"
            "- Return valid JSON containing `command` and normalized `options` (`name`, `type`, `required`, `default`).\n"
        )
        return addition, ["test_export_schema_excludes_hidden_by_default", "test_export_schema_includes_hidden_when_requested_and_sorts"], instr, test_code

    if axis == "lifecycle_decorators":
        addition = '''
class AliasRegistry:
    """Registry mapping command aliases to primary command names with optional deprecation flags."""

    def __init__(self) -> None:
        self._aliases: dict[str, tuple[str, bool]] = {}

    def register(self, alias: str, target: str, *, deprecated: bool = False) -> None:
        if not alias or not target:
            raise ValueError("Alias and target must be non-empty.")
        if alias in self._aliases and self._aliases[alias][0] != target:
            raise ValueError(f"Alias '{alias}' is already bound to '{self._aliases[alias][0]}'.")
        self._aliases[alias] = (target, deprecated)

    def resolve(self, name: str) -> tuple[str, str | None]:
        if name not in self._aliases:
            return name, None
        target, deprecated = self._aliases[name]
        warning = f"Warning: alias '{name}' is deprecated; use '{target}' instead." if deprecated else None
        return target, warning
'''
        test_code = f'''import pytest
from {mod_import} import AliasRegistry


def test_alias_resolution_and_warning():
    reg = AliasRegistry()
    reg.register("ls", "list")
    reg.register("old-ls", "list", deprecated=True)
    assert reg.resolve("ls") == ("list", None)
    target, warning = reg.resolve("old-ls")
    assert target == "list"
    assert warning is not None and "deprecated" in warning


def test_alias_rejects_conflicting_rebind():
    reg = AliasRegistry()
    reg.register("rm", "remove")
    reg.register("rm", "remove")
    with pytest.raises(ValueError, match="already bound"):
        reg.register("rm", "rename")
    with pytest.raises(ValueError, match="non-empty"):
        reg.register("", "remove")
    assert reg.resolve("unknown") == ("unknown", None)
'''
        instr = (
            f"# Feature Specification: {proposal.title}\n\n"
            f"Implement `AliasRegistry` in `{mod_import}` (`{proposal.target_file}`).\n\n"
            "## Requirements\n"
            "- Provide `register(alias: str, target: str, *, deprecated: bool = False)` and `resolve(name: str) -> tuple[str, str | None]`.\n"
            "- Raise `ValueError` if an alias is empty, target is empty, or an alias is rebound to a different target.\n"
            "- Return `(target, warning)` with a deprecation message when `deprecated=True`, or `(name, None)` for unknown names.\n"
        )
        return addition, ["test_alias_resolution_and_warning", "test_alias_rejects_conflicting_rebind"], instr, test_code

    # Default / runtime_utilities
    addition = '''
from contextlib import contextmanager as _hf_contextmanager
import os as _hf_os


@_hf_contextmanager
def atomic_write_file(path: str | _hf_os.PathLike[str], *, encoding: str = "utf-8"):
    """Open a temporary staging file and atomically commit it to `path` on clean exit."""
    from pathlib import Path as _hf_Path

    target = _hf_Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.tmp.{_hf_os.getpid()}")
    handle = staging.open("w", encoding=encoding)
    try:
        yield handle
        handle.flush()
        _hf_os.fsync(handle.fileno())
        handle.close()
        _hf_os.replace(staging, target)
    except Exception:
        if not handle.closed:
            handle.close()
        if staging.exists():
            staging.unlink()
        raise
'''
    test_code = f'''import pytest
from {mod_import} import atomic_write_file


def test_atomic_write_commits_on_success(tmp_path):
    dest = tmp_path / "a" / "b" / "out.txt"
    with atomic_write_file(dest) as f:
        f.write("committed-data")
    with atomic_write_file(dest) as f:
        f.write("updated-data")
    assert dest.read_text(encoding="utf-8") == "updated-data"
    assert not any(p.name.endswith(".tmp") or ".tmp." in p.name for p in dest.parent.iterdir())


def test_atomic_write_rolls_back_on_error(tmp_path):
    dest = tmp_path / "config.txt"
    dest.write_text("original", encoding="utf-8")
    with pytest.raises(RuntimeError):
        with atomic_write_file(dest) as f:
            f.write("partial-corrupt")
            raise RuntimeError("boom")
    assert dest.read_text(encoding="utf-8") == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["config.txt"]
'''
    instr = (
        f"# Feature Specification: {proposal.title}\n\n"
        f"Implement `atomic_write_file(path, *, encoding: str = \"utf-8\")` context manager in `{mod_import}` (`{proposal.target_file}`).\n\n"
        "## Requirements\n"
        "- Stage writes to a temporary file in the same directory and atomically replace the target on clean exit.\n"
        "- On any exception inside the context block, preserve any pre-existing target file untouched and remove the temporary file.\n"
        "- Leave zero temporary files behind after either success or failure.\n"
    )
    return addition, ["test_atomic_write_commits_on_success", "test_atomic_write_rolls_back_on_error"], instr, test_code
