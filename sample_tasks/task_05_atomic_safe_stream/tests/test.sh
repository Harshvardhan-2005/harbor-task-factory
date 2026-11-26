#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HARBORFORGE_WORKDIR:-/app}"
LOG_DIR="${HARBORFORGE_VERIFIER_LOG_DIR:-/logs/verifier}"
PY_BIN="${HARBORFORGE_PYTHON:-python3}"
RESULTS_JSON="$LOG_DIR/pytest_results.json"

mkdir -p "$LOG_DIR"
mkdir -p "$APP_DIR/tests"

# Inject the hidden feature test file strictly at verification time
cp "$SCRIPT_DIR/test_hf_atomic_safe_stream.py" "$APP_DIR/tests/test_hf_atomic_safe_stream.py"

if [ -d "$APP_DIR/src" ]; then
    export PYTHONPATH="$APP_DIR/src:$APP_DIR:${PYTHONPATH:-}"
else
    export PYTHONPATH="$APP_DIR:${PYTHONPATH:-}"
fi

cd "$APP_DIR"
"$PY_BIN" "$SCRIPT_DIR/run_pytest_collector.py" \
    --output "$RESULTS_JSON" \
    --target "tests" || true

"$PY_BIN" "$SCRIPT_DIR/grade_harbor.py" \
    --results "$RESULTS_JSON" \
    --fail-to-pass "$SCRIPT_DIR/fail_to_pass.json" \
    --pass-to-pass "$SCRIPT_DIR/pass_to_pass.json" \
    --reward-txt "$LOG_DIR/reward.txt" \
    --reward-json "$LOG_DIR/reward.json"

exit 0
