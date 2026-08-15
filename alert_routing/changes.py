# author: Victor Ibhafidon
# date: 2026-08-14
"""Change detector: turns raw events into diffs against the frozen snapshot.

It reads the snapshot table and the event payload only — never a live presence
query. A stakeholder can only ever be availability-evaluated once, at snapshot
time; every event after that is a *diff*, not a re-query.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum

from .models import ChangeNotice, SnapshotEntry


class ChangeType(str, Enum):
    RECIPIENT_OFFLINE = "recipient_offline"
    CANDIDATE_AVAILABLE = "candidate_available"
    CHANNEL_FAILED = "channel_failed"
    NO_CHANGE = "no_change"


@dataclass(frozen=True)
class DetectedChange:
    ctype: ChangeType
    stakeholder_id: str
    channel: str | None = None
    online: bool = True
    snapshot: SnapshotEntry | None = None


def detect(
    notice: ChangeNotice,
    snapshots: dict[str, SnapshotEntry],
) -> DetectedChange:
    sid = notice.stakeholder_id
    snap = snapshots.get(sid)

    if notice.event == "channel.failed":
        if snap is None:
            return DetectedChange(ChangeType.NO_CHANGE, sid, channel=notice.payload.get("channel"))
        channel = notice.payload.get("channel")
        state = notice.payload.get("state")
        if state in ("DOWN", "DEGRADED"):
            return DetectedChange(ChangeType.CHANNEL_FAILED, sid, channel=channel, snapshot=snap)
        return DetectedChange(ChangeType.NO_CHANGE, sid, channel=channel, snapshot=snap)

    # presence.changed / candidate.available
    online = bool(notice.payload.get("online", True))
    if snap is None:
        # Never evaluated → we have no frozen state. The event carries the truth;
        # a "now available" candidate is the only actionable case.
        if online and notice.event == "candidate.available":
            return DetectedChange(ChangeType.CANDIDATE_AVAILABLE, sid, online=True)
        return DetectedChange(ChangeType.NO_CHANGE, sid, online=online)

    if snap.online and not online:
        return DetectedChange(ChangeType.RECIPIENT_OFFLINE, sid, online=False, snapshot=snap)
    if not snap.online and online:
        return DetectedChange(ChangeType.CANDIDATE_AVAILABLE, sid, online=True, snapshot=snap)
    return DetectedChange(ChangeType.NO_CHANGE, sid, online=online, snapshot=snap)
