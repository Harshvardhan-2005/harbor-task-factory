"""Core data models for the HarborForge task generation pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SymbolSummary:
    """Summary of a public class or function extracted via Python AST."""

    kind: str  # "class" or "function"
    name: str
    signature: str
    lineno: int
    docstring: str = ""
    methods: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubsystemProfile:
    """AST-derived profile for a single module/subsystem in the target repo."""

    module_name: str
    rel_path: str
    line_count: int
    imports: list[str] = field(default_factory=list)
    symbols: list[SymbolSummary] = field(default_factory=list)
    candidate_axis: str = "general"


@dataclass(slots=True)
class RepoProfile:
    """Snapshot of the verified repository baseline commit and structure."""

    repo_path: str
    repo_name: str
    remote_url: str
    base_commit: str
    branch_or_tag: str
    license_name: str
    is_clean: bool
    subsystems: list[SubsystemProfile] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    existing_test_nodeids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FeatureProposal:
    """A planned, subsystem-orthogonal feature enhancement."""

    task_id: str
    slug: str
    title: str
    target_subsystem: str
    target_file: str
    behavioral_axis: str
    summary: str
    acceptance_criteria: list[str]
    non_goals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SynthesizedArtifact:
    """Generated code patch, specification, and feature tests before packaging."""

    proposal: FeatureProposal
    instruction_md: str
    patch_diff: str
    test_file_rel_path: str
    test_file_content: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    model_used: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AuditResult:
    """Result from the anti-leakage, security-scope, and patch-apply auditor."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VerificationResult:
    """Harbor (or local worktree) nop/oracle verification outcome."""

    task_id: str
    mode: str  # "harbor" or "local"
    nop_reward: float | None
    oracle_reward: float | None
    passed: bool
    nop_details: dict[str, Any] = field(default_factory=dict)
    oracle_details: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MutantOutcome:
    """Single mutant execution outcome."""

    mutant_id: str
    operator: str
    file_path: str
    lineno: int
    description: str
    killed: bool
    reward: float | None
    error: str = ""


@dataclass(slots=True)
class MutationReport:
    """Aggregated mutation testing score for a generated task package."""

    task_id: str
    total_mutants: int
    killed_mutants: int
    survived_mutants: int
    kill_rate: float
    outcomes: list[MutantOutcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
