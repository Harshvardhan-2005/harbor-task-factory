#!/usr/bin/env bash
set -euo pipefail

cd "${HARBORFORGE_WORKDIR:-/app}"
git apply --whitespace=nowarn - <<'HARBORFORGE_PATCH_EOF_9F3A'
diff --git a/src/click/formatting.py b/src/click/formatting.py
--- a/src/click/formatting.py
+++ b/src/click/formatting.py
@@ -299,3 +299,28 @@
 
     rv.sort(key=lambda x: x[0])
     return ", ".join(x[1] for x in rv), any_prefix_is_slash
+
+
+def export_command_schema_json(
+    command_name: str,
+    options: list[dict[str, object]],
+    *,
+    include_hidden: bool = False,
+    indent: int = 2,
+) -> str:
+    """Serialize CLI command metadata and visible options into deterministic JSON."""
+    import json
+
+    filtered = [
+        {
+            "name": str(opt["name"]),
+            "type": str(opt.get("type", "str")),
+            "required": bool(opt.get("required", False)),
+            "default": opt.get("default"),
+        }
+        for opt in options
+        if include_hidden or not bool(opt.get("hidden", False))
+    ]
+    filtered.sort(key=lambda item: item["name"])
+    payload = {"command": command_name, "options": filtered}
+    return json.dumps(payload, indent=indent, sort_keys=True)
HARBORFORGE_PATCH_EOF_9F3A
