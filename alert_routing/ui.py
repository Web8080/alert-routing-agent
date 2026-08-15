# author: Victor Ibhafidon
# date: 2026-08-14
"""Zero-dependency web UI for the alert routing agent (stdlib only).

Serves a single-page dashboard from `alert_routing/static/` and exposes a tiny
JSON API that reuses the exact same router code path as the CLI:

    GET  /api/scenarios                → list of bundled demo scenarios
    POST /api/dispatch                 → run a scenario (or a custom alert) and
                                         return trace + decisions + timeline
    GET  /api/registry                 → stakeholder roster (on-call effective today)
    POST /api/registry                 → add or update a stakeholder
    POST /api/registry/<sid>/on-call   → toggle on-call flag
    DELETE /api/registry/<sid>         → remove a stakeholder
    GET  /api/roster                   → on-call calendar shifts + today's effect
    POST /api/roster                   → add / update a shift
    DELETE /api/roster/<id>            → remove a shift

Usage:
    python -m alert_routing.ui [--port 8000] [--registry registry.json]

Open http://127.0.0.1:8000/ in a browser. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from datetime import date as _date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .cli import run_scenario_data
from .timeline import render_timeline

_UI_DIR = Path(__file__).resolve().parent / "static"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCENARIOS_DIR = _REPO_ROOT / "scenarios"
_ROSTER_PATH = _REPO_ROOT / "roster.json"
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
    a = router.alert
    return {
        "alert_id": alert_id,
        "alert": {
            "metric": a.metric,
            "value": a.value,
            "threshold": a.threshold,
            "severity": a.severity,
            "domain": a.domain,
            "context": a.context,
        },
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


def _effective_registry(registry_path: str, roster_path: str):
    """Stakeholders dict with `on_call` overridden by the roster for today."""
    from .registry import load_registry
    from .roster import effective_on_call, load_shifts

    reg = load_registry(registry_path)
    on_call = effective_on_call(reg, load_shifts(roster_path))
    for sid, st in reg.items():
        if st.on_call != on_call[sid]:
            from dataclasses import replace
            reg[sid] = replace(st, on_call=on_call[sid])
    return reg


def _registry_payload(registry_path: str, roster_path: str) -> list[dict]:
    from . import settings
    from .registry import stakeholder_to_dict
    out = []
    for sid, st in sorted(_effective_registry(registry_path, roster_path).items()):
        item = stakeholder_to_dict(st)
        for ch in item["channels"]:
            ch["webhook_missing"] = (
                ch["name"] == "slack"
                and settings.slack_webhook_for(ch["endpoint"]) is None
            )
        out.append(item)
    return out


def _roster_payload(registry_path: str, roster_path: str) -> dict:
    from .registry import load_registry
    from .roster import covering_shifts, effective_on_call, load_shifts

    reg = load_registry(registry_path)
    today = _date.today().isoformat()
    shifts = load_shifts(roster_path)
    eff = effective_on_call(reg, shifts, today)
    covering = covering_shifts(shifts, today)
    return {
        "today": today,
        "shifts": shifts,
        "covering": [s["id"] for s in covering],
        "effective": eff,
        "on_call_names": [reg[sid].name for sid, on in eff.items() if on],
        "shift_mode": bool(covering),
    }


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


def _on_call_overrides(registry_path: str, roster_path: str) -> dict[str, bool]:
    """Full sid → on_call map (roster-aware) handed to every dispatch."""
    from .registry import load_registry
    from .roster import effective_on_call, load_shifts
    return effective_on_call(load_registry(registry_path), load_shifts(roster_path))


def _next_stakeholder_id(reg: dict) -> str:
    nums = [int(sid[4:]) for sid in reg if sid.startswith("STK-") and sid[4:].isdigit()]
    return f"STK-{max(nums) + 1:03d}" if nums else "STK-001"


def _upsert_stakeholder(registry_path: str, item: dict):
    from .registry import load_registry, parse_stakeholder, save_registry
    reg = load_registry(registry_path)
    if not item.get("id"):
        item = {**item, "id": _next_stakeholder_id(reg)}
    st = parse_stakeholder(item)
    reg[st.id] = st
    save_registry(registry_path, reg)
    return st


def _set_on_call(registry_path: str, sid: str, on: bool):
    from .registry import (load_registry, parse_stakeholder, save_registry,
                           stakeholder_to_dict)
    reg = load_registry(registry_path)
    if sid not in reg:
        raise KeyError(f"no stakeholder {sid}")
    item = {**stakeholder_to_dict(reg[sid]), "on_call": bool(on)}
    reg[sid] = parse_stakeholder(item)
    save_registry(registry_path, reg)
    return reg[sid]


def _delete_stakeholder(registry_path: str, sid: str) -> None:
    from .registry import load_registry, save_registry
    reg = load_registry(registry_path)
    if sid not in reg:
        raise KeyError(f"no stakeholder {sid}")
    del reg[sid]
    save_registry(registry_path, reg)


def _stable_alert_id(alert: dict) -> str:
    """Deterministic id for a custom alert: SHA-1 of the canonicalized payload.

    Distinct alerts get distinct ids (the demo's two incidents must not look like
    the same alert), and identical alerts keep the same id across runs (P5).
    """
    import hashlib
    canonical = json.dumps(alert, sort_keys=True).encode("utf-8")
    return "custom-" + hashlib.sha1(canonical).hexdigest()[:12]


def dispatch_scenario(stem: str, registry_path: str, min_reroute_delta: float = 1.5,
                      on_call_overrides: Optional[dict[str, bool]] = None):
    path = _SCENARIOS_DIR / f"{stem}.json"
    if not path.exists():
        raise FileNotFoundError(f"unknown scenario: {stem}")
    data = json.loads(path.read_text())
    return run_scenario_data(
        data, registry_path, ":memory:", alert_id=f"alert-{stem}",
        min_reroute_delta=min_reroute_delta, on_call_overrides=on_call_overrides)


def dispatch_custom(alert: dict, registry_path: str, min_reroute_delta: float = 1.5,
                    on_call_overrides: Optional[dict[str, bool]] = None):
    # For an unknown domain, route to the most senior on-call stakeholder
    # (the "duty manager") instead of an arbitrary tie-broken candidate.
    duty_manager_ids = []
    try:
        from .registry import load_registry
        roster = load_registry(registry_path)
        on_call = [s for sid, s in roster.items() if not on_call_overrides or on_call_overrides[sid]]
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
    return run_scenario_data(
        data, registry_path, ":memory:", alert_id=_stable_alert_id(alert),
        min_reroute_delta=min_reroute_delta, on_call_overrides=on_call_overrides)


def _summary_payload(router, alert_id: str) -> dict:
    """AI incident summary (Anthropic) with a deterministic fallback."""
    from . import settings
    from .ai import fallback_incident_summary, prose_or_fallback
    from .runbooks import runbook_snippet

    state = router.ledger.plan_state(alert_id)
    final_sid = next((n["stakeholder_id"]
                      for n in router.ledger.notifications_for(alert_id)
                      if n["status"] in ("DELIVERED", "ACKED", "ESCALATED")),
                     None)
    trace = [f"{t.ts} {t.kind} {t.text}" for t in router.trace]
    runbook = runbook_snippet(router.alert)
    summary = prose_or_fallback(
        "summary", router.alert, state, final_sid, trace, runbook,
        fallback=lambda: fallback_incident_summary(router.alert, state, final_sid))
    return {
        "ai_summary": summary,
        "ai_runbook": runbook,
        "ai_enabled": settings.ai_enabled(),
    }


def _save_shift(body: dict, registry_path: str, roster_path: str) -> list[dict]:
    from .registry import load_registry
    from .roster import add_shift, load_shifts, save_shifts, upsert_shift
    shifts = load_shifts(roster_path)
    known = set(load_registry(registry_path))
    if body.get("id"):
        shifts = upsert_shift(shifts, body, known)
    else:
        shifts = add_shift(shifts, body, known)
    save_shifts(roster_path, shifts)
    return shifts


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
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send_file("index.html")
            elif path in ("/style.css", "/app.js", "/favicon.svg"):
                self._send_file(path.lstrip("/"))
            elif path == "/api/scenarios":
                self._send_json({"scenarios": _scenario_list(registry_path)})
            elif path == "/api/registry":
                self._send_json({"stakeholders": _registry_payload(registry_path, _ROSTER_PATH)})
            elif path == "/api/roster":
                self._send_json(_roster_payload(registry_path, _ROSTER_PATH))
            else:
                self.send_error(404, "not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                body = self._read_body()
            except (ValueError, json.JSONDecodeError):
                self._send_json({"error": "invalid JSON body"}, status=400)
                return
            with _lock:
                try:
                    if path == "/api/dispatch":
                        overrides = _on_call_overrides(registry_path, _ROSTER_PATH)
                        if body.get("scenario"):
                            stem = body["scenario"]
                            router, alert_id = dispatch_scenario(
                                stem, registry_path,
                                min_reroute_delta=float(body.get("min_reroute_delta", 1.5)),
                                on_call_overrides=overrides)
                        elif body.get("alert"):
                            stem = "custom"
                            router, alert_id = dispatch_custom(
                                body["alert"], registry_path,
                                min_reroute_delta=float(body.get("min_reroute_delta", 1.5)),
                                on_call_overrides=overrides)
                        else:
                            self._send_json(
                                {"error": "send {'scenario': name} or {'alert': {...}}"},
                                status=400)
                            return
                        payload = _result_payload(router, alert_id)
                        payload["scenario"] = stem
                        if body.get("summary"):
                            payload.update(_summary_payload(router, alert_id))
                        self._send_json(payload)
                        return

                    if path == "/api/registry":
                        st = _upsert_stakeholder(registry_path, body)
                        self._send_json({"ok": True, "stakeholder": st.id})
                        return
                    if path == "/api/roster":
                        shifts = _save_shift(body, registry_path, _ROSTER_PATH)
                        self._send_json({"ok": True, "shifts": shifts})
                        return
                    m = re.match(r"^/api/registry/([^/]+)/on-call$", path)
                    if m:
                        sid = m.group(1)
                        if "on_call" not in body:
                            self._send_json({"error": "missing 'on_call'"}, status=400)
                            return
                        _set_on_call(registry_path, sid, bool(body["on_call"]))
                        self._send_json({"ok": True, "stakeholder": sid})
                        return
                    self._send_json({"error": "not found"}, status=404)
                except FileNotFoundError as exc:
                    self._send_json({"error": str(exc)}, status=404)
                except (KeyError, ValueError) as exc:
                    self._send_json({"error": str(exc)}, status=400)
                except Exception as exc:  # surface backend bugs without killing the server
                    self._send_json({"error": f"internal: {exc}"}, status=500)

        def do_DELETE(self) -> None:
            path = urlparse(self.path).path
            with _lock:
                try:
                    m = re.match(r"^/api/registry/([^/]+)$", path)
                    if m:
                        _delete_stakeholder(registry_path, m.group(1))
                        self._send_json({"ok": True})
                        return
                    m = re.match(r"^/api/roster/([^/]+)$", path)
                    if m:
                        from .roster import load_shifts, remove_shift, save_shifts
                        shifts = remove_shift(load_shifts(_ROSTER_PATH), m.group(1))
                        save_shifts(_ROSTER_PATH, shifts)
                        self._send_json({"ok": True, "shifts": shifts})
                        return
                    self._send_json({"error": "not found"}, status=404)
                except FileNotFoundError as exc:
                    self._send_json({"error": str(exc)}, status=404)
                except (KeyError, ValueError) as exc:
                    self._send_json({"error": str(exc)}, status=400)
                except Exception as exc:
                    self._send_json({"error": f"internal: {exc}"}, status=500)

    return Handler


def main(argv: Optional[list[str]] = None) -> None:
    import os
    parser = argparse.ArgumentParser(description="Zero-dependency web UI")
    # Render injects PORT; local runs default to 8000.
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
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
