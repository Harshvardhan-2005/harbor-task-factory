#!/usr/bin/env bash
set -euo pipefail

cd "${HARBORFORGE_WORKDIR:-/app}"
git apply --whitespace=nowarn - <<'HARBORFORGE_PATCH_EOF_9F3A'
diff --git a/src/click/core.py b/src/click/core.py
--- a/src/click/core.py
+++ b/src/click/core.py
@@ -3045,3 +3045,22 @@
 
     def add_to_parser(self, parser: OptionParser, ctx: Context) -> None:
         parser.add_argument(dest=self.name, nargs=self.nargs, obj=self)
+
+
+def validate_option_constraints(
+    supplied_options: set[str],
+    *,
+    mutually_exclusive: list[set[str]] | None = None,
+    required_together: list[set[str]] | None = None,
+) -> None:
+    """Validate mutually exclusive and required-together option constraint sets."""
+    for group in mutually_exclusive or []:
+        active = sorted(supplied_options & set(group))
+        if len(active) > 1:
+            raise ValueError(f"Options {', '.join(active)} are mutually exclusive.")
+    for group in required_together or []:
+        group_set = set(group)
+        present = supplied_options & group_set
+        if present and present != group_set:
+            missing = sorted(group_set - present)
+            raise ValueError(f"Options {', '.join(sorted(present))} require missing option(s): {', '.join(missing)}.")
HARBORFORGE_PATCH_EOF_9F3A
