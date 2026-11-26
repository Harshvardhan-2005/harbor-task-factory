"""Lightweight stdlib HTTP dashboard for inspecting generated Harbor tasks & reports."""

from __future__ import annotations

import json
import tomllib
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def build_tasks_snapshot(tasks_dir: str | Path) -> dict[str, Any]:
    """Read generated Harbor tasks and validation reports into an inspectable JSON payload."""
    root = Path(tasks_dir).resolve()
    tasks: list[dict[str, Any]] = []

    report_path = root / "validation_summary.json"
    validation_map: dict[str, dict[str, Any]] = {}
    mutation_map: dict[str, dict[str, Any]] = {}
    if report_path.is_file():
        summary = json.loads(report_path.read_text(encoding="utf-8"))
        for item in summary.get("verifications", []):
            validation_map[item["task_id"]] = item
        for item in summary.get("mutations", []):
            mutation_map[item["task_id"]] = item

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not (entry / "task.toml").is_file():
            continue
        toml_data = tomllib.loads((entry / "task.toml").read_text(encoding="utf-8"))
        task_meta = toml_data.get("task", {})
        meta = toml_data.get("metadata", {})
        f2p = _read_json_list(entry / "tests" / "fail_to_pass.json")
        p2p = _read_json_list(entry / "tests" / "pass_to_pass.json")
        instr = _read_text(entry / "instruction.md")
        patch = _read_text(entry / "solution" / "solution.patch")

        tasks.append(
            {
                "task_id": entry.name,
                "name": task_meta.get("name", entry.name),
                "description": task_meta.get("description", ""),
                "subsystem": meta.get("subsystem", ""),
                "behavioral_axis": meta.get("behavioral_axis", ""),
                "base_commit": meta.get("base_commit", ""),
                "generator_model": meta.get("generator_model", ""),
                "fail_to_pass_count": len(f2p),
                "pass_to_pass_count": len(p2p),
                "fail_to_pass": f2p,
                "instruction_md": instr,
                "solution_patch": patch,
                "verification": validation_map.get(entry.name),
                "mutation": mutation_map.get(entry.name),
            }
        )

    return {
        "tasks_dir": str(root),
        "task_count": len(tasks),
        "subsystems": sorted({t["subsystem"] for t in tasks if t["subsystem"]}),
        "tasks": tasks,
    }


def dispatch_dashboard_request(tasks_dir: str | Path, raw_path: str) -> tuple[int, str, bytes]:
    """Route a dashboard HTTP GET path to (status_code, content_type, body_bytes)."""
    parsed = urllib.parse.urlparse(raw_path)
    if parsed.path == "/api/summary":
        payload = build_tasks_snapshot(tasks_dir)
        return HTTPStatus.OK, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8")
    if parsed.path in ("/", "/index.html"):
        return HTTPStatus.OK, "text/html; charset=utf-8", _DASHBOARD_HTML.encode("utf-8")
    return HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not Found"


def create_dashboard_server(
    tasks_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create a configured ThreadingHTTPServer for inspecting generated Harbor tasks."""
    root = Path(tasks_dir).resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            status, content_type, body = dispatch_dashboard_request(root, self.path)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve_dashboard(tasks_dir: str | Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start a blocking HTTP dashboard server for the generated task directory."""
    server = create_dashboard_server(tasks_dir, host=host, port=port)
    print(f"HarborForge Inspector running at http://{host}:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _read_json_list(path: Path) -> list[str]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>HarborForge Task Inspector</title>
  <style>
    :root { --bg:#0b1020; --panel:#131b31; --border:#243154; --text:#e6edf7; --muted:#8fa3c7; --ok:#22c55e; --warn:#f59e0b; --bad:#ef4444; --accent:#38bdf8; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; background:var(--bg); color:var(--text); }
    header { padding:20px 28px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; }
    h1 { margin:0; font-size:20px; letter-spacing:0.3px; }
    .sub { color:var(--muted); font-size:13px; margin-top:4px; }
    main { padding:24px 28px; max-width:1320px; margin:0 auto; }
    .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(210px, 1fr)); gap:14px; margin-bottom:22px; }
    .card { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:16px; }
    .card .label { font-size:12px; color:var(--muted); text-transform:uppercase; }
    .card .val { font-size:26px; font-weight:700; margin-top:6px; }
    .layout { display:grid; grid-template-columns:360px 1fr; gap:18px; }
    .list { display:flex; flex-direction:column; gap:10px; }
    .item { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:14px; cursor:pointer; }
    .item.active { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); }
    .badge { display:inline-block; font-size:11px; padding:2px 8px; border-radius:999px; font-weight:600; margin-right:6px; }
    .ok { background:rgba(34,197,94,.16); color:var(--ok); }
    .bad { background:rgba(239,68,68,.16); color:var(--bad); }
    .info { background:rgba(56,189,248,.16); color:var(--accent); }
    pre { background:#080c18; border:1px solid var(--border); border-radius:8px; padding:14px; overflow:auto; font-size:12px; line-height:1.45; max-height:360px; }
    .detail { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:20px; }
    h3 { margin:16px 0 8px; font-size:14px; color:var(--accent); }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>HarborForge — Coding Task Factory Inspector</h1>
      <div class="sub" id="dirLabel">Loading Harbor v0.25.x tasks...</div>
    </div>
    <span class="badge info">Harbor v0.25.x</span>
  </header>
  <main>
    <div class="cards" id="stats"></div>
    <div class="layout">
      <div class="list" id="taskList"></div>
      <div class="detail" id="detailPane">Select a task package to inspect its instruction, patch, and validation matrix.</div>
    </div>
  </main>
  <script>
    let snapshot = null;
    function renderDetail(idx) {
      const t = snapshot.tasks[idx];
      document.querySelectorAll('.item').forEach((el, i) => el.classList.toggle('active', i === idx));
      const v = t.verification;
      const m = t.mutation;
      const vText = v ? `nop=${v.nop_reward} | oracle=${v.oracle_reward} (${v.mode})` : 'Not run yet';
      const mText = m ? `${m.killed_mutants}/${m.total_mutants} killed (${Math.round(m.kill_rate * 100)}%)` : 'Not run yet';
      document.getElementById('detailPane').innerHTML = `
        <h2 style="margin-top:0">${escapeHtml(t.description)}</h2>
        <div class="sub">Subsystem: <code>${escapeHtml(t.subsystem)}</code> &bull; Axis: <code>${escapeHtml(t.behavioral_axis)}</code> &bull; Commit: <code>${escapeHtml(t.base_commit.slice(0,12))}</code></div>
        <div style="margin-top:10px">
          <span class="badge ${v && v.passed ? 'ok' : 'info'}">${escapeHtml(vText)}</span>
          <span class="badge ${m && m.kill_rate >= 0.8 ? 'ok' : 'info'}">Mutation: ${escapeHtml(mText)}</span>
          <span class="badge info">F2P: ${t.fail_to_pass_count} | P2P: ${t.pass_to_pass_count}</span>
        </div>
        <h3>fail_to_pass Targets</h3>
        <pre>${escapeHtml((t.fail_to_pass || []).join('\\n'))}</pre>
        <h3>instruction.md (Visible to Evaluated Agent)</h3>
        <pre>${escapeHtml(t.instruction_md)}</pre>
        <h3>solution/solution.patch (Oracle Reference Implementation)</h3>
        <pre>${escapeHtml(t.solution_patch)}</pre>
      `;
    }
    function escapeHtml(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    fetch('/api/summary').then(r => r.json()).then(data => {
      snapshot = data;
      document.getElementById('dirLabel').textContent = data.tasks_dir;
      const verified = data.tasks.filter(t => t.verification && t.verification.passed).length;
      const avgKill = data.tasks.filter(t => t.mutation).reduce((acc, t, _, a) => acc + t.mutation.kill_rate / a.length, 0);
      document.getElementById('stats').innerHTML = `
        <div class="card"><div class="label">Generated Tasks</div><div class="val">${data.task_count}</div></div>
        <div class="card"><div class="label">Distinct Subsystems</div><div class="val">${data.subsystems.length}</div></div>
        <div class="card"><div class="label">Harbor Pass (0.0 → 1.0)</div><div class="val">${verified}/${data.task_count}</div></div>
        <div class="card"><div class="label">Avg Mutation Kill Rate</div><div class="val">${Math.round(avgKill * 100)}%</div></div>
      `;
      const list = document.getElementById('taskList');
      list.innerHTML = data.tasks.map((t, i) => `
        <div class="item" onclick="renderDetail(${i})">
          <div style="font-weight:600">${t.task_id}</div>
          <div class="sub">${t.subsystem} &bull; ${t.behavioral_axis}</div>
        </div>
      `).join('');
      if (data.tasks.length) renderDetail(0);
    });
  </script>
</body>
</html>
"""
