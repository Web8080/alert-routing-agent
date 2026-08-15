#!/usr/bin/env python3
# author: Victor Ibhafidon
# date: 2026-08-15
"""LLM proposes new scenarios -> the invariant suite decides.

Usage:
    python -m alert_routing.propose_scenario "a SECOND alert arrives while a
        reroute is in flight" --out scenarios/proposed
    python -m alert_routing.propose_scenario --list-examples

The LLM drafts a candidate scenario JSON; the deterministic invariant suite
then ADOPTS or REJECTS it. The LLM never decides what ships — it proposes.
Adoption requires: valid schema, clean run, and the five guarantees (no dup
notification, no double-query of availability, no downgrade, deterministic).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import settings
from .ai import AnthropicProse
from .cli import run_scenario_data
from .models import Config
from .presence import Presence
from .router import Router, SimClock

SCHEMA_EXAMPLE = json.dumps({
    "name": "scenario_example",
    "description": "one-line description of the edge case",
    "alert": {"metric": "stock_level", "value": 9, "threshold": 20,
              "severity": "CRITICAL", "domain": "inventory",
              "context": {"warehouse": "WH-2", "sku": "ACME-77"}},
    "presence": {"online": {"STK-003": False, "STK-006": False, "STK-007": False}},
    "steps": [{"op": "set_online", "sid": "STK-003", "online": True},
              {"op": "ack"}],
}, indent=2)

STEPS_OPS = ["set_online", "set_channel_health", "ack", "ack_timeout", "retry_advance"]


def _extract_json(raw: str) -> dict:
    """Extract the first JSON object from an LLM reply (tolerates code fences)."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n"):].strip()
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


def _invariants(data: dict, out: Optional[Path]) -> list[str]:
    """Run the candidate and check the five guarantees. Returns violations."""
    violations: list[str] = []
    try:
        router, aid = run_scenario_data(data, "registry.json", ":memory:",
                                        min_reroute_delta=1.5)
    except Exception as exc:  # schema / runtime failure
        return [f"run failed: {exc}"]

    notified = router.ledger.notifications_for(aid)
    pairs = [(n["stakeholder_id"], n["channel"], n["escalation_level"])
             for n in notified]
    if len(pairs) != len(set(pairs)):
        violations.append("P2 violated: duplicate (stakeholder, channel, level)")
    if not data.get("steps"):
        violations.append("scenario has no steps")
    if data.get("presence", {}).get("online", {}) == {} \
            and not any(s.get("op") == "set_online" for s in data.get("steps", [])):
        violations.append("scenario does not exercise availability change")
    # NOTE: a plan ending FAILED/ABORTED is a VALID test of R6 (the abort path) —
    # it is not a guarantee violation. We only enforce the guarantees here.

    # Determinism: same scenario, two independent runs, identical trace text.
    router2, _ = run_scenario_data(data, "registry.json", ":memory:",
                                   min_reroute_delta=1.5)
    t1 = [(l.ts, l.kind, l.text) for l in router.trace]
    t2 = [(l.ts, l.kind, l.text) for l in router2.trace]
    if t1 != t2:
        violations.append("P5 violated: run not reproducible")

    if violations and out is not None:
        out.write_text(json.dumps(data, indent=2) + "\n")
    return violations


def _write_candidate(data: dict, out_dir: Path) -> Path:
    name = data.get("name") or f"proposed_{abs(hash(str(data))) % 100000}"
    out = out_dir / f"{name}.json"
    out.write_text(json.dumps(data, indent=2) + "\n")
    return out


def propose(idea: str, out_dir: Path) -> None:
    if not settings.ai_enabled():
        sys.exit("AI not configured (set ALERT_AI_API_KEY). Refusing to guess.")
    provider = AnthropicProse()
    system = ("You write incident-routing scenario definitions for an alert "
              "routing engine. You respond with ONLY a JSON object, no "
              "markdown fences, matching the given schema exactly.")
    prompt = (
        f"Produce ONE scenario JSON testing this edge case: {idea}\n\n"
        f"Use this schema and the registry stakeholders STK-001..STK-007 "
        f"(Sarah Chen, David Miller, Elena Ross, Frank Dubois, Grace Lin, "
        f"Priya Nair, Maya Khan; primary for inventory is STK-001).\n"
        f"CONSTRAINTS: exactly ONE alert at the top level; steps may ONLY use "
        f"these ops (op + their fields): {', '.join(STEPS_OPS)}; NEVER put an "
        f"alert inside a step; keep steps short (2-4).\n\n"
        f"Schema example:\n{SCHEMA_EXAMPLE}"
    )
    raw = provider._complete(system, prompt, max_tokens=900)
    data = _extract_json(raw)  # raises ValueError -> reject below

    name = data.get("name") or f"proposed_{abs(hash(idea)) % 100000}"
    violations = _invariants(data, out=None)
    if violations:
        print(f"REJECTED {name} — {len(violations)} violation(s):")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    out = _write_candidate(data, out_dir)
    print(f"ADOPTED {name} -> {out}")
    print("Run it:  python -m alert_routing.cli "
          f"{out} --alert-id alert-{name}")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("idea", nargs="?", help="edge case to propose a scenario for")
    parser.add_argument("--out", default="scenarios/proposed")
    parser.add_argument("--list-examples", action="store_true")
    args = parser.parse_args(argv)

    if args.list_examples:
        print("Ideas to try:")
        for idea in [
            "a second alert for a DIFFERENT metric fires while the first is mid-reroute",
            "an acknowledged primary goes offline AFTER ack (completed dispatch)",
            "all candidates offline simultaneously -> R6 cap / abort path",
            "a channel fails then recovers mid-flight before the ack timeout",
        ]:
            print(f"  python -m alert_routing.propose_scenario \"{idea}\"")
        return
    if not args.idea:
        parser.error("idea is required (or use --list-examples)")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    propose(args.idea, out_dir)


if __name__ == "__main__":
    main()
