"""Unit tests for planner diversity, anti-leakage auditor, reward grader, and LLM parser."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harborforge.feature_planner import validate_diversity
from harborforge.llm_client import LLMError, extract_json_object
from harborforge.models import FeatureProposal, SynthesizedArtifact
from harborforge.packager import _HARBOR_GRADER_SCRIPT
from harborforge.spec_auditor import audit_artifact


def _load_grader_module():
    spec = importlib.util.spec_from_loader("harbor_grader_test", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(_HARBOR_GRADER_SCRIPT, module.__dict__)
    return module


class TestPlannerAndAuditor(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "sample_repo"
        (self.repo / "src" / "pkg").mkdir(parents=True)
        (self.repo / "src" / "pkg" / "types.py").write_text("def existing():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)
        self.base_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_diversity_rejects_duplicate_axes(self) -> None:
        p1 = FeatureProposal("t1", "slug-one", "Feature A", "pkg.types", "src/pkg/types.py", "input_validation", "Add duration type", ["c1"])
        p2 = FeatureProposal("t2", "slug-two", "Feature B", "pkg.core", "src/pkg/core.py", "input_validation", "Add regex type", ["c2"])
        with self.assertRaises(ValueError):
            validate_diversity([p1, p2])

    def test_auditor_rejects_leakage_bugfix_and_network(self) -> None:
        proposal = FeatureProposal(
            "t1", "cidr-type", "CIDR Validation", "pkg.types", "src/pkg/types.py",
            "input_validation", "Fixes a crash when passing CIDR strings", ["Validate IPv4"]
        )
        bad_patch = (
            "diff --git a/src/pkg/types.py b/src/pkg/types.py\n"
            "--- a/src/pkg/types.py\n"
            "+++ b/src/pkg/types.py\n"
            "@@ -1,2 +1,4 @@\n"
            " def existing():\n"
            "     return 1\n"
            "+def parse_cidr(v):\n"
            "+    return requests.get('https://evil.example')\n"
        )
        artifact = SynthesizedArtifact(
            proposal=proposal,
            instruction_md="Implement parse_cidr. Must pass test_valid_cidr in tests/test_hf_cidr.py.",
            patch_diff=bad_patch,
            test_file_rel_path="tests/test_hf_cidr.py",
            test_file_content="def test_valid_cidr():\n    assert True\n",
            fail_to_pass=["tests/test_hf_cidr.py::test_valid_cidr"],
            pass_to_pass=[],
            model_used="test",
        )
        res = audit_artifact(artifact, repo_path=self.repo, base_commit=self.base_commit)
        self.assertFalse(res.passed)
        joined = " | ".join(res.errors)
        self.assertIn("bug-fix", joined)
        self.assertIn("Spec leakage", joined)
        self.assertIn("Security/offline", joined)

    def test_extract_json_object_handles_fences(self) -> None:
        obj = extract_json_object("Here is the JSON:\n```json\n{\"ok\": true, \"n\": 5}\n```")
        self.assertEqual(obj, {"ok": True, "n": 5})
        with self.assertRaises(LLMError):
            extract_json_object("no json here")


class TestHarborGraderFormula(unittest.TestCase):
    def setUp(self) -> None:
        self.grader = _load_grader_module()

    def test_reward_matrix_rules(self) -> None:
        # 1. Regression failure overrides partial/full feature passes -> 0.0
        reward, details = self.grader.compute_reward(
            outcomes={"tests/t.py::f1": "passed", "tests/old.py::r1": "failed"},
            collection_errors=[],
            fail_to_pass=["tests/t.py::f1"],
            pass_to_pass=["tests/old.py::r1"],
        )
        self.assertEqual(reward, 0.0)
        self.assertEqual(details["reason"], "regression_failure")

        # 2. Skipped platform regression tests still allow 1.0 oracle pass
        reward, _ = self.grader.compute_reward(
            outcomes={"tests/t.py::f1": "passed", "tests/old.py::win_only": "skipped"},
            collection_errors=[],
            fail_to_pass=["tests/t.py::f1"],
            pass_to_pass=["tests/old.py::win_only"],
        )
        self.assertEqual(reward, 1.0)

        # 3. Partial feature passes give fractional reward
        reward, _ = self.grader.compute_reward(
            outcomes={"tests/t.py::f1": "passed", "tests/t.py::f2": "failed", "tests/old.py::r1": "passed"},
            collection_errors=[],
            fail_to_pass=["tests/t.py::f1", "tests/t.py::f2"],
            pass_to_pass=["tests/old.py::r1"],
        )
        self.assertEqual(reward, 0.5)

        # 4. Nop baseline (all feature tests fail, regressions pass) -> 0.0
        reward, _ = self.grader.compute_reward(
            outcomes={"tests/t.py::f1": "failed", "tests/old.py::r1": "passed"},
            collection_errors=[],
            fail_to_pass=["tests/t.py::f1"],
            pass_to_pass=["tests/old.py::r1"],
        )
        self.assertEqual(reward, 0.0)


class TestAstMutatorScope(unittest.TestCase):
    def test_mutator_only_touches_target_lines(self) -> None:
        from harborforge.mutator import _generate_ast_mutants

        src = (
            "def existing_legacy(x):\n"
            "    if x == 10:\n"
            "        raise ValueError('old')\n"
            "    return 42\n"
            "\n"
            "def new_feature(y):\n"
            "    if y == 5:\n"
            "        raise ValueError('new')\n"
            "    return 99\n"
        )
        mutants = _generate_ast_mutants(src, target_lines={7, 8, 9})
        self.assertTrue(len(mutants) >= 3)
        for m in mutants:
            self.assertIn(m.lineno, {7, 8, 9})
            # Legacy lines 1-4 must remain byte-for-byte identical
            self.assertTrue(
                m.mutated_source.startswith(
                    "def existing_legacy(x):\n"
                    "    if x == 10:\n"
                    "        raise ValueError('old')\n"
                    "    return 42"
                ),
                f"Mutant {m.operator} touched legacy lines:\n{m.mutated_source}",
            )


if __name__ == "__main__":
    unittest.main()
