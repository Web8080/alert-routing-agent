# author: Victor Ibhafidon
# date: 2026-08-14
"""Zero-dependency web UI for the alert routing agent (stdlib only).

Serves a single-page dashboard from `alert_routing/static/` and exposes a tiny
JSON API that reuses the exact same router code path as the CLI:

    GET  /api/scenarios           → list of bundled demo scenarios
    POST /api/dispatch            → run a scenario (or a custom alert) and
                                    return trace + decisions + timeline as JSON

Usage:
    python -m alert_routing.ui [--port 8000] [--registry registry.json]

Open http://127.0.0.1:8000/ in a browser. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .cli import run_scenario_data
from .timeline import render_timeline

_UI_DIR = Path(__file__).resolve().parent / "static"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCENARIOS_DIR = _REPO_ROOT / "scenarios"
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}

# scenario name → (file stem, friendly description shown in the UI)
_SCENARIO_META = [
    ("scenario_1_offline", "Recipient goes offline mid-flight  (R2 abort + reroute)"),
    ("scenario_2_channel_fail", "Preferred channel fails mid-flight  (R1 channel retry)"),
    ("scenario_3_no_downgrade", "Senior-but-less-qualified appears  (R5 no-downgrade)"),
]

_lock = threading.Lock()


def _result_payload(router, alert_id: str) -> dict:
    """Serialize a finished dispatch into the JSON the dashboard renders."""
    trace = [{"ts": line.ts, "kind": line.kind, "text": line.text}
             for line in router.trace]
    decisions = router.ledger.decision_log(alert_id)
    notifications = router.ledger.notifications_for(alert_id)
    ranking = _ranking_payload(router)
    return {
        "alert_id": alert_id,
        "plan_state": router.ledger.plan_state(alert_id),
        "trace": trace,
        "decisions": decisions,
        "notifications": notifications,
        "ranking": ranking,
        "timeline_text": render_timeline(router.ledger, alert_id),
        "policy_codes": [d["code"] for d in decisions],
    }


def _ranking_payload(router) -> list[dict]:
    """Raw qualification ranking + availability + gating (what RANK shows).

    Sorted by qualification descending (ties broken by id) so the demo table
    matches the trace's RANK lines exactly. Gated candidates are retained —
    that's the 'qualified-but-unavailable' talking point."""
    rows = []
    for sid, snap in router.snapshots.items():
        st = router.stakeholders.get(sid)
        rows.append({
            "sid": sid,
            "name": snap.name,
            "title": st.title if st else "",
            "seniority": st.seniority if st else 0,
            "qualification": round(snap.qualification, 2),
            "online": snap.online,
            "on_call": st.on_call if st else False,
            "gated": snap.gated,
            "channels": [c.name for c in (st.channels if st else [])],
        })
    rows.sort(key=lambda r: (-r["qualification"], r["sid"]))
    return rows


def _registry_payload(registry_path: str) -> list[dict]:
    from .registry import load_registry
    out = []
    for sid, st in sorted(load_registry(registry_path).items()):
        out.append({
            "id": st.id,
            "name": st.name,
            "title": st.title,
            "seniority": st.seniority,
            "on_call": st.on_call,
            "expertise": st.expertise,
            "channels": [{"name": c.name, "priority": c.priority, "endpoint": c.endpoint}
                         for c in st.channels],
        })
    return out


def _scenario_list(registry_path: str) -> list[dict]:
    out = []
    for stem, desc in _SCENARIO_META:
        path = _SCENARIOS_DIR / f"{stem}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        alert = data["alert"]
        out.append({
            "name": stem,
            "description": desc,
            "alert": {
                "metric": alert["metric"],
                "value": alert["value"],
                "threshold": alert["threshold"],
                "severity": alert["severity"],
                "domain": alert["domain"],
                "context": alert.get("context", {}),
            },
        })
    return out


def dispatch_scenario(stem: str, registry_path: str, min_reroute_delta: float = 1.5) -> dict:
    path = _SCENARIOS_DIR / f"{stem}.json"
    if not path.exists():
        raise FileNotFoundError(f"unknown scenario: {stem}")
    data = json.loads(path.read_text())
    router, alert_id = run_scenario_data(
        data, registry_path, ":memory:", min_reroute_delta=min_reroute_delta)
    payload = _result_payload(router, alert_id)
    payload["scenario"] = stem
    return payload


def dispatch_custom(alert: dict, registry_path: str, min_reroute_delta: float = 1.5) -> dict:
    # For an unknown domain, route to the most senior on-call stakeholder
    # (the "duty manager") instead of an arbitrary tie-broken candidate.
    duty_manager_ids = []
    try:
        from .registry import load_registry
        roster = load_registry(registry_path)
        on_call = [s for s in roster.values() if s.on_call]
        if on_call:
            top = max(on_call, key=lambda s: (s.seniority, s.id))
            duty_manager_ids = [top.id]
    except Exception:
        pass
    data = {
        "name": "custom",
        "alert": alert,
        "presence": {"online": {}},
        "steps": [],
        "duty_manager_ids": duty_manager_ids,
    }
    router, alert_id = run_scenario_data(
        data, registry_path, ":memory:", min_reroute_delta=min_reroute_delta)
    payload = _result_payload(router, alert_id)
    payload["scenario"] = "custom"
    return payload


def build_handler(registry_path: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args):  # quieter default logging
            pass

        def _send_json(self, obj: dict, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, rel: str) -> None:
            path = (_UI_DIR / rel).resolve()
            if _UI_DIR not in path.parents or not path.is_file():
                self.send_error(404, "not found")
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _MIME.get(path.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send_file("index.html")
            elif path in ("/style.css", "/app.js", "/favicon.svg"):
                self._send_file(path.lstrip("/"))
            elif path == "/api/scenarios":
                self._send_json({"scenarios": _scenario_list(registry_path)})
            elif path == "/api/registry":
                self._send_json({"stakeholders": _registry_payload(registry_path)})
            else:
                self.send_error(404, "not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path != "/api/dispatch":
                self.send_error(404, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send_json({"error": "invalid JSON body"}, status=400)
                return
            with _lock:  # dispatches are cheap + in-memory; serialize to be tidy
                try:
                    if body.get("scenario"):
                        payload = dispatch_scenario(
                            body["scenario"], registry_path,
                            min_reroute_delta=float(body.get("min_reroute_delta", 1.5)))
                    elif body.get("alert"):
                        payload = dispatch_custom(
                            body["alert"], registry_path,
                            min_reroute_delta=float(body.get("min_reroute_delta", 1.5)))
                    else:
                        self._send_json({"error": "send {'scenario': name} or {'alert': {...}}"},
                                        status=400)
                        return
                except FileNotFoundError as exc:
                    self._send_json({"error": str(exc)}, status=404)
                    return
                except KeyError as exc:
                    self._send_json({"error": f"alert missing required field: {exc}"},
                                    status=400)
                    return
            self._send_json(payload)

    return Handler


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Zero-dependency web UI")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--registry", default=str(_REPO_ROOT / "registry.json"))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    handler = build_handler(args.registry)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Alert Routing UI -> http://{args.host}:{args.port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
