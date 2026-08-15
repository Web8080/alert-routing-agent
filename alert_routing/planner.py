# author: Victor Ibhafidon
# date: 2026-08-14
"""Planner: builds the immutable dispatch route from the frozen snapshot.

The route is built ONCE. Re-planning moves only a cursor — it never rebuilds the
route — because a rebuilt route would re-rank against current availability and
reopen the availability-first downgrade bug.
"""

from __future__ import annotations

from typing import Optional

from .models import ChannelState, Config, Plan, RouteStep, SnapshotEntry, Stakeholder


def _channel_order(
    stakeholder: Stakeholder,
    snap: Optional[SnapshotEntry],
) -> list[str]:
    """Preference order (priority asc) filtered to channels that were healthy
    at snapshot time. Falls back to all channels if no snapshot is present."""
    if snap is not None:
        healthy = {c for c, s in snap.channel_health.items() if s == ChannelState.OK}
        if healthy:
            return [p.name for p in sorted(stakeholder.channels, key=lambda p: p.priority)
                    if p.name in healthy]
    return [p.name for p in sorted(stakeholder.channels, key=lambda p: p.priority)]


def build_plan(
    alert_id: str,
    severity: str,
    stakeholders: dict[str, Stakeholder],
    snapshots: dict[str, SnapshotEntry],
    config: Config,
) -> Plan:
    """Route = gated-in candidates in frozen qualification order."""
    gated_in = [
        sid for sid, snap in snapshots.items()
        if not snap.gated
    ]
    gated_in.sort(key=lambda sid: snapshots[sid].qualification, reverse=True)
    route = [
        RouteStep(
            stakeholder_id=sid,
            name=snapshots[sid].name,
            qualification=snapshots[sid].qualification,
            channel_order=_channel_order(stakeholders[sid], snapshots[sid]),
        )
        for sid in gated_in
    ]
    return Plan(alert_id=alert_id, severity=severity, route=route,
                escalation_cap=config.escalation_cap)
