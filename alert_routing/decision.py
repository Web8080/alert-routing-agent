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

R2B (decide_batch only): several perturbations can land in the SAME decision
    window. decide_batch folds them into ONE verdict instead of N sequential
    single-event decisions: a recipient going offline while a better-qualified
    candidate comes online is a SINGLE hop straight to the best available target
    (no intermediate backup, no second R4a re-reroute). One window => one action.
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
    exclude_attempted: bool = False,
) -> Optional[RouteStep]:
    """Next un-notified, gated-in, online-at-snapshot backup in route order.

    `exclude_attempted` (SAME-LEVEL reroutes only): skip anyone with ANY claim
    at the current level — even if every attempt was CANCELLED. A cancelled
    slot still consumes the UNIQUE (alert, sid, channel, level) key, so
    re-picking an exhausted recipient would crash the claim, and re-picking
    the current recipient would loop forever. Escalations (R3/R4C, level+1)
    keep fresh slots, so they must NOT set this flag.
    """
    for step in plan.route:
        if start_after is not None and step.stakeholder_id == start_after:
            start_after = None
            continue
        if start_after is not None:
            continue
        if step.stakeholder_id in view.notified_sids:
            continue
        if exclude_attempted and step.stakeholder_id in view.attempted_sids:
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
        backup = _next_backup(plan, snapshots, view, exclude_attempted=True)
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
        backup = _next_backup(plan, snapshots, view, exclude_attempted=True)
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


def _offline_batch_verdict(
    alert: Alert,
    plan: Plan,
    snapshots: dict[str, SnapshotEntry],
    stakeholders: dict[str, Stakeholder],
    view: LedgerView,
    candidates: list[DetectedChange],
) -> Verdict:
    """Recipient offline, nothing acked: pick the BEST target now available.

    The recipient is gone, so a move is forced — there is no "stay put" option
    and no churn risk, so MIN_REROUTE_DELTA does not apply here (it guards
    voluntary interruption, not a forced handoff). The fold evaluates the
    next-ranked backup AND every candidate who just came online together and
    hands off to the highest-qualified available one. If a candidate wins, this
    is a SINGLE hop straight to them (R2B) — the sequential alternative (R2 to
    backup, then a separate R4a) would strand a worse recipient. Ties resolve
    to the snapshot-ordered backup for route stability.
    """
    backup = _next_backup(plan, snapshots, view, exclude_attempted=True)
    best_cand = max(
        candidates,
        key=lambda c: _candidate_qualification(
            c.stakeholder_id, snapshots, stakeholders, alert),
        default=None,
    )
    cand_q = _candidate_qualification(best_cand.stakeholder_id, snapshots, stakeholders, alert) \
        if best_cand else float("-inf")
    backup_q = _candidate_qualification(backup.stakeholder_id, snapshots, stakeholders, alert) \
        if backup else float("-inf")
    if best_cand is not None and cand_q > backup_q:
        return Verdict(
            action="REROUTE", target=best_cand.stakeholder_id,
            decision_code="R2B_REROUTE_BEST",
            rationale=(f"Recipient {view.current_sid} went offline mid-flight and "
                       f"candidate {best_cand.stakeholder_id} (qualification {cand_q:.2f}) "
                       f"came online in the same window; folding all events into a single "
                       f"hop straight to {best_cand.stakeholder_id} instead of the "
                       f"next-ranked backup (which would strand a worse recipient)."))
    if backup is not None:
        return Verdict(
            action="REROUTE", target=backup.stakeholder_id,
            decision_code="R2_ABORT_REROUTE",
            rationale=(f"Recipient {view.current_sid} went offline mid-flight and the "
                       f"send was not acknowledged; rerouting to next-ranked backup "
                       f"{backup.stakeholder_id} (snapshot order)."))
    return Verdict(
        action="ABORT", decision_code="R2_NO_BACKUP",
        rationale=f"Recipient {view.current_sid} offline mid-flight, no viable target; aborting.")


def decide_batch(
    alert: Alert,
    plan: Plan,
    snapshots: dict[str, SnapshotEntry],
    stakeholders: dict[str, Stakeholder],
    view: LedgerView,
    changes: Sequence[DetectedChange],
    config: Config,
    ack_timeout: bool = False,
) -> Verdict:
    """Fold ALL pending changes in one decision window into a SINGLE verdict.

    Events can arrive faster than the decision loop drains them (e.g. a scenario
    tick that takes the recipient offline and brings a better candidate online).
    Sequential single-event handling would emit one action per event — possibly
    two hops (R2 then R4a). This function evaluates the whole window at once and
    returns exactly one action. A single-event window must produce the same
    verdict as decide(), so the batch path is a superset, not a divergence.

    Priority in the window (highest first):
      1. ack-timeout                      (delegates to decide)
      2. recipient offline, unacked       (R2 / R2B_REROUTE_BEST — folds candidates)
      3. channel failed, unacked          (R1 retry — transport fix beats re-routing)
      4. better candidate available       (R4a / R4b)
      5. anything else / non-recipient    (IGNORE)
    """
    if plan.state.value in ("DELIVERED", "ESCALATED", "ABORTED", "FAILED"):
        return Verdict(action="COMPLETE", decision_code="TERMINAL",
                       rationale="Plan already in terminal state.")

    if ack_timeout:
        return decide(alert, plan, snapshots, stakeholders, view, None, config,
                      ack_timeout=True)

    cur = view.current_sid
    offline = [c for c in changes
               if c.ctype == ChangeType.RECIPIENT_OFFLINE and c.stakeholder_id == cur]
    chan_fail = [c for c in changes
                 if c.ctype == ChangeType.CHANNEL_FAILED and c.stakeholder_id == cur]
    candidates = [c for c in changes
                  if c.ctype == ChangeType.CANDIDATE_AVAILABLE
                  and c.stakeholder_id not in view.notified_sids
                  and c.stakeholder_id not in view.delivered_sids]

    if offline and not view.current_acked:
        return _offline_batch_verdict(
            alert, plan, snapshots, stakeholders, view, candidates)
    if chan_fail and not view.current_acked:
        return decide(alert, plan, snapshots, stakeholders, view, chan_fail[0], config)
    if candidates:
        # decide() picks R4a (reroute, unacked) or R4b (parallel escalate, acked).
        best = max(candidates, key=lambda c: _candidate_qualification(
            c.stakeholder_id, snapshots, stakeholders, alert))
        return decide(alert, plan, snapshots, stakeholders, view, best, config)
    if offline and view.current_acked:
        return decide(alert, plan, snapshots, stakeholders, view, offline[0], config)
    if chan_fail and view.current_acked:
        return decide(alert, plan, snapshots, stakeholders, view, chan_fail[0], config)
    return Verdict(
        action="IGNORE", decision_code="R5_OTHER_EVENTS",
        rationale="No decisive change for the current recipient in this window.")
