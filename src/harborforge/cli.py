"""Command-line interface for HarborForge Coding Task Factory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dashboard import serve_dashboard
from .feature_planner import plan_orthogonal_features
from .llm_client import build_llm_client
from .mutator import run_mutation_testing
from .packager import package_harbor_task
from .repo_analyzer import analyze_repository
from .synthesizer import synthesize_task_artifact
from .verifier import verify_and_repair_task, verify_task_package


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harborforge",
        description="Automated AST-guided, mutation-verified Harbor v0.25.x Coding Task Factory.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="Inspect repository AST, commit hash, and baseline tests.")
    p_analyze.add_argument("--repo", required=True, help="Path to local git repository clone.")
    p_analyze.add_argument("--no-collect-tests", action="store_true", help="Skip pytest collection.")

    p_gen = sub.add_parser("generate", help="Generate N orthogonal Harbor v0.25.x feature task packages.")
    p_gen.add_argument("--repo", required=True, help="Path to local git repository clone.")
    p_gen.add_argument("--out", required=True, help="Output directory for generated task packages.")
    p_gen.add_argument("--count", type=int, default=5, help="Number of distinct tasks to generate.")
    p_gen.add_argument(
        "--provider",
        default="offline",
        choices=["offline", "gemini", "anthropic", "openai", "ollama"],
        help="LLM backend for task synthesis (default: offline demo/test mode).",
    )
    p_gen.add_argument("--model", default=None, help="Optional model override for the selected provider.")
    p_gen.add_argument("--verify", action="store_true", help="Run nop/oracle verification after packaging.")
    p_gen.add_argument("--mutate", action="store_true", help="Run AST mutation testing after verification.")
    p_gen.add_argument(
        "--verify-mode",
        default="auto",
        choices=["auto", "harbor", "local"],
        help="Verification backend (default: auto).",
    )

    p_ver = sub.add_parser("verify", help="Run Harbor nop (0.0) and oracle (1.0) validation across tasks.")
    p_ver.add_argument("--tasks-dir", required=True, help="Directory containing generated Harbor task folders.")
    p_ver.add_argument("--repo", default=None, help="Local repository path (needed for --mode local).")
    p_ver.add_argument("--mode", default="auto", choices=["auto", "harbor", "local"])

    p_mut = sub.add_parser("mutate", help="Run AST mutation testing on generated Harbor tasks.")
    p_mut.add_argument("--tasks-dir", required=True, help="Directory containing generated Harbor task folders.")
    p_mut.add_argument("--repo", required=True, help="Local repository path.")
    p_mut.add_argument("--max-mutants", type=int, default=6)

    p_dash = sub.add_parser("dashboard", help="Launch local Web Inspector UI for generated tasks.")
    p_dash.add_argument("--tasks-dir", required=True, help="Directory containing generated Harbor task folders.")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", type=int, default=8765)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        profile = analyze_repository(args.repo, collect_tests=not args.no_collect_tests)
        print(json.dumps(profile.to_dict(), indent=2))
        return 0

    if args.command == "generate":
        llm_client = None if args.provider == "offline" else build_llm_client(args.provider, args.model)
        profile = analyze_repository(args.repo, collect_tests=True)
        if not profile.is_clean:
            print("Warning: Target repository has uncommitted changes; tasks still anchor to base_commit.", file=sys.stderr)

        proposals = plan_orthogonal_features(profile, count=args.count, llm_client=llm_client)
        out_root = Path(args.out).resolve()
        out_root.mkdir(parents=True, exist_ok=True)

        generated_pairs: list[tuple[object, Path]] = []
        for proposal in proposals:
            artifact = synthesize_task_artifact(proposal, profile, llm_client=llm_client)
            task_dir = package_harbor_task(artifact, profile, output_root=out_root)
            generated_pairs.append((proposal, task_dir))
            print(f"[PACKAGED] {task_dir.name} ({proposal.target_subsystem} | {proposal.behavioral_axis})")

        summary: dict[str, object] = {
            "repository": profile.remote_url,
            "base_commit": profile.base_commit,
            "task_count": len(generated_pairs),
            "verifications": [],
            "mutations": [],
        }

        all_ok = True
        verified_dirs: list[Path] = [pair[1] for pair in generated_pairs]
        if args.verify:
            ver_results = []
            verified_dirs = []
            for proposal, task_dir in generated_pairs:
                final_dir, res = verify_and_repair_task(
                    task_dir,
                    proposal=proposal,
                    profile=profile,
                    output_root=out_root,
                    mode=args.verify_mode,
                    llm_client=llm_client,
                )
                verified_dirs.append(final_dir)
                ver_results.append(res.to_dict())
                status = "PASS" if res.passed else "FAIL"
                print(f"[VERIFY:{res.mode}] {res.task_id} -> {status} (nop={res.nop_reward}, oracle={res.oracle_reward})")
                if not res.passed:
                    all_ok = False
            summary["verifications"] = ver_results

        if args.mutate:
            mut_results = []
            for task_dir in verified_dirs:
                try:
                    mres = run_mutation_testing(task_dir, repo_path=profile.repo_path)
                    mut_results.append(mres.to_dict())
                    print(
                        f"[MUTATE] {mres.task_id} -> {mres.killed_mutants}/{mres.total_mutants} killed "
                        f"({mres.kill_rate * 100:.1f}%)"
                    )
                except RuntimeError as exc:
                    all_ok = False
                    print(f"[MUTATE] {task_dir.name} -> SKIPPED ({exc})", file=sys.stderr)
            summary["mutations"] = mut_results

        (out_root / "validation_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return 0 if all_ok else 1

    if args.command == "verify":
        root = Path(args.tasks_dir).resolve()
        results = []
        all_ok = True
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not (entry / "task.toml").is_file():
                continue
            res = verify_task_package(entry, mode=args.mode, repo_path=args.repo)
            results.append(res.to_dict())
            print(f"[{res.mode}] {res.task_id}: nop={res.nop_reward} oracle={res.oracle_reward} passed={res.passed}")
            if not res.passed:
                all_ok = False
        _merge_summary(root / "validation_summary.json", "verifications", results)
        return 0 if all_ok else 1

    if args.command == "mutate":
        root = Path(args.tasks_dir).resolve()
        results = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not (entry / "task.toml").is_file():
                continue
            res = run_mutation_testing(entry, repo_path=args.repo, max_mutants=args.max_mutants)
            results.append(res.to_dict())
            print(f"[mutate] {res.task_id}: {res.killed_mutants}/{res.total_mutants} killed ({res.kill_rate * 100:.1f}%)")
        _merge_summary(root / "validation_summary.json", "mutations", results)
        return 0

    if args.command == "dashboard":
        serve_dashboard(args.tasks_dir, host=args.host, port=args.port)
        return 0

    return 1


def _merge_summary(summary_path: Path, key: str, items: list[dict[str, object]]) -> None:
    existing: dict[str, object] = {}
    if summary_path.is_file():
        existing = json.loads(summary_path.read_text(encoding="utf-8"))
    existing[key] = items
    summary_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
