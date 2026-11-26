# HarborForge — Technical Architecture & Validation Report

## 1. System Architecture & Design Decisions

HarborForge separates **task planning**, **artifact synthesis**, **static/security auditing**, **Harbor packaging**, **execution verification**, and **mutation testing** into independent deterministic stages:

1. **Repository Grounding (`repo_analyzer.py`)**: Anchors every run to an immutable 40-character commit SHA (`base_commit`) and verifies existing tests via `pytest --collect-only`. Parsing `src/` via Python's `ast` gives the planner exact visibility into public classes, signatures, and module boundaries while feeding recent `git log` entries into the prompt to prevent reinventing existing upstream behavior.
2. **Enforced Task Diversity (`feature_planner.py`)**: Instead of asking an LLM for five tasks in an unconstrained list (which commonly degenerates into five similar CLI flags), the planner assigns distinct target files and five orthogonal behavioral axes (`input_validation`, `command_execution`, `output_formatting`, `lifecycle_decorators`, `runtime_utilities`) and enforces a pairwise token Jaccard similarity ceiling.
3. **Zero-Leakage & Scope Guard (`spec_auditor.py`)**: Automatically rejects bug-fix phrasing, checks that `instruction.md` never references `fail_to_pass`, `solve.sh`, test file names, or test function names, rejects outbound network/credential patterns, and validates `git apply --check` in a detached `git worktree`.
4. **Harbor v0.25.x Packaging Safeguards (`packager.py`)**:
   - **No `null` crashes on `nop`:** `tests/test.sh` avoids `set -e` around `pytest`, allowing `run_pytest_collector.py` and `grade_harbor.py` to execute even when new feature tests fail and write `0.0` cleanly to `/logs/verifier/reward.txt`.
   - **OS-safe regression checks:** Baseline tests that legitimately skip on Linux containers (`skipped`) do not trigger false regression penalties in `pass_to_pass` grading, whereas any `failed`, `error`, or `missing` regression test forces reward `0.0`.
   - **Mount-agnostic `solve.sh`:** The reference patch is embedded via heredoc inside `solution/solve.sh` (in addition to `solution/solution.patch`) so `harbor run -a oracle` succeeds regardless of `/oracle` vs `/solution` mount paths.
   - **Zero test leakage into container image:** `environment/Dockerfile` builds only the unmodified `base_commit` repository; hidden feature tests are injected strictly by `tests/test.sh` at grading time.

## 2. Models, Frameworks, and Tools Disclosed

| Component | Tool / Model | Purpose |
| :--- | :--- | :--- |
| Task Evaluation Harness | Harbor `0.25.x` + Docker | Containerized `nop` (`0.0`) and `oracle` (`1.0`) evaluation |
| Target Repository | `pallets/click` (Python + `pytest`) | Baseline open-source CLI toolkit |
| Pipeline Runtime | Python 3.11+ (`ast`, `tomllib`, `git`) | AST analysis, worktree diffing, mutation engine, Harbor packager |
| Synthesis Backends | Configurable (`gemini-2.5-pro`, `claude-sonnet-4`, OpenAI, local Ollama) | Feature proposal & reference patch/test synthesis (with offline test fixture mode) |

## 3. Validation Matrix & Mutation Evidence

Populate this table from `validation_summary.json` after executing `harborforge verify` and `harborforge mutate` against your pinned `pallets/click` commit:

| Task ID | Subsystem | Behavioral Axis | `nop` Reward | `oracle` Reward | Mutation Kill Rate |
| :--- | :--- | :--- | :---: | :---: | :---: |
| `task_01_*` | `click.types` | `input_validation` | `0.0` | `1.0` | *Fill from run* |
| `task_02_*` | `click.core` | `command_execution` | `0.0` | `1.0` | *Fill from run* |
| `task_03_*` | `click.formatting` | `output_formatting` | `0.0` | `1.0` | *Fill from run* |
| `task_04_*` | `click.decorators` | `lifecycle_decorators` | `0.0` | `1.0` | *Fill from run* |
| `task_05_*` | `click.utils` | `runtime_utilities` | `0.0` | `1.0` | *Fill from run* |

## 4. Known Assumptions & Failure Recovery

- Tasks assume pure-Python standard library or existing repository dependencies (`pytest` available inside container).
- If an LLM-generated patch fails `git apply --check` or leaks test identifiers, `synthesize_task_artifact` automatically feeds audit diagnostics back into up to 3 synthesis retries before failing fast.
