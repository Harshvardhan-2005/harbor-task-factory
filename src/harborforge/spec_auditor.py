"""Anti-leakage, security-scope, bug-fix guard, and patch integrity auditor."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from .models import AuditResult, SynthesizedArtifact

BUG_FIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfix(?:es|ed|ing)?\s+(?:a\s+)?(?:the\s+)?(?:bug|crash|regression|defect|issue)\b", re.I),
    re.compile(r"\bhotfix\b", re.I),
    re.compile(r"\bcurrently\s+(?:crashes|broken|fails\s+with)\b", re.I),
)

SECURITY_AND_NETWORK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:requests|httpx|aiohttp)\.(?:get|post|put|delete)\b"), "external HTTP client call"),
    (re.compile(r"\burllib\.request\.urlopen\b"), "external URL open call"),
    (re.compile(r"\bsocket\.create_connection\b"), "outbound socket call"),
    (re.compile(r"\b(?:os\.system|subprocess\.(?:run|Popen))\([^)]*(?:curl|wget|nc|bash -c)"), "suspicious shell call"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "hardcoded AWS credential"),
)


def audit_artifact(
    artifact: SynthesizedArtifact,
    *,
    repo_path: str | Path,
    base_commit: str,
) -> AuditResult:
    """Validate that an artifact satisfies feature-only, zero-leakage, security, and git-apply rules."""
    errors: list[str] = []
    warnings: list[str] = []

    instr = artifact.instruction_md.strip()
    if len(instr) < 80:
        errors.append("instruction.md is too short (< 80 chars) to specify a realistic feature.")

    # 1. Bug-fix scope exclusion
    for pattern in BUG_FIX_PATTERNS:
        if pattern.search(instr) or pattern.search(artifact.proposal.summary):
            errors.append(f"Feature scope violation: instruction/summary matches bug-fix pattern '{pattern.pattern}'.")

    # 2. Anti-leakage checks on instruction.md
    test_basename = Path(artifact.test_file_rel_path).name
    leakage_tokens = [
        test_basename,
        artifact.test_file_rel_path,
        "fail_to_pass",
        "pass_to_pass",
        "solve.sh",
        "solution.patch",
        "/logs/verifier",
        "reward.txt",
    ]
    for nodeid in artifact.fail_to_pass:
        fn_name = nodeid.split("::")[-1].split("[")[0]
        if fn_name.startswith("test_") and len(fn_name) > 6:
            leakage_tokens.append(fn_name)

    lower_instr = instr.lower()
    for token in leakage_tokens:
        if token.lower() in lower_instr:
            errors.append(f"Spec leakage detected in instruction.md: references internal identifier '{token}'.")

    # 3. Security & offline network constraints across patch and tests
    combined_code = f"{artifact.patch_diff}\n{artifact.test_file_content}"
    for pattern, label in SECURITY_AND_NETWORK_PATTERNS:
        if pattern.search(combined_code):
            errors.append(f"Security/offline policy violation: detected {label}.")

    # 4. Patch must modify source code, not pre-existing tests, and must be non-empty
    touched_files = _extract_patch_paths(artifact.patch_diff)
    if not touched_files:
        errors.append("patch_diff modifies zero files or is not a valid unified git diff.")
    for path in touched_files:
        if path.startswith("tests/") or Path(path).name.startswith("test_"):
            errors.append(f"Reference patch must not alter tests ('{path}'); tests belong in tests/.")

    if not artifact.fail_to_pass:
        errors.append("fail_to_pass list is empty; every feature task must define failing->passing tests.")

    # 5. Verify patch applies cleanly at exact base_commit using an isolated git worktree
    if not errors:
        apply_ok, apply_msg = _check_git_apply_worktree(
            Path(repo_path).resolve(),
            base_commit=base_commit,
            patch_diff=artifact.patch_diff,
        )
        if not apply_ok:
            errors.append(f"Reference patch failed `git apply --check` at {base_commit[:10]}: {apply_msg}")

    return AuditResult(passed=len(errors) == 0, errors=errors, warnings=warnings)


def _extract_patch_paths(patch_diff: str) -> list[str]:
    paths: list[str] = []
    for line in patch_diff.splitlines():
        if line.startswith("+++ b/"):
            paths.append(line[6:].strip())
        elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            raw = line[4:].strip()
            if raw.startswith("b/"):
                raw = raw[2:]
            paths.append(raw)
    return paths


def _check_git_apply_worktree(repo_root: Path, *, base_commit: str, patch_diff: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="hf_audit_") as tmp_dir:
        wt_path = Path(tmp_dir) / "wt"
        add_proc = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt_path), base_commit],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if add_proc.returncode != 0:
            return False, add_proc.stderr.strip() or add_proc.stdout.strip()

        try:
            check_proc = subprocess.run(
                ["git", "apply", "--check", "--whitespace=nowarn", "-"],
                cwd=wt_path,
                input=patch_diff,
                capture_output=True,
                text=True,
                check=False,
            )
            if check_proc.returncode != 0:
                return False, check_proc.stderr.strip() or check_proc.stdout.strip()
            return True, "ok"
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
