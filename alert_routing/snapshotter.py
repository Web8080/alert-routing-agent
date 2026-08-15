# author: Victor Ibhafidon
# date: 2026-08-14
"""One-time availability snapshotting.

The snapshotter is the ONLY component that reads time-varying presence/channel
state. It runs once per dispatch and writes one row per candidate. The ledger's
PRIMARY KEY (alert_id, stakeholder_id) physically forbids a second evaluation.
"""

from __future__ import annotations

from .ledger import Ledger, SnapshotAlreadyExists
from .models import Alert, ChannelState, SnapshotEntry, Stakeholder
from .presence import Presence
from .ranker import qualification


def snapshot(
    alert: Alert,
    ranked: list[tuple[Stakeholder, float]],
    presence: Presence,
    ledger: Ledger,
    eval_ts: str,
) -> list[SnapshotEntry]:
    entries: list[SnapshotEntry] = []
    for stakeholder, q in ranked:
        online = presence.online(stakeholder.id)
        health = presence.channel_health(stakeholder.id)
        has_healthy = any(state == ChannelState.OK for state in health.values())
        gated = not (stakeholder.on_call and online and has_healthy)
        entry = SnapshotEntry(
            stakeholder_id=stakeholder.id,
            name=stakeholder.name,
            qualification=q,
            online=online,
            channel_health=health,
            gated=gated,
            eval_ts=eval_ts,
        )
        # Single-evaluation guarantee: a duplicate write for this (alert, sid)
        # is physically rejected by the schema and surfaces as an exception.
        ledger.insert_snapshot(alert.alert_id, entry)
        entries.append(entry)
    return entries


def require_no_reevaluation(entries: list[SnapshotEntry], ledger: Ledger, alert_id: str) -> None:
    """Assert the invariant directly (used by tests to prove single-eval)."""
    for e in entries:
        try:
            ledger.insert_snapshot(alert_id, e)
        except SnapshotAlreadyExists:
            pass  # expected — already evaluated
        else:
            raise AssertionError(f"{e.stakeholder_id} evaluated twice without schema rejection")
