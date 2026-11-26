#!/usr/bin/env bash
set -euo pipefail

cd "${HARBORFORGE_WORKDIR:-/app}"
git apply --whitespace=nowarn - <<'HARBORFORGE_PATCH_EOF_9F3A'
diff --git a/src/click/types.py b/src/click/types.py
--- a/src/click/types.py
+++ b/src/click/types.py
@@ -1091,3 +1091,31 @@
 
 #: A UUID parameter.
 UUID = UUIDParameterType()
+
+
+def parse_duration_seconds(value: str, *, min_seconds: int | None = None, max_seconds: int | None = None) -> int:
+    """Parse composite human-readable durations such as '1h30m' or '45s' into integer seconds."""
+    if not isinstance(value, str) or not value.strip():
+        raise ValueError("Duration string must be a non-empty string.")
+    import re
+
+    token_re = re.compile(r"(\d+)(ms|s|m|h|d)")
+    cleaned = value.strip().lower()
+    pos = 0
+    total_ms = 0
+    multipliers = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
+    for match in token_re.finditer(cleaned):
+        if match.start() != pos:
+            raise ValueError(f"Invalid duration segment near '{cleaned[pos:]}'")
+        amount = int(match.group(1))
+        unit = match.group(2)
+        total_ms += amount * multipliers[unit]
+        pos = match.end()
+    if pos != len(cleaned) or pos == 0:
+        raise ValueError(f"Invalid duration literal: {value!r}")
+    seconds = total_ms // 1000
+    if min_seconds is not None and seconds < min_seconds:
+        raise ValueError(f"Duration {seconds}s is below minimum {min_seconds}s")
+    if max_seconds is not None and seconds > max_seconds:
+        raise ValueError(f"Duration {seconds}s exceeds maximum {max_seconds}s")
+    return seconds
HARBORFORGE_PATCH_EOF_9F3A
