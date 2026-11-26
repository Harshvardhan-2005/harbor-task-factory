"""End-to-end integration test covering generation, Harbor packaging, verification, mutation, and dashboard."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harborforge.cli import main as cli_main
from harborforge.dashboard import build_tasks_snapshot, dispatch_dashboard_request
from harborforge.llm_client import LLMClient
from harborforge.models import FeatureProposal
from harborforge.repo_analyzer import analyze_repository
from harborforge.synthesizer import synthesize_task_artifact
from harborforge.verifier import verify_and_repair_task


class _MockRepairLLM:
    """Mock LLM that intentionally fails first synthesis attempt then repairs on retry."""

    model_label = "mock:repair-llm"

    def __init__(self, orig_file_text: str) -> None:
        self.calls = 0
        self.orig = orig_file_text

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> str:
        self.calls += 1
        if self.calls == 1:
            # First call omits implementation -> oracle will fail with 0.0 -> triggers verify_and_repair
            new_src = self.orig + "\n\ndef greet_badge(name: str) -> str:\n    return 'WRONG'\n"
        else:
            new_src = self.orig + "\n\ndef greet_badge(name: str) -> str:\n    return f'[{name.upper()}]'\n"
        return json.dumps(
            {
                "instruction_md": (
                    "# Feature: Badge Formatter\n\n"
                    "Implement `greet_badge(name: str) -> str` in `mini_click.formatting` returning `[{name.upper()}]`."
                ),
                "modified_files": {"src/mini_click/formatting.py": new_src},
                "test_file_content": (
                    "from mini_click.formatting import greet_badge\n\n"
                    "def test_badge_uppercases_name():\n"
                    "    assert greet_badge('harbor') == '[HARBOR]'\n"
                ),
                "fail_to_pass_functions": ["test_badge_uppercases_name"],
            }
        )


def _create_mini_click_repo(repo_dir: Path) -> str:
    src_pkg = repo_dir / "src" / "mini_click"
    tests_dir = repo_dir / "tests"
    src_pkg.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    (repo_dir / "LICENSE.txt").write_text("BSD 3-Clause License\n", encoding="utf-8")
    (repo_dir / "pyproject.toml").write_text(
        "[project]\nname = 'mini_click'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (src_pkg / "__init__.py").write_text("__version__ = '0.1.0'\n", encoding="utf-8")
    (src_pkg / "types.py").write_text("def normalize_str(v: str) -> str:\n    return v.strip()\n", encoding="utf-8")
    (src_pkg / "core.py").write_text("class Command:\n    def __init__(self, name: str):\n        self.name = name\n", encoding="utf-8")
    (src_pkg / "formatting.py").write_text("def format_heading(title: str) -> str:\n    return f'== {title} =='\n", encoding="utf-8")
    (src_pkg / "decorators.py").write_text("def identity(fn):\n    return fn\n", encoding="utf-8")
    (src_pkg / "utils.py").write_text("def safe_name(s: str) -> str:\n    return s.replace(' ', '_')\n", encoding="utf-8")
    (tests_dir / "test_baseline.py").write_text(
        "from mini_click.types import normalize_str\n"
        "from mini_click.core import Command\n\n"
        "def test_normalize():\n"
        "    assert normalize_str(' hi ') == 'hi'\n\n"
        "def test_command_name():\n"
        "    assert Command('run').name == 'run'\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial stable baseline"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "tag", "v0.1.0"], cwd=repo_dir, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()


class TestEndToEndHarborForge(unittest.TestCase):
    def setUp(self) -> None:
        self.pytest_bin = shutil.which("pytest")
        if not self.pytest_bin:
            self.skipTest("pytest executable not available in PATH")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "mini_click"
        self.out_dir = self.root / "generated_tasks"
        self.base_commit = _create_mini_click_repo(self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_full_cli_pipeline_generate_verify_mutate_and_dashboard(self) -> None:
        # Ensure verify subprocesses pick the python that has pytest installed
        pytest_py = Path(self.pytest_bin).parent / "python3"
        if not pytest_py.exists():
            pytest_py = Path(sys.executable)

        import os
        old_py = os.environ.get("HARBORFORGE_PYTHON")
        os.environ["HARBORFORGE_PYTHON"] = str(pytest_py)
        try:
            rc = cli_main([
                "generate",
                "--repo", str(self.repo),
                "--out", str(self.out_dir),
                "--count", "5",
                "--provider", "offline",
                "--verify",
                "--verify-mode", "local",
                "--mutate",
            ])
        finally:
            if old_py is None:
                os.environ.pop("HARBORFORGE_PYTHON", None)
            else:
                os.environ["HARBORFORGE_PYTHON"] = old_py

        self.assertEqual(rc, 0)

        summary_path = self.out_dir / "validation_summary.json"
        self.assertTrue(summary_path.is_file())
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["base_commit"], self.base_commit)
        self.assertEqual(summary["task_count"], 5)

        verifications = summary["verifications"]
        self.assertEqual(len(verifications), 5)
        for v in verifications:
            self.assertTrue(v["passed"], f"Verification failed for {v['task_id']}: {v}")
            self.assertEqual(v["nop_reward"], 0.0)
            self.assertEqual(v["oracle_reward"], 1.0)

        mutations = summary["mutations"]
        self.assertEqual(len(mutations), 5)
        for m in mutations:
            self.assertGreater(m["total_mutants"], 0)
            self.assertGreaterEqual(m["kill_rate"], 0.75, f"Low mutation kill rate for {m['task_id']}: {m}")

        # Verify strict Harbor v0.25.x task folder contents
        snapshot = build_tasks_snapshot(self.out_dir)
        self.assertEqual(snapshot["task_count"], 5)
        self.assertEqual(len(snapshot["subsystems"]), 5)

        for task in snapshot["tasks"]:
            tdir = self.out_dir / task["task_id"]
            self.assertTrue((tdir / "instruction.md").is_file())
            self.assertTrue((tdir / "environment" / "Dockerfile").is_file())
            self.assertTrue((tdir / "solution" / "solve.sh").is_file())
            self.assertTrue((tdir / "solution" / "solution.patch").is_file())
            self.assertTrue((tdir / "tests" / "test.sh").is_file())
            self.assertTrue((tdir / "tests" / "fail_to_pass.json").is_file())
            self.assertTrue((tdir / "tests" / "pass_to_pass.json").is_file())
            toml_data = tomllib.loads((tdir / "task.toml").read_text(encoding="utf-8"))
            self.assertEqual(toml_data["metadata"]["base_commit"], self.base_commit)
            self.assertEqual(len(toml_data["metadata"]["base_commit"]), 40)

        # Verify dashboard router (/api/summary, /, and 404)
        status, ctype, body = dispatch_dashboard_request(self.out_dir, "/api/summary")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        api_data = json.loads(body.decode("utf-8"))
        self.assertEqual(api_data["task_count"], 5)

        status_html, ctype_html, html_body = dispatch_dashboard_request(self.out_dir, "/")
        self.assertEqual(status_html, 200)
        self.assertIn("text/html", ctype_html)
        self.assertIn("HarborForge", html_body.decode("utf-8"))

        status_404, _, _ = dispatch_dashboard_request(self.out_dir, "/missing")
        self.assertEqual(status_404, 404)

    def test_auto_repair_loop_recovers_from_failed_oracle(self) -> None:
        pytest_py = Path(self.pytest_bin).parent / "python3"
        if not pytest_py.exists():
            pytest_py = Path(sys.executable)

        profile = analyze_repository(self.repo, collect_tests=True, python_bin=str(pytest_py))
        orig_fmt = (self.repo / "src" / "mini_click" / "formatting.py").read_text(encoding="utf-8")
        mock_llm: LLMClient = _MockRepairLLM(orig_fmt)
        proposal = FeatureProposal(
            task_id="task_repair_badge",
            slug="badge-formatter",
            title="Badge Formatter",
            target_subsystem="mini_click.formatting",
            target_file="src/mini_click/formatting.py",
            behavioral_axis="output_formatting",
            summary="Add greet_badge function",
            acceptance_criteria=["Uppercases name inside brackets"],
        )
        from harborforge.packager import package_harbor_task

        artifact = synthesize_task_artifact(proposal, profile, llm_client=mock_llm)
        task_dir = package_harbor_task(artifact, profile, output_root=self.out_dir)

        _, result = verify_and_repair_task(
            task_dir,
            proposal=proposal,
            profile=profile,
            output_root=self.out_dir,
            mode="local",
            llm_client=mock_llm,
            max_repairs=2,
            python_bin=str(pytest_py),
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.nop_reward, 0.0)
        self.assertEqual(result.oracle_reward, 1.0)
        self.assertEqual(mock_llm.calls, 2)


if __name__ == "__main__":
    unittest.main()
