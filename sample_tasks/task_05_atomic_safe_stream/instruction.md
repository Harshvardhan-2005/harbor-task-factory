# Feature Specification: Bounded Rollback-Safe File Output Helper

Implement `atomic_write_file(path, *, encoding: str = "utf-8")` context manager in `click._compat` (`src/click/_compat.py`).

## Requirements
- Stage writes to a temporary file in the same directory and atomically replace the target on clean exit.
- On any exception inside the context block, preserve any pre-existing target file untouched and remove the temporary file.
- Leave zero temporary files behind after either success or failure.
