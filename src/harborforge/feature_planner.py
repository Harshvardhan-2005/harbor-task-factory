"""Orthogonal feature variation planner for HarborForge."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .llm_client import LLMClient, extract_json_object
from .models import FeatureProposal, RepoProfile, SubsystemProfile

REQUIRED_AXES: tuple[str, ...] = (
    "input_validation",
    "command_execution",
    "output_formatting",
    "lifecycle_decorators",
    "runtime_utilities",
)

PLANNER_SYSTEM_PROMPT = """You are a principal software architect designing synthetic feature-development benchmark tasks.

Rules (STRICT):
1. Every task MUST be a brand-new feature or substantive behavior extension. Bug fixes, crash fixes, typos, docstrings, or refactors are FORBIDDEN.
2. Every task MUST target a DIFFERENT module/subsystem and a DIFFERENT behavioral axis.
3. No feature may duplicate existing classes, functions, flags, or recent commits shown in the repository snapshot.
4. Features must require realistic behavioral logic, edge-case handling, and backward compatibility with existing tests.
5. No task may require external network calls, credentials, or introduce security vulnerabilities.

Return ONLY valid JSON:
{
  "proposals": [
    {
      "slug": "short-kebab-slug",
      "title": "Human-readable feature title",
      "target_subsystem": "exact module_name from profile",
      "target_file": "exact rel_path from profile",
      "behavioral_axis": "one of input_validation|command_execution|output_formatting|lifecycle_decorators|runtime_utilities",
      "summary": "2-3 sentences explaining the new user-facing capability and rules",
      "acceptance_criteria": ["concrete testable rule 1", "rule 2", "rule 3", "rule 4"],
      "non_goals": ["explicit boundary 1"]
    }
  ]
}
"""


def plan_orthogonal_features(
    profile: RepoProfile,
    *,
    count: int = 5,
    llm_client: LLMClient | None = None,
) -> list[FeatureProposal]:
    """Produce `count` distinct, non-overlapping feature proposals from a RepoProfile."""
    if count < 1:
        raise ValueError("count must be >= 1")
    if not profile.subsystems:
        raise ValueError("RepoProfile contains no Python subsystems to plan features against.")

    if llm_client is None:
        proposals = _build_offline_proposals(profile, count=count)
    else:
        user_prompt = _build_planner_prompt(profile, count=count)
        raw = llm_client.complete(PLANNER_SYSTEM_PROMPT, user_prompt, temperature=0.3)
        parsed = extract_json_object(raw)
        raw_items = parsed.get("proposals")
        if not isinstance(raw_items, list) or len(raw_items) < count:
            raise ValueError(f"Expected at least {count} proposals from LLM, got {raw_items!r}")
        proposals = [_coerce_proposal(idx + 1, item, profile) for idx, item in enumerate(raw_items[:count])]

    validate_diversity(proposals)
    return proposals


def validate_diversity(proposals: list[FeatureProposal], *, max_jaccard: float = 0.55) -> None:
    """Verify tasks are substantively distinct across subsystems, axes, and vocabulary."""
    if len(proposals) > 1:
        axes = [p.behavioral_axis for p in proposals]
        if len(set(axes)) < min(len(proposals), len(REQUIRED_AXES)):
            raise ValueError(f"Proposals lack distinct behavioral axes: {axes}")

        target_files = [p.target_file for p in proposals]
        if len(set(target_files)) < min(len(proposals), 3):
            raise ValueError(f"Proposals cluster on too few target files: {target_files}")

    for i in range(len(proposals)):
        for j in range(i + 1, len(proposals)):
            sim = _jaccard_similarity(
                f"{proposals[i].title} {proposals[i].summary}",
                f"{proposals[j].title} {proposals[j].summary}",
            )
            if sim > max_jaccard:
                raise ValueError(
                    f"Proposals '{proposals[i].slug}' and '{proposals[j].slug}' are too similar "
                    f"(Jaccard={sim:.2f} > {max_jaccard:.2f})."
                )


def _build_planner_prompt(profile: RepoProfile, *, count: int) -> str:
    subsystem_lines = []
    for sub in profile.subsystems[:15]:
        sym_preview = ", ".join(s.name for s in sub.symbols[:10])
        subsystem_lines.append(
            f"- module={sub.module_name} | path={sub.rel_path} | axis_hint={sub.candidate_axis} | symbols=[{sym_preview}]"
        )
    recent = "\n".join(f"  * {c}" for c in profile.recent_commits[:15]) or "  * (none)"
    return (
        f"Repository: {profile.repo_name} ({profile.remote_url})\n"
        f"Baseline commit: {profile.base_commit} ({profile.branch_or_tag})\n"
        f"Generate exactly {count} substantively different new feature proposals.\n\n"
        f"Available subsystems:\n" + "\n".join(subsystem_lines) + "\n\n"
        f"Recent upstream commits (DO NOT duplicate these):\n{recent}\n"
    )


def _coerce_proposal(index: int, raw: object, profile: RepoProfile) -> FeatureProposal:
    if not isinstance(raw, dict):
        raise ValueError(f"Proposal #{index} is not an object: {raw!r}")

    target_file = str(raw.get("target_file", "")).strip()
    known_paths = {s.rel_path: s for s in profile.subsystems}
    if target_file not in known_paths:
        sub_name = str(raw.get("target_subsystem", "")).strip()
        matched = next((s for s in profile.subsystems if s.module_name == sub_name), None)
        if matched is None:
            matched = profile.subsystems[(index - 1) % len(profile.subsystems)]
        target_file = matched.rel_path

    sub = known_paths.get(target_file) or profile.subsystems[(index - 1) % len(profile.subsystems)]
    slug = _slugify(str(raw.get("slug", f"feature-{index}")))
    criteria = raw.get("acceptance_criteria") or []
    non_goals = raw.get("non_goals") or []

    return FeatureProposal(
        task_id=f"task_{index:02d}_{slug.replace('-', '_')}",
        slug=slug,
        title=str(raw.get("title", f"Feature {index}")).strip(),
        target_subsystem=sub.module_name,
        target_file=sub.rel_path,
        behavioral_axis=str(raw.get("behavioral_axis", sub.candidate_axis)).strip(),
        summary=str(raw.get("summary", "")).strip(),
        acceptance_criteria=[str(c).strip() for c in criteria if str(c).strip()],
        non_goals=[str(g).strip() for g in non_goals if str(g).strip()],
    )


def _build_offline_proposals(profile: RepoProfile, *, count: int) -> list[FeatureProposal]:
    """Deterministic subsystem-diverse proposals for offline pipeline testing."""
    sub_by_axis: dict[str, SubsystemProfile] = {}
    for sub in profile.subsystems:
        sub_by_axis.setdefault(sub.candidate_axis, sub)

    def pick_sub(axis: str, fallback_idx: int) -> SubsystemProfile:
        return sub_by_axis.get(axis) or profile.subsystems[fallback_idx % len(profile.subsystems)]

    templates = [
        (
            "duration-range-param",
            "Bounded Duration Parameter Type",
            "input_validation",
            "Add a DurationRange ParamType supporting human-readable duration strings (ms, s, m, h, d) "
            "converted to integer seconds with inclusive min/max validation and clear Context-aware BadParameter messages.",
            [
                "Accept composite duration literals like '1h30m' and '45s' and normalize to seconds",
                "Enforce optional min_seconds and max_seconds bounds cleanly",
                "Reject negative, empty, unknown-unit, or out-of-range durations with contextual error messages",
                "Expose clean metavar metadata for CLI help output",
            ],
        ),
        (
            "option-mutex-groups",
            "Mutually Exclusive Option Constraint Validation",
            "command_execution",
            "Introduce mutually exclusive and required-together option constraint descriptors on Command "
            "objects evaluated deterministically during argument parsing before command callback invocation.",
            [
                "Reject CLI invocations supplying two mutually exclusive options with a UsageError",
                "Allow invocations where at most one option in a mutually exclusive group is supplied",
                "Support required_together groups that reject partial option subsets",
                "Preserve standard Click behavior when no constraints are declared",
            ],
        ),
        (
            "structured-help-json",
            "Structured JSON Help Schema Emitter",
            "output_formatting",
            "Add a structured help formatter helper that serializes Command metadata, options, defaults, "
            "and subcommands into deterministic JSON for programmatic CLI inspection.",
            [
                "Serialize command name, help summary, parameters, types, defaults, and required flags",
                "Include nested subcommand metadata recursively when formatting a Group",
                "Exclude hidden options unless include_hidden=True is explicitly requested",
                "Produce deterministic, valid JSON parseable by json.loads",
            ],
        ),
        (
            "command-alias-deprecation",
            "Command Alias Registry with Deprecation Warnings",
            "lifecycle_decorators",
            "Extend Group command registration to support secondary command aliases and optional "
            "deprecation notices emitted to stderr when an alias is invoked.",
            [
                "Resolve registered alias names transparently to the target Command",
                "Emit a standardized deprecation warning to stderr only when deprecated=True",
                "Prevent silent overwrite when registering conflicting duplicate aliases",
                "Display non-deprecated aliases cleanly in command help listings",
            ],
        ),
        (
            "atomic-safe-stream",
            "Bounded Rollback-Safe File Output Helper",
            "runtime_utilities",
            "Introduce a safe atomic file-writing utility context manager that buffers writes to a "
            "temporary sibling file and commits via atomic replace only on clean exit.",
            [
                "Write content to a staging file and atomically move into target path on success",
                "Discard staging file and leave existing target file untouched when an exception occurs",
                "Support configurable encoding and newline normalization",
                "Clean up temporary staging files on both success and failure paths",
            ],
        ),
    ]

    proposals: list[FeatureProposal] = []
    for idx in range(count):
        slug, title, axis, summary, criteria = templates[idx % len(templates)]
        if idx >= len(templates):
            slug = f"{slug}-{idx + 1}"
            title = f"{title} ({idx + 1})"
        sub = pick_sub(axis, idx)
        proposals.append(
            FeatureProposal(
                task_id=f"task_{idx + 1:02d}_{slug.replace('-', '_')}",
                slug=slug,
                title=title,
                target_subsystem=sub.module_name,
                target_file=sub.rel_path,
                behavioral_axis=axis,
                summary=summary,
                acceptance_criteria=criteria,
                non_goals=["Do not modify unrelated public APIs or break existing tests"],
            )
        )
    return proposals


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:48] or "feature-task"


def _jaccard_similarity(a: str, b: str) -> float:
    stop = {"the", "a", "an", "and", "or", "to", "for", "of", "in", "on", "with", "add", "support"}
    tok_a = set(re.findall(r"[a-z0-9]+", a.lower())) - stop
    tok_b = set(re.findall(r"[a-z0-9]+", b.lower())) - stop
    if not tok_a or not tok_b:
        return 0.0
    return len(tok_a & tok_b) / len(tok_a | tok_b)
