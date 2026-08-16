# author: Victor Ibhafidon
# date: 2026-08-16
"""Continuous monitoring: watch every scenario feed and auto-submit breaches to
the deterministic routing agent.

Design contract (defensible in code review):
  * EVERY threshold breach is always submitted to the deterministic router — the
    AI watcher can never suppress or alter a dispatch (P5 determinism is
    untouched). The AI's role is advisory: a one-line watcher note describing
    what the telemetry showed, written AFTER the routing decision.
  * Feed selection order is deterministic (severity first, then deviation), so
    the same ticks always produce the same activity stream.
  * Feeds are derived from the bundled scenario files, so "all scenarios" stays
    in lockstep with what the dashboard ships.

The dashboard's Monitor view drives this: each tick advances every feed's value,
and any feed that breaches fires its full scripted scenario through the router
into ONE shared durable ledger (all activity is queryable via the ledger API).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .cli import run_scenario_data
from .roster import effective_on_call, load_shifts

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
_COOLDOWN = 6  # ticks a feed must wait before firing the same breach again

# Deterministic telemetry profile per metric. "below" metrics fall toward the
# threshold (value <= threshold breaches), "above" metrics rise toward it.
_METRIC_PROFILE = {
    "stock_level":       {"slope": -1.8, "direction": "below"},
    "contract_expiry":   {"slope": -2.1, "direction": "below"},
    "sla_response_time": {"slope": 25.0, "direction": "above"},
    "anomaly_score":     {"slope": 0.06, "direction": "above"},
}
_DEFAULT_PROFILE = {"slope": 1.5, "direction": "above"}
_SEVERITY_WEIGHT = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}


@dataclass
class Feed:
    stem: str
    metric: str
    threshold: float
    severity: str
    domain: str
    context: dict
    direction: str
    slope: float
    value: float
    breaches: int = 0
    breached: bool = False
    cooldown: int = 0

    def advance(self) -> bool:
        """Advance the feed one tick; return True if it fires this tick.

        A feed fires (returns True) only when it breaches AND its cooldown has
        elapsed, so the same feed does not spam dispatches every tick."""
        self.value = round(self.value + self.slope, 3)
        if self.cooldown > 0:
            self.cooldown -= 1
        self.breached = (
            self.value <= self.threshold if self.direction == "below"
            else self.value >= self.threshold)
        if self.breached and self.cooldown == 0:
            self.breaches += 1
            self.cooldown = _COOLDOWN
            return True
        return False

    def deviation(self) -> float:
        return abs(self.value - self.threshold) / max(abs(self.threshold), 1e-9)

    def to_dict(self) -> dict:
        return {
            "stem": self.stem, "metric": self.metric, "value": self.value,
            "threshold": self.threshold, "severity": self.severity,
            "domain": self.domain, "breached": self.breached,
            "breaches": self.breaches,
        }


@dataclass
class DispatchRecord:
    seq: int
    stem: str
    alert_id: str
    metric: str
    value: float
    threshold: float
    severity: str
    domain: str
    plan_state: str
    recipient: Optional[str]
    rule: Optional[str]
    note: str
    ai_enabled: bool

    def to_dict(self) -> dict:
        return {
            "seq": self.seq, "stem": self.stem, "alert_id": self.alert_id,
            "metric": self.metric, "value": self.value,
            "threshold": self.threshold, "severity": self.severity,
            "domain": self.domain, "plan_state": self.plan_state,
            "recipient": self.recipient, "rule": self.rule, "note": self.note,
            "ai_enabled": self.ai_enabled,
        }


def _scenario_stems() -> list[str]:
    return sorted(p.stem for p in _SCENARIOS_DIR.glob("scenario_*.json"))


def _feed_for_stem(stem: str) -> Feed:
    data = json.loads((_SCENARIOS_DIR / f"{stem}.json").read_text())
    a = data["alert"]
    profile = _METRIC_PROFILE.get(a["metric"], _DEFAULT_PROFILE)
    direction = profile["direction"]
    slope = profile["slope"]
    threshold = float(a["threshold"])
    # Start near the threshold (3 ticks of drift from it) so the watcher's
    # first breach lands a few seconds after the dashboard opens — a live demo
    # should show action, not a 30-second build-up.
    gap = slope * 3
    value = (threshold + gap if direction == "below"
             else threshold - gap)
    return Feed(stem=stem, metric=a["metric"], threshold=threshold,
                severity=a["severity"], domain=a["domain"],
                context=a.get("context", {}), direction=direction,
                slope=slope, value=value)


class AutoMonitor:
    """Drives all bundled scenario feeds and submits breaches to the router.

    Every dispatch lands in ONE shared ledger (durable temp file by default) so
    the activity stream and the ledger stay consistent.
    """

    def __init__(self, store, roster_path: str, ledger_path: Optional[str] = None,
                 min_reroute_delta: float = 1.5, tick_sec: float = 1.0):
        self.store = store
        self.roster_path = roster_path
        self.ledger_path = ledger_path or self._temp_ledger()
        self.min_reroute_delta = min_reroute_delta
        self.tick_sec = tick_sec
        self.seq = 0
        self.feeds = [_feed_for_stem(stem) for stem in _scenario_stems()]
        self.records: list[DispatchRecord] = []

    @staticmethod
    def _temp_ledger() -> str:
        fh = tempfile.NamedTemporaryFile(prefix="alert_monitor_", suffix=".db",
                                         delete=False)
        fh.close()
        return fh.name

    def feed_payload(self) -> list[dict]:
        return [f.to_dict() for f in self.feeds]

    def select(self, breached: list[Feed]) -> list[Feed]:
        """Deterministic submission order: severity first, then deviation."""
        return sorted(breached, key=lambda f: (
            -_SEVERITY_WEIGHT.get(f.severity, 1), -f.deviation(), f.stem))

    def tick(self) -> list[dict]:
        """Advance every feed one tick; submit each new breach to the router.

        Returns the dispatch records fired THIS tick (empty when nothing
        breached). Roster-aware on-call overrides are refreshed per tick."""
        reg = self.store.load()
        overrides = effective_on_call(reg, load_shifts(self.roster_path))
        fired = [f for f in self.feeds if f.advance()]
        if not fired:
            return []
        results = []
        for feed in self.select(fired):
            self.seq += 1
            aid = f"mon-{feed.stem}-{self.seq}"
            data = json.loads(
                (_SCENARIOS_DIR / f"{feed.stem}.json").read_text())
            router, _ = run_scenario_data(
                data, self.store.json_path, self.ledger_path, alert_id=aid,
                min_reroute_delta=self.min_reroute_delta,
                on_call_overrides=overrides, stakeholders=reg)
            record = self._record(router, feed, aid)
            self.records.append(record)
            results.append(record.to_dict())
        return results

    def _record(self, router, feed: Feed, aid: str) -> DispatchRecord:
        from . import settings
        from .ai import fallback_monitor_note, prose_or_fallback

        notifs = router.ledger.notifications_for(aid)
        final = next((n for n in notifs
                      if n["status"] in ("DELIVERED", "ACKED", "ESCALATED")),
                     notifs[0] if notifs else None)
        decisions = router.ledger.decision_log(aid)
        rule = decisions[-1]["code"] if decisions else None
        recipient = f"{final['stakeholder_name']} ({final['stakeholder_id']})" \
            if final else None
        summary_line = (f"{feed.metric} {feed.severity} breach observed "
                        f"{feed.value} (threshold {feed.threshold}) in "
                        f"{feed.domain} -> deterministic router "
                        f"{router.ledger.plan_state(aid)}"
                        + (f" via {rule}" if rule else "")
                        + (f", final recipient {final['stakeholder_name']}"
                           if final else ", unresolved"))
        note = prose_or_fallback(
            "monitor", summary_line,
            fallback=lambda: fallback_monitor_note(summary_line))
        return DispatchRecord(
            seq=self.seq, stem=feed.stem, alert_id=aid, metric=feed.metric,
            value=feed.value, threshold=feed.threshold, severity=feed.severity,
            domain=feed.domain, plan_state=router.ledger.plan_state(aid),
            recipient=recipient, rule=rule, note=note,
            ai_enabled=settings.ai_enabled())


def build_monitor(store, roster_path: str, ledger_path: Optional[str] = None,
                  min_reroute_delta: float = 1.5) -> AutoMonitor:
    return AutoMonitor(store, roster_path, ledger_path=ledger_path,
                       min_reroute_delta=min_reroute_delta)
