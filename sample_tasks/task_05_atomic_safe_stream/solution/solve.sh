#!/usr/bin/env bash
set -euo pipefail

cd "${HARBORFORGE_WORKDIR:-/app}"
git apply --whitespace=nowarn - <<'HARBORFORGE_PATCH_EOF_9F3A'
diff --git a/src/click/_compat.py b/src/click/_compat.py
--- a/src/click/_compat.py
+++ b/src/click/_compat.py
@@ -621,3 +621,30 @@
     "stdout": get_text_stdout,
     "stderr": get_text_stderr,
 }
+
+
+from contextlib import contextmanager as _hf_contextmanager
+import os as _hf_os
+
+
+@_hf_contextmanager
+def atomic_write_file(path: str | _hf_os.PathLike[str], *, encoding: str = "utf-8"):
+    """Open a temporary staging file and atomically commit it to `path` on clean exit."""
+    from pathlib import Path as _hf_Path
+
+    target = _hf_Path(path)
+    target.parent.mkdir(parents=True, exist_ok=True)
+    staging = target.with_name(f".{target.name}.tmp.{_hf_os.getpid()}")
+    handle = staging.open("w", encoding=encoding)
+    try:
+        yield handle
+        handle.flush()
+        _hf_os.fsync(handle.fileno())
+        handle.close()
+        _hf_os.replace(staging, target)
+    except Exception:
+        if not handle.closed:
+            handle.close()
+        if staging.exists():
+            staging.unlink()
+        raise
HARBORFORGE_PATCH_EOF_9F3A
