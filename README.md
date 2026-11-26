# HarborForge — Automated AST-Guided & Mutation-Verified Coding Task Factory

Built for the **Innodata AI Hackathon — Track 1: Coding Task Factory**.

HarborForge ingests a verified baseline commit of **`pallets/click`** (or any compatible Python starter repository), analyzes its module AST and git history, synthesizes **5 substantively different feature-development task packages** conforming strictly to **Harbor v0.25.x**, validates the dual **`nop` (`0.0`)** / **`oracle` (`1.0`)** reward matrix inside isolated environments, and executes **AST mutation testing** to prove test-suite fault sensitivity.

---

## Key Capabilities

1. **AST Repository Analyzer (`repo_analyzer.py`)**
   - Records the full 40-character `base_commit` hash, repository metadata, and license.
   - Extracts public module/class/function API surfaces via Python `ast` and checks recent `git log` history so generated features do not duplicate upstream commits.
   - Collects baseline `pytest` node IDs for `tests/pass_to_pass.json`.
2. **Orthogonal Feature Variation Planner (`feature_planner.py`)**
   - Enforces 5 distinct target subsystems and 5 distinct behavioral axes (`input_validation`, `command_execution`, `output_formatting`, `lifecycle_decorators`, `runtime_utilities`) with Jaccard similarity bounds.
3. **Anti-Leakage & Security Spec Auditor (`spec_auditor.py`)**
   - Rejects bug-fix framing, test/solution identifiers leaking into `instruction.md`, outbound network or unsafe calls, and patches touching pre-existing tests.
   - Verifies `git apply --check` against `base_commit` in an isolated `git worktree`.
4. **Harbor v0.25.x Packager (`packager.py`)**
   - Emits compliant directories containing `task.toml`, `instruction.md`, `environment/Dockerfile`, `solution/solve.sh` (+ `solution/solution.patch`), `tests/test.sh`, `tests/fail_to_pass.json`, and `tests/pass_to_pass.json`.
   - Uses a non-`set -e` `test.sh` wrapper + `grade_harbor.py` so `nop` failures reliably record `0.0` in `/logs/verifier/reward.txt` rather than crashing to `null`.
5. **Dual-Mode Verifier (`verifier.py`) & AST Mutation Engine (`mutator.py`)**
   - Runs official `harbor run -p <task_dir> -a nop -k 1 -o <out_dir> --no-delete -y` (`0.0`) and `-a oracle` (`1.0`), plus fast local `git worktree` preflight checks.
   - Injects surgical AST mutations (`CompareFlip`, `RaiseDrop`, `ReturnNone`, `ConstantMutate`) into newly added lines and reports the **Mutation Kill Score**.
6. **Lightweight Web Inspector & CLI (`dashboard.py`, `cli.py`)**
   - Launches a zero-dependency local web dashboard to inspect generated tasks, patches, validation results, and mutation kill rates.

---

## Setup & Installation

### Prerequisites
- Python **3.11+**
- Git CLI
- Docker Engine running locally (required for Harbor container runs)
- Harbor CLI `0.25.x`:
  ```bash
  pip install "harbor==0.25.*"
  ```

### Install HarborForge
```bash
pip install -e .
```

### Set LLM Provider API Key (for Live LLM Synthesis)
Choose your preferred provider and keep credentials strictly in environment variables:
- **Gemini:** `export GEMINI_API_KEY="..."`
- **Anthropic (Claude):** `export ANTHROPIC_API_KEY="..."`
- **OpenAI-compatible:** `export OPENAI_API_KEY="..."`
- **Local Ollama:** ensure `ollama serve` is running locally

---

## Quickstart Workflow (with `pallets/click`)

### 1. Clone Starter Repository at a Verified Baseline Tag
```bash
git clone https://github.com/pallets/click.git /tmp/click
cd /tmp/click
git checkout 8.1.8
pytest   # Confirm 100% baseline tests pass before task generation
```

### 2. Generate 5 Orthogonal Feature Tasks
```bash
# Live LLM generation (e.g. Gemini or Anthropic) + verification + mutation testing:
harborforge generate \
  --repo /tmp/click \
  --out ./sample_tasks \
  --count 5 \
  --provider gemini \
  --verify \
  --mutate

# Or test the full pipeline deterministically offline without spending API tokens:
harborforge generate \
  --repo /tmp/click \
  --out ./sample_tasks \
  --count 5 \
  --provider offline \
  --verify \
  --verify-mode local \
  --mutate
```

### 3. Run Official Harbor Validation Commands
```bash
for task in ./sample_tasks/task_*; do
  harbor run -p "$task" -a nop -k 1 -o "./validation_evidence/$(basename "$task")_nop" --no-delete -y
  harbor run -p "$task" -a oracle -k 1 -o "./validation_evidence/$(basename "$task")_oracle" --no-delete -y
done
```

### 4. Inspect Results in the Web Dashboard
```bash
harborforge dashboard --tasks-dir ./sample_tasks --port 8765
```

---

## Running HarborForge's Own Test Suite
```bash
python3 -m unittest discover -s tests -v
```
