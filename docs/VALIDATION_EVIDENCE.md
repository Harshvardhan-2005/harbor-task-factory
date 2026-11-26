# HarborForge Validation Evidence

## Target Repository & Baseline Commit

- **Repository:** `https://github.com/pallets/click.git`
- **Tag / Branch:** `8.1.8`
- **Base Commit (`base_commit`):** `934813e4d421071a1b3db3973c02fe2721359a6e`
- **Harbor Version:** `0.25.0`

## Automated Generation, Verification & Mutation Run

Command executed:

```bash
PYTHONPATH=src .venv/bin/harborforge generate \
  --repo repos/click \
  --out sample_tasks \
  --count 5 \
  --provider offline \
  --verify-mode local \
  --verify \
  --mutate
```

### Verification Matrix Results

| Task ID | Subsystem | Behavioral Axis | `nop` Reward | `oracle` Reward | `fail_to_pass` | `pass_to_pass` | Status |
|---|---|---|---|---|---|---|---|
| `task_01_duration_range_param` | `click.types` | `input_validation` | `0.0` | `1.0` | 3/3 passed (oracle) | 20/20 modules | PASS |
| `task_02_option_mutex_groups` | `click.core` | `command_execution` | `0.0` | `1.0` | 3/3 passed (oracle) | 20/20 modules | PASS |
| `task_03_structured_help_json` | `click.formatting` | `output_formatting` | `0.0` | `1.0` | 2/2 passed (oracle) | 20/20 modules | PASS |
| `task_04_command_alias_deprecation` | `click.decorators` | `lifecycle_decorators` | `0.0` | `1.0` | 2/2 passed (oracle) | 20/20 modules | PASS |
| `task_05_atomic_safe_stream` | `click._compat` | `runtime_utilities` | `0.0` | `1.0` | 2/2 passed (oracle) | 20/20 modules | PASS |

### Mutation Testing Results

| Task ID | Mutants Killed | Total Mutants | Kill Rate |
|---|---:|---:|---:|
| `task_01_duration_range_param` | 5 | 6 | 83.3% |
| `task_02_option_mutex_groups` | 6 | 6 | 100.0% |
| `task_03_structured_help_json` | 6 | 6 | 100.0% |
| `task_04_command_alias_deprecation` | 6 | 6 | 100.0% |
| `task_05_atomic_safe_stream` | 3 | 3 | 100.0% |
| **Total** | **26** | **27** | **96.3%** |

## Automated Pipeline Unit & Integration Tests

Command executed:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Result: `Ran 7 tests in 14.640s` — `OK` (all 7 tests passed).

## Real-World Regression Caught & Fixed During Validation

During initial verification against `pallets/click@8.1.8`:
1. **`click` `filterwarnings = ["error"]` under `pytest >= 9`**: `tests/test_basic.py` raised `pytest.PytestRemovedIn10Warning` during collection when run with `pytest 9.2.0`. Fixed by pinning `"pytest<9"` in the generated `Dockerfile` (matching `click`'s `requirements/tests.txt` `pytest==8.3.4`) and ignoring `PytestRemovedIn10Warning` in `run_pytest_collector.py` when running locally on `pytest >= 9`.
2. **`tests/test_imports.py::test_light_imports`**: `task_05_atomic_safe_stream` initially placed `from pathlib import Path` at module top level in `click/_compat.py`, which failed `click`'s import-time regression guard (`oracle=0.0`). Moving the `pathlib.Path` import inside `atomic_write_file()` resolved the regression (`oracle=1.0`).

## Raw Artifacts

- [`validation_evidence/validation_summary.json`](../validation_evidence/validation_summary.json)
- [`validation_evidence/generate_verify_mutate.log`](../validation_evidence/generate_verify_mutate.log)
