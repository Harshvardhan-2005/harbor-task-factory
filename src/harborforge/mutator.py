"""Surgical AST mutation testing harness to prove generated feature tests catch faults."""

from __future__ import annotations

import ast
import copy
import os
import re
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .models import MutantOutcome, MutationReport
from .runtime import resolve_python_with_pytest


@dataclass(slots=True)
class _CandidateMutant:
    operator: str
    lineno: int
    description: str
    mutated_source: str


def run_mutation_testing(
    task_dir: str | Path,
    *,
    repo_path: str | Path,
    max_mutants: int = 6,
    python_bin: str | None = None,
) -> MutationReport:
    """Mutate lines introduced by the task's patch and verify test.sh catches each fault."""
    task_path = Path(task_dir).resolve()
    repo_root = Path(repo_path).resolve()
    py = resolve_python_with_pytest(python_bin)

    meta = tomllib.loads((task_path / "task.toml").read_text(encoding="utf-8")).get("metadata", {})
    base_commit = str(meta.get("base_commit", "")).strip()
    patch_text = (task_path / "solution" / "solution.patch").read_text(encoding="utf-8")
    changed_ranges = _parse_added_line_ranges(patch_text)

    # Guard: ensure the unmutated oracle state strictly yields 1.0 before running mutants
    baseline_killed, baseline_reward, baseline_err = _execute_mutant(
        task_path=task_path,
        repo_root=repo_root,
        base_commit=base_commit,
        patch_text=patch_text,
        rel_path=None,
        mutated_source=None,
        python_bin=py,
    )
    if baseline_killed or baseline_reward != 1.0:
        raise RuntimeError(
            f"Cannot run mutation testing on '{task_path.name}': unmutated oracle baseline scored "
            f"{baseline_reward} ({baseline_err})."
        )

    with tempfile.TemporaryDirectory(prefix="hf_mutate_base_") as tmp_dir:
        wt = Path(tmp_dir) / "wt"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), base_commit],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        try:
            subprocess.run(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=wt,
                input=patch_text,
                text=True,
                capture_output=True,
                check=True,
            )

            candidates: list[tuple[str, _CandidateMutant]] = []
            for rel_path, target_lines in changed_ranges.items():
                file_path = wt / rel_path
                if not file_path.is_file() or file_path.suffix != ".py":
                    continue
                src = file_path.read_text(encoding="utf-8")
                for m in _generate_ast_mutants(src, target_lines=target_lines):
                    candidates.append((rel_path, m))
                    if len(candidates) >= max_mutants:
                        break
                if len(candidates) >= max_mutants:
                    break
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt)],
                cwd=repo_root,
                capture_output=True,
                check=False,
            )

    outcomes: list[MutantOutcome] = []
    for idx, (rel_path, mutant) in enumerate(candidates, start=1):
        killed, reward, err = _execute_mutant(
            task_path=task_path,
            repo_root=repo_root,
            base_commit=base_commit,
            patch_text=patch_text,
            rel_path=rel_path,
            mutated_source=mutant.mutated_source,
            python_bin=py,
        )
        outcomes.append(
            MutantOutcome(
                mutant_id=f"M{idx:02d}",
                operator=mutant.operator,
                file_path=rel_path,
                lineno=mutant.lineno,
                description=mutant.description,
                killed=killed,
                reward=reward,
                error=err,
            )
        )

    total = len(outcomes)
    killed_count = sum(1 for o in outcomes if o.killed)
    survived = total - killed_count
    kill_rate = round(killed_count / total, 4) if total > 0 else 0.0

    return MutationReport(
        task_id=task_path.name,
        total_mutants=total,
        killed_mutants=killed_count,
        survived_mutants=survived,
        kill_rate=kill_rate,
        outcomes=outcomes,
    )


def _execute_mutant(
    *,
    task_path: Path,
    repo_root: Path,
    base_commit: str,
    patch_text: str,
    rel_path: str | None,
    mutated_source: str | None,
    python_bin: str,
) -> tuple[bool, float | None, str]:
    with tempfile.TemporaryDirectory(prefix="hf_mut_run_") as tmp_dir:
        tmp = Path(tmp_dir)
        wt = tmp / "app"
        log_dir = tmp / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), base_commit],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        try:
            subprocess.run(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=wt,
                input=patch_text,
                text=True,
                capture_output=True,
                check=True,
            )
            if rel_path is not None and mutated_source is not None:
                (wt / rel_path).write_text(mutated_source, encoding="utf-8")

            env = {
                **os.environ,
                "HARBORFORGE_WORKDIR": str(wt),
                "HARBORFORGE_VERIFIER_LOG_DIR": str(log_dir),
                "HARBORFORGE_PYTHON": python_bin,
            }
            proc = subprocess.run(
                ["bash", str(task_path / "tests" / "test.sh")],
                cwd=wt,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return True, None, proc.stderr.strip()

            reward_txt = log_dir / "reward.txt"
            if not reward_txt.is_file():
                return True, None, "reward.txt missing"
            reward = float(reward_txt.read_text(encoding="utf-8").strip())
            return reward < 1.0, reward, ""
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt)],
                cwd=repo_root,
                capture_output=True,
                check=False,
            )


def _parse_added_line_ranges(patch_text: str) -> dict[str, set[int]]:
    """Map modified b/<file> paths to the set of new line numbers introduced in the patch."""
    ranges: dict[str, set[int]] = {}
    current_file: str | None = None
    cur_line = 0
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            ranges.setdefault(current_file, set())
        elif line.startswith("@@ ") and current_file is not None:
            match = hunk_re.match(line)
            if match:
                cur_line = int(match.group(1))
        elif current_file is not None and not line.startswith("--- "):
            if line.startswith("+") and not line.startswith("+++"):
                ranges[current_file].add(cur_line)
                cur_line += 1
            elif line.startswith(" "):
                cur_line += 1
    return ranges


def _generate_ast_mutants(source: str, *, target_lines: set[int]) -> list[_CandidateMutant]:
    tree = ast.parse(source)
    # tuple: (priority, operator, lineno, description, ast_site_idx)
    specs: list[tuple[int, str, int, str, int]] = []

    class Collector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.index = 0

        def visit_Raise(self, node: ast.Raise) -> None:
            if node.lineno in target_lines:
                specs.append((0, "RaiseDrop", node.lineno, f"Replace raise with pass at line {node.lineno}", self.index))
                self.index += 1
            self.generic_visit(node)

        def visit_Compare(self, node: ast.Compare) -> None:
            if node.lineno in target_lines and node.ops:
                op = node.ops[0]
                if isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                    specs.append((1, "CompareFlip", node.lineno, f"Flip comparison operator at line {node.lineno}", self.index))
                    self.index += 1
            self.generic_visit(node)

        def visit_BoolOp(self, node: ast.BoolOp) -> None:
            if node.lineno in target_lines and isinstance(node.op, (ast.And, ast.Or)):
                specs.append((2, "BoolOpFlip", node.lineno, f"Flip boolean operator at line {node.lineno}", self.index))
                self.index += 1
            self.generic_visit(node)

        def visit_Return(self, node: ast.Return) -> None:
            if node.lineno in target_lines and node.value is not None:
                specs.append((3, "ReturnNone", node.lineno, f"Replace return value with None at line {node.lineno}", self.index))
                self.index += 1
            self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant) -> None:
            if node.lineno in target_lines and isinstance(node.value, (bool, int)):
                specs.append((4, "ConstantMutate", node.lineno, f"Mutate constant {node.value!r} at line {node.lineno}", self.index))
                self.index += 1

    Collector().visit(tree)
    specs.sort(key=lambda item: (item[0], item[2], item[4]))

    mutants: list[_CandidateMutant] = []
    for _, operator, lineno, desc, target_idx in specs:
        cloned = copy.deepcopy(tree)
        transformer = _SingleMutantTransformer(target_idx=target_idx, target_lines=target_lines)
        mutated_tree = transformer.visit(cloned)
        ast.fix_missing_locations(mutated_tree)
        try:
            code = ast.unparse(mutated_tree) + "\n"
            ast.parse(code)
        except SyntaxError:
            continue
        if code != source:
            mutants.append(
                _CandidateMutant(
                    operator=operator,
                    lineno=lineno,
                    description=desc,
                    mutated_source=code,
                )
            )
    return mutants


class _SingleMutantTransformer(ast.NodeTransformer):
    """Apply exactly one AST mutation matching the collected site index within target_lines."""

    def __init__(self, *, target_idx: int, target_lines: set[int]) -> None:
        self.target_idx = target_idx
        self.target_lines = target_lines
        self.current = 0

    def _consume_if_match(self) -> bool:
        matched = self.current == self.target_idx
        self.current += 1
        return matched

    def visit_Raise(self, node: ast.Raise) -> ast.AST:
        if getattr(node, "lineno", -1) in self.target_lines and self._consume_if_match():
            return ast.Pass()
        self.generic_visit(node)
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        if getattr(node, "lineno", -1) in self.target_lines and node.ops and isinstance(
            node.ops[0], (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
        ):
            if self._consume_if_match():
                flip_map: dict[type[ast.cmpop], ast.cmpop] = {
                    ast.Eq: ast.NotEq(),
                    ast.NotEq: ast.Eq(),
                    ast.Lt: ast.GtE(),
                    ast.GtE: ast.Lt(),
                    ast.Gt: ast.LtE(),
                    ast.LtE: ast.Gt(),
                }
                node.ops[0] = flip_map[type(node.ops[0])]
                return node
        self.generic_visit(node)
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        if getattr(node, "lineno", -1) in self.target_lines and isinstance(node.op, (ast.And, ast.Or)):
            if self._consume_if_match():
                node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
                return node
        self.generic_visit(node)
        return node

    def visit_Return(self, node: ast.Return) -> ast.AST:
        if getattr(node, "lineno", -1) in self.target_lines and node.value is not None and self._consume_if_match():
            node.value = ast.copy_location(ast.Constant(value=None), node.value)
            return node
        self.generic_visit(node)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if getattr(node, "lineno", -1) in self.target_lines and isinstance(node.value, (bool, int)):
            if self._consume_if_match():
                if isinstance(node.value, bool):
                    return ast.copy_location(ast.Constant(value=not node.value), node)
                return ast.copy_location(ast.Constant(value=node.value + 1), node)
        return node
