# author: Victor Ibhafidon
# date: 2026-08-14
"""Decision policy: rules R1-R6 + MIN_REROUTE_DELTA.

Pure function: (plan, snapshots, ledger view, change) -> Verdict. No I/O.
Every verdict carries a stable decision code and a template-composed rationale
that also becomes the 'why you were chosen' text on the delivered notification.

R1  channel fallback       transport failed, same recipient, next channel
R2  abort + reroute        recipient offline, nothing acked -> next backup
R3  complete + escalate    recipient offline, delivery acked -> parallel escalate
R4a reroute to better      better-qualified candidate (delta >= MIN_REROUTE_DELTA),
                           nothing acked
R4b escalate to better     same, but already acked
R4c ack-timeout escalate   HIGH/CRITICAL, no ack within window
R5  ignore downgrade       candidate qualifies <= current + delta -> IGNORE
R6  abort / unresolved     no viable target remains
"""

from __future__ import annotations

from typing import Optional

from .changes import ChangeType, DetectedChange
from .models import (
    Alert,
    Config,
    LedgerView,
    Plan,
    RouteStep,
    SnapshotEntry,
    Stakeholder,
    Verdict,
)

HIGH_SEVERITY = ("HIGH", "CRITICAL")


class UnhandledSituationError(RuntimeError):
    pass


def _current_qualification(view: LedgerView, snapshots: dict[str, SnapshotEntry]) -> float:
    if not view.current_sid:
        return 0.0
    snap = snapshots.get(view.current_sid)
    return snap.qualification if snap else 0.0


def _next_backup(
    plan: Plan,
    snapshots: dict[str, SnapshotEntry],
    view: LedgerView,
    start_after: Optional[str] = None,
) -> Optional[RouteStep]:
    """Next un-notified, gated-in, online-at-snapshot backup in route order."""
    for step in plan.route:
        if start_after is not None and step.stakeholder_id == start_after:
            start_after = None
            continue
        if start_after is not None:
            continue
        if step.stakeholder_id in view.notified_sids:
            continue
        snap = snapshots.get(step.stakeholder_id)
        if snap is None or snap.gated or not snap.online:
            continue
        if not step.channel_order:
            continue
        return step
    return None


def _candidate_qualification(
    sid: str,
    snapshots: dict[str, SnapshotEntry],
    stakeholders: dict[str, Stakeholder],
    alert: Alert,
) -> float:
    """Frozen snapshot score; falls back to registry (a config read, NOT a
    presence query) for stakeholders never evaluated."""
    snap = snapshots.get(sid)
    if snap is not None:
        return snap.qualification
    s = stakeholders.get(sid)
    if s is None:
        return 0.0
    return s.expertise.get(alert.domain, 0) * (1.0 + (s.seniority - 1) * 0.15)


def decide(
    alert: Alert,
    plan: Plan,
    snapshots: dict[str, SnapshotEntry],
    stakeholders: dict[str, Stakeholder],
    view: LedgerView,
    change: Optional[DetectedChange],
    config: Config,
    ack_timeout: bool = False,
) -> Verdict:
    if plan.state.value in ("DELIVERED", "ESCALATED", "ABORTED", "FAILED"):
        return Verdict(action="COMPLETE", decision_code="TERMINAL",
                       rationale="Plan already in terminal state.")

    current_q = _current_qualification(view, snapshots)
    level = plan.level

    # ---------------------------------------------------------------- ack timeout
    if ack_timeout:
        if view.current_acked:
            return Verdict(action="COMPLETE", decision_code="R4C_ACKED",
                           rationale="Ack timeout fired but delivery was already acknowledged.")
        if alert.severity not in HIGH_SEVERITY:
            return Verdict(action="COMPLETE", decision_code="R4C_LOW_SEVERITY",
                           rationale="Ack timeout only applies to HIGH/CRITICAL alerts.")
        if level + 1 >= plan.escalation_cap:
            return Verdict(action="ABORT", decision_code="R6_CAP",
                           rationale=f"Escalation cap ({plan.escalation_cap}) reached; aborting.")
        backup = _next_backup(plan, snapshots, view)
        target = backup.stakeholder_id if backup else None
        if target is None:
            for dm in config.duty_manager_ids:
                if dm not in view.notified_sids:
                    target = dm
                    break
        if target is None:
            return Verdict(action="ABORT", decision_code="R6_UNRESOLVED",
                           rationale="No target available for ack-timeout escalation; alert unresolved.")
        return Verdict(
            action="ESCALATE_PARALLEL", target=target, decision_code="R4C_TIMEOUT",
            rationale=(f"High-severity alert not acknowledged within window; "
                       f"escalating to {target}."))

    if change is None:
        return Verdict(action="COMPLETE", decision_code="CONTINUE",
                       rationale="No change; continue current dispatch.")

    sid = change.stakeholder_id
    snap = change.snapshot

    # ------------------------------------------------------------- channel failed (R1)
    if change.ctype == ChangeType.CHANNEL_FAILED:
        if sid != view.current_sid:
            return Verdict(action="IGNORE", decision_code="R5_CHANNEL_OTHER",
                           rationale=f"Channel failure for non-recipient {sid}; ignoring.")
        if view.current_acked:
            return Verdict(action="COMPLETE", decision_code="R1_ACKED",
                           rationale="Channel failed after delivery acknowledged; nothing to do.")
        current_step = plan.current_step() or plan.route[0]
        idx = current_step.channel_index
        for ch in current_step.channel_order[idx + 1:]:
            return Verdict(
                action="RETRY_CHANNEL", target=view.current_sid, channel=ch,
                decision_code="R1_RETRY",
                rationale=(f"Channel {change.channel} failed for {view.current_sid}; "
                           f"retrying same recipient via {ch}."))
        backup = _next_backup(plan, snapshots, view)
        if backup is None:
            return Verdict(action="ABORT", decision_code="R6_EXHAUSTED",
                           rationale="No channels left for recipient and no backup; aborting.")
        return Verdict(
            action="REROUTE", target=backup.stakeholder_id, decision_code="R2_NO_CHANNEL",
            rationale=(f"All channels failed for {view.current_sid}; "
                       f"rerouting to next-ranked backup {backup.stakeholder_id}."))

    # ------------------------------------------------------- recipient offline (R2/R3)
    if change.ctype == ChangeType.RECIPIENT_OFFLINE:
        if sid != view.current_sid:
            return Verdict(action="IGNORE", decision_code="R5_OTHER_OFFLINE",
                           rationale=f"Stakeholder {sid} offline; not the current recipient; ignoring.")
        if view.current_acked:
            backup = _next_backup(plan, snapshots, view)
            if backup is None:
                return Verdict(action="COMPLETE", decision_code="R3_NO_BACKUP",
                               rationale="Recipient offline but delivery acked; no backup to escalate to.")
            return Verdict(
                action="ESCALATE_PARALLEL", target=backup.stakeholder_id,
                decision_code="R3_ESCALATE",
                rationale=(f"Recipient {sid} offline after delivery was acknowledged; "
                           f"email/ack is not recallable; escalating in parallel to "
                           f"{backup.stakeholder_id} (next-ranked)."))
        # Not acked -> abort + reroute to next backup.
        backup = _next_backup(plan, snapshots, view)
        if backup is None:
            return Verdict(action="ABORT", decision_code="R2_NO_BACKUP",
                           rationale=f"Recipient {sid} offline mid-flight, no backup available; aborting.")
        return Verdict(
            action="REROUTE", target=backup.stakeholder_id, decision_code="R2_ABORT_REROUTE",
            rationale=(f"Recipient {sid} went offline mid-flight and the send was not "
                       f"acknowledged; aborting current attempt and rerouting to next-"
                       f"ranked backup {backup.stakeholder_id} (snapshot order)."))

    # ------------------------------------------------- candidate available (R4/R5)
    if change.ctype == ChangeType.CANDIDATE_AVAILABLE:
        cand_q = _candidate_qualification(sid, snapshots, stakeholders, alert)
        if sid in view.notified_sids or sid in view.delivered_sids:
            return Verdict(action="IGNORE", decision_code="R5_ALREADY_NOTIFIED",
                           rationale=f"{sid} already received the alert; ignoring.")
        if not view.current_sid:
            # No active recipient yet — nothing to compare against.
            return Verdict(action="IGNORE", decision_code="R5_NO_CURRENT",
                           rationale="No active recipient; ignoring candidate event.")
        if cand_q < current_q + config.min_reroute_delta:
            return Verdict(
                action="IGNORE", decision_code="R5_NO_DOWNGRADE",
                rationale=(f"Candidate {sid} (qualification {cand_q:.2f}) does not beat "
                           f"current recipient {view.current_sid} "
                           f"(qualification {current_q:.2f} + delta {config.min_reroute_delta}); "
                           f"not downgrading / not interrupting."))
        if view.current_acked:
            return Verdict(
                action="ESCALATE_PARALLEL", target=sid, decision_code="R4B_ESCALATE",
                rationale=(f"Higher-qualified candidate {sid} (qualification {cand_q:.2f} vs "
                           f"{current_q:.2f}, delta >= {config.min_reroute_delta}) became "
                           f"available after delivery; escalating in parallel."))
        return Verdict(
            action="REROUTE", target=sid, decision_code="R4A_REROUTE",
            rationale=(f"Higher-qualified candidate {sid} (qualification {cand_q:.2f} vs "
                       f"{current_q:.2f}, delta >= {config.min_reroute_delta}) became "
                       f"available and the send is not yet acknowledged; rerouting."))

    # ----------------------------------------------------------- no change / other
    if change.ctype == ChangeType.NO_CHANGE:
        return Verdict(action="IGNORE", decision_code="NO_CHANGE",
                       rationale="Event matches frozen snapshot; no action.")

    raise UnhandledSituationError(f"Unhandled change type: {change.ctype}")
