# author: Victor Ibhafidon
# date: 2026-08-14
"""Core data model for the alert routing agent.

Every routing decision is made against these immutable records. Availability is
captured once (SnapshotEntry) and frozen; the decision policy never re-reads it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
CHANNELS = ("email", "slack", "sms")


class ChannelState(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class DeliveryReceipt(str, Enum):
    ACKED = "ACKED"          # delivery confirmed terminal
    FAILED = "FAILED"        # permanent failure (bad endpoint, hard reject)
    RETRIABLE = "RETRIABLE"  # transient failure (provider down, timeout)


class PlanState(str, Enum):
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    DELIVERED = "DELIVERED"
    ESCALATED = "ESCALATED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


class NotificationStatus(str, Enum):
    INTENT = "INTENT"      # claimed, about to be / being sent
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ESCALATED = "ESCALATED"


@dataclass(frozen=True)
class ChannelPref:
    name: str              # "slack" | "email" | "sms"
    priority: int          # 1 = most preferred
    endpoint: str          # opaque; only adapters interpret it


@dataclass(frozen=True)
class Stakeholder:
    id: str
    name: str
    title: str
    seniority: int                      # 1 (IC) .. 5 (lead)
    expertise: dict[str, int]           # metric-domain -> proficiency 1..5
    on_call: bool
    channels: list[ChannelPref]         # ordered by priority


@dataclass(frozen=True)
class Alert:
    alert_id: str
    metric: str
    value: float
    threshold: float
    severity: str
    domain: str
    context: dict[str, Any]
    ts: str


@dataclass(frozen=True)
class SnapshotEntry:
    stakeholder_id: str
    name: str
    qualification: float
    online: bool
    channel_health: dict[str, ChannelState]
    gated: bool            # True if filtered out by availability / on-call / no channel
    eval_ts: str


@dataclass
class RouteStep:
    stakeholder_id: str
    name: str
    qualification: float
    channel_order: list[str]            # preference order filtered to snapshot-healthy
    channel_index: int = 0


@dataclass
class Plan:
    alert_id: str
    severity: str
    route: list[RouteStep]
    escalation_cap: int
    state: PlanState = PlanState.QUEUED
    level: int = 0                      # current escalation level (0 = primary)
    step_index: int = 0                 # cursor into route

    def current_step(self) -> Optional[RouteStep]:
        if not self.route:
            return None
        if self.step_index >= len(self.route):
            return None
        return self.route[self.step_index]


@dataclass
class Notification:
    notification_id: str
    alert_id: str
    stakeholder_id: str
    stakeholder_name: str
    channel: str
    status: NotificationStatus
    escalation_level: int
    body: str
    endpoint: str = ""        # opaque delivery target (email addr / Slack channel); adapters interpret it


@dataclass(frozen=True)
class ChangeNotice:
    event: str                    # "presence.changed" | "channel.failed" | "candidate.available"
    stakeholder_id: str
    payload: dict[str, Any]


@dataclass
class Verdict:
    action: str                   # COMPLETE|REROUTE|ESCALATE_PARALLEL|ABORT|RETRY_CHANNEL|IGNORE
    target: Optional[str] = None
    channel: Optional[str] = None
    decision_code: str = ""
    rationale: str = ""


@dataclass
class Config:
    min_reroute_delta: float = 1.5
    escalation_cap: int = 3
    ack_window: float = 30.0
    duty_manager_ids: tuple[str, ...] = ()
    high_severity_acks: bool = True    # arm ack timers for HIGH/CRITICAL


@dataclass
class LedgerView:
    """Frozen view of ledger state handed to the decision policy (no I/O inside)."""

    delivered_sids: frozenset[str] = frozenset()
    current_sid: Optional[str] = None
    current_channel: Optional[str] = None
    current_acked: bool = False
    notified_sids: frozenset[str] = frozenset()
    attempted_sids: frozenset[str] = frozenset()


@dataclass
class TraceLine:
    ts: str
    kind: str                     # ingress|rank|plan|send|event|policy|ledger|notify|summary
    text: str


def make_notification_id(alert_id: str, sid: str, channel: str, level: int) -> str:
    return f"{alert_id}:{sid}:{channel}:l{level}"
