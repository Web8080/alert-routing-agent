# author: Victor Ibhafidon
# date: 2026-08-14
"""CLI + scripted scenario driver.

Usage:
    python -m alert_routing.cli scenarios/scenario_1_offline.json
    python -m alert_routing.cli scenarios/scenario_1_offline.json --ledger /tmp/ledger.db

A scenario is a JSON file describing an alert, initial presence state, and an
ordered sequence of steps (events / acks). The driver advances a simulated
clock between steps so the output is fully deterministic and reproducible.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from .ledger import Ledger
from .models import Alert, Config
from .presence import Presence
from .router import Router, SimClock
from .timeline import render_timeline
from . import settings
from .ai import make_notification_prose


def _step_ops(presence: Presence, router: Router, step: dict) -> None:
    op = step["op"]
    if op == "set_online":
        presence.set_online(step["sid"], bool(step["online"]))
    elif op == "set_channel_health":
        presence.set_channel_health(step["sid"], step["channel"], step["state"])
    elif op == "ack":
        router.acknowledge()
    elif op == "ack_timeout":
        router.evaluate_ack_timeout()
    elif op == "retry_advance":
        router.acknowledge()
    else:
        raise ValueError(f"unknown scenario step op: {op}")


def run_scenario(
    scenario_path: str,
    registry_path: str,
    ledger_path: str = ":memory:",
    alert_id: Optional[str] = None,
    min_reroute_delta: float = 1.5,
    prose=None,
) -> tuple[Router, str]:
    data = json.loads(Path(scenario_path).read_text())
    stem = Path(scenario_path).stem
    aid = alert_id or data["alert"].get("alert_id") or f"alert-{stem}"
    return run_scenario_data(
        data, registry_path, ledger_path,
        alert_id=aid, min_reroute_delta=min_reroute_delta, prose=prose)


def run_scenario_data(
    data: dict,
    registry_path: str,
    ledger_path: str = ":memory:",
    alert_id: Optional[str] = None,
    min_reroute_delta: float = 1.5,
    prose=None,
    on_call_overrides: Optional[dict[str, bool]] = None,
) -> tuple[Router, str]:
    """Drive the router through a scenario *dict* (scenario JSON schema).

    Shared by the CLI (file → dict) and the web UI (API → dict) so both entry
    points exercise the exact same code path."""
    presence = Presence()
    presence.seed(data.get("presence", {}).get("online", {}),
                  data.get("presence", {}).get("health", {}))
    clock = SimClock()
    config = Config(min_reroute_delta=min_reroute_delta,
                    duty_manager_ids=tuple(data.get("duty_manager_ids", [])))
    router = Router(registry_path, ledger_path, config=config, clock=clock,
                    presence=presence, prose=prose,
                    on_call_overrides=on_call_overrides)

    a = data["alert"]
    aid = alert_id or a.get("alert_id") or "alert-dispatch"
    alert = Alert(alert_id=aid, metric=a["metric"], value=a["value"],
                  threshold=a["threshold"], severity=a["severity"],
                  domain=a["domain"], context=a.get("context", {}), ts=clock.now())

    router.dispatch(alert)
    for step in data.get("steps", []):
        clock.advance(0.5)
        _step_ops(presence, router, step)
    # Drain: acknowledge any in-flight send and finalize the plan state.
    router.close()
    return router, aid


def print_trace(router: Router) -> None:
    for line in router.trace:
        print(f"{line.ts} {line.kind.upper():7s} {line.text}")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Alert routing agent demo")
    parser.add_argument("scenario", help="path to scenario JSON")
    parser.add_argument("--registry", default="registry.json")
    parser.add_argument("--ledger", default=None,
                        help="SQLite path (default: a durable temp file that survives "
                             "a crash; pass a path for cross-run persistence)")
    parser.add_argument("--alert-id", default=None)
    parser.add_argument("--min-reroute-delta", type=float, default=1.5)
    parser.add_argument("--timeline", action="store_true", default=True,
                        help="render the incident timeline at the end")
    parser.add_argument("--summary", action="store_true", default=False,
                        help="print an AI (or fallback) incident summary at the end")
    args = parser.parse_args(argv)

    # Durable-by-default ledger: a fresh temp FILE (not :memory:) so a crash
    # cannot lose the alert — the file survives and the timeline can be
    # re-rendered from it. Explicit --ledger PATH gives cross-run persistence.
    # The path is logged to STDERR so stdout stays byte-identical across runs
    # (P5 determinism check diffs stdout under different PYTHONHASHSEED values).
    import sys
    ledger_path = args.ledger
    if ledger_path is None:
        import tempfile
        fd, ledger_path = tempfile.mkstemp(prefix="alert_ledger_", suffix=".db")
        import os
        os.close(fd)
        print(f"[ledger] durable temp ledger: {ledger_path}", file=sys.stderr)
        print("[ledger] re-render it anytime with: "
              f"python3 -m alert_routing.cli {args.scenario} --ledger {ledger_path} "
              f"--alert-id alert-{Path(args.scenario).stem} --timeline",
              file=sys.stderr)

    router, aid = run_scenario(
        args.scenario, args.registry, ledger_path,
        alert_id=args.alert_id, min_reroute_delta=args.min_reroute_delta,
        prose=make_notification_prose())
    print_trace(router)
    print()
    if args.timeline:
        print(render_timeline(router.ledger, aid))
    if args.summary:
        from .ai import prose_or_fallback, fallback_incident_summary
        from .runbooks import runbook_snippet
        final_sid = next(
            (n["stakeholder_id"] for n in router.ledger.notifications_for(aid)
             if n["status"] in ("DELIVERED", "ESCALATED")), None)
        state = (router.plan.state.value if router.plan else "unknown")
        runbook = runbook_snippet(router.alert)
        summary = prose_or_fallback(
            "summary", router.alert, state, final_sid,
            [f"{t.ts} {t.kind} {t.text}" for t in router.trace], runbook,
            fallback=lambda: fallback_incident_summary(
                router.alert, state, final_sid))
        print()
        print(f"INCIDENT SUMMARY: {summary}")
        if runbook and settings.ai_enabled() is False:
            print(f"RUNBOOK RETRIEVED: {runbook.splitlines()[0]}")


if __name__ == "__main__":
    main()
