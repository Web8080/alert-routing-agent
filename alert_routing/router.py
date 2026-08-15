# author: Victor Ibhafidon
# date: 2026-08-14
"""Router: orchestrator (the "agent"). Thin coordinator — all rules live in the
decision policy, all state in the ledger.

A dispatch lifecycle:
  dispatch()  -> rank, snapshot (single-eval), plan, claim + start primary send
  on_event()  -> change detector -> decision policy -> apply verdict
  acknowledge() -> ack the in-flight (pending) notification
"""

from __future__ import annotations

import json
from typing import Optional

from .channels import BaseAdapter, adapter_for
from .changes import ChangeType, DetectedChange, detect
from .decision import _next_backup, decide
from .ledger import Ledger
from .models import (
    Alert,
    ChangeNotice,
    Config,
    DeliveryReceipt,
    LedgerView,
    Notification,
    NotificationStatus,
    Plan,
    PlanState,
    RouteStep,
    SnapshotEntry,
    Stakeholder,
    TraceLine,
    Verdict,
)
from .planner import _channel_order, build_plan
from .presence import Presence
from .ranker import rank
from .registry import load_registry
from .snapshotter import snapshot


class Clock:
    def now(self) -> str:
        raise NotImplementedError


class SystemClock(Clock):
    def now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SimClock(Clock):
    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def advance(self, dt: float) -> None:
        self._t += dt

    def now(self) -> str:
        return f"t={self._t:05.1f}s"


class AlertValidationError(ValueError):
    pass


def validate_alert(alert: Alert) -> None:
    if not alert.metric or not alert.domain:
        raise AlertValidationError("metric and domain are required")
    try:
        float(alert.value)
        float(alert.threshold)
    except (TypeError, ValueError) as exc:
        raise AlertValidationError("value and threshold must be numeric") from exc
    if alert.severity not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        raise AlertValidationError(f"bad severity: {alert.severity}")


class Router:
    def __init__(
        self,
        registry_path: str,
        ledger_path: str = ":memory:",
        config: Optional[Config] = None,
        clock: Optional[Clock] = None,
        presence: Optional[Presence] = None,
        prose: Optional[callable] = None,
    ) -> None:
        self.stakeholders: dict[str, Stakeholder] = load_registry(registry_path)
        self.ledger = Ledger(ledger_path)
        self.config = config or Config()
        self.clock = clock or SystemClock()
        self.presence = presence or Presence()
        self.presence.seed_defaults(
            {sid: [p.name for p in s.channels] for sid, s in self.stakeholders.items()})
        self.presence.subscribe(self.on_event)
        self.adapters: dict[str, BaseAdapter] = {
            name: adapter_for(name, self.presence) for name in ("email", "slack", "sms")
        }
        self.prose = prose  # optional post-decision prose writer; None keeps routing 100% deterministic

        self.alert: Optional[Alert] = None
        self.plan: Optional[Plan] = None
        self.snapshots: dict[str, SnapshotEntry] = {}
        self.pending: Optional[Notification] = None
        self.trace: list[TraceLine] = []

    # ------------------------------------------------------------- lifecycle

    def dispatch(self, alert: Alert) -> None:
        validate_alert(alert)
        if self.ledger.alert_exists(alert.alert_id):
            self.trace.append(TraceLine(alert.ts, "ingress",
                                        f"{alert.alert_id} already ingested; skipping (idempotent)"))
            return
        self.alert = alert
        self.ledger.create_alert(alert)
        self.trace.append(TraceLine(alert.ts, "ingress",
                                    f"received: {alert.metric}={alert.value} "
                                    f"threshold={alert.threshold} {alert.severity} {alert.domain}"))

        ranked = rank(alert, self.stakeholders)
        for s, q in ranked:
            self.trace.append(TraceLine(alert.ts, "rank",
                                        f"{s.id:8s} {s.name:14s} q={q:.2f}"))

        entries = snapshot(alert, ranked, self.presence, self.ledger, self.clock.now())
        self.snapshots = {e.stakeholder_id: e for e in entries}

        self.plan = build_plan(alert.alert_id, alert.severity, self.stakeholders,
                               self.snapshots, self.config)
        self._apply_duty_manager_if_no_experts()
        self.ledger.save_plan(self.plan, self.clock.now())
        self.trace.append(TraceLine(self.clock.now(), "plan",
                                    f"route={[f'{s.stakeholder_id}({s.channel_order})' for s in self.plan.route]} "
                                    f"cap={self.plan.escalation_cap}"))
        self._start_send()

    # -------------------------------------------------------------- sends

    def _compose_body(self, step: RouteStep, level: int, rationale: str) -> str:
        a = self.alert
        ctx = json.dumps(a.context, sort_keys=True)
        lines = [
            f"[ALERT] {a.metric} threshold breached",
            f"  for      : {step.name}",
            f"  severity : {a.severity}",
            f"  value    : {a.value} (threshold {a.threshold})",
            f"  domain   : {a.domain}",
            f"  context  : {ctx}",
            f"  alert_id : {a.alert_id}",
        ]
        if rationale:
            lines.append(f"  why you  : {rationale}")
        lines.append(f"  level    : {level}")
        return "\n".join(lines)

    def _view(self) -> LedgerView:
        acked = False
        current_sid = None
        current_channel = None
        if self.plan and self.plan.route:
            step = self.plan.current_step()
            if step:
                current_sid = step.stakeholder_id
                current_channel = step.channel_order[step.channel_index] \
                    if step.channel_index < len(step.channel_order) else None
                if self.pending is None:
                    acked = step.stakeholder_id in self.ledger.delivered_sids(self.alert.alert_id)
        return LedgerView(
            delivered_sids=self.ledger.delivered_sids(self.alert.alert_id),
            current_sid=current_sid,
            current_channel=current_channel,
            current_acked=acked,
            notified_sids=self.ledger.notified_sids(self.alert.alert_id),
            attempted_sids=self.ledger.attempted_sids(
                self.alert.alert_id, self.plan.level if self.plan else 0),
        )

    def _start_send_for(self, sid: str, level: int, rationale: str) -> None:
        """Claim + initiate a send for a route step; handle RETRIABLE via policy."""
        a = self.alert
        step = self.plan.current_step()
        channel = step.channel_order[step.channel_index] \
            if step.channel_index < len(step.channel_order) else None
        if channel is None:
            self._apply_verdict(decide(a, self.plan, self.snapshots, self.stakeholders,
                                       self._view(), None, self.config,
                                       ack_timeout=False))
            return
        snap = self.snapshots[sid]
        body = self._compose_body(step, level, rationale)
        if self.prose is not None:
            note = self.prose(a, step.name, rationale, level)
            if note:
                body = f"{body}\n\n{note}"
        endpoint = next((c.endpoint for c in self.stakeholders[sid].channels
                         if c.name == channel), "")
        nid = self.ledger.claim(a.alert_id, sid, step.name, channel, level, body)
        if nid is None:
            self.trace.append(TraceLine(self.clock.now(), "ledger",
                                        f"claim rejected {sid}/{channel} (already notified)"))
            backup = _next_backup(self.plan, self.snapshots, self._view(),
                                  exclude_attempted=True)
            if backup is None:
                self.plan.state = PlanState.FAILED
                self.ledger.set_plan_state(a.alert_id, PlanState.FAILED)
                self._log("R6_EXHAUSTED", "ABORT", None,
                          "No un-notified backup remains; alert unresolved.")
            else:
                self._move_to_target(backup.stakeholder_id)
                self._start_send_for(backup.stakeholder_id, level, rationale)
            return
        self.trace.append(TraceLine(self.clock.now(), "ledger", f"INTENT  {nid}"))
        notif = Notification(nid, a.alert_id, sid, step.name, channel,
                             NotificationStatus.INTENT, level, body, endpoint)
        receipt = self.adapters[channel].send(notif, snap.online, snap.channel_health)
        if receipt == DeliveryReceipt.RETRIABLE:
            self.trace.append(TraceLine(self.clock.now(), "send",
                                        f"RETRIABLE {a.alert_id} -> {sid} via {channel}"))
            # Release the INTENT claim: dedup (I1) only blocks a non-cancelled
            # slot, so rule R1 can retry the SAME recipient on the next channel
            # (a retriable send was never delivered — nothing to preserve).
            self.ledger.set_status(nid, NotificationStatus.CANCELLED, self.clock.now())
            self.trace.append(TraceLine(self.clock.now(), "ledger",
                                        f"CANCELLED {nid} (retriable -> retry next channel)"))
            change = DetectedChange(ChangeType.CHANNEL_FAILED, sid, channel=channel, snapshot=snap)
            self._apply_verdict(decide(a, self.plan, self.snapshots, self.stakeholders,
                                       self._view(), change, self.config))
        else:
            self.pending = notif
            self.plan.state = PlanState.SENDING
            self.ledger.set_plan_state(a.alert_id, PlanState.SENDING)
            self.trace.append(TraceLine(self.clock.now(), "send",
                                        f"SENDING {a.alert_id} -> {sid} via {channel} "
                                        f"(level={level})"))

    def _apply_duty_manager_if_no_experts(self) -> None:
        """E8: unknown/unmapped domain → no candidate has expertise.

        If no one qualifies (top qualification == 0) and a duty manager is
        configured, route to the first available duty manager instead of an
        arbitrary tie-broken candidate. Rationale is surfaced in the send body."""
        if not self.config.duty_manager_ids:
            return
        top_q = max((snap.qualification for snap in self.snapshots.values()), default=0.0)
        if top_q > 0.0:
            return
        for dm in self.config.duty_manager_ids:
            snap = self.snapshots.get(dm)
            if snap is None:
                continue
            order = _channel_order(self.stakeholders[dm], snap)
            if not order:
                continue
            self.plan.route = [RouteStep(dm, snap.name, 0.0, order)]
            self.plan.step_index = 0
            self.trace.append(TraceLine(self.clock.now(), "plan",
                                        f"no domain experts; routing to duty manager {dm}"))
            return

    def _start_send(self) -> None:
        step = self.plan.current_step()
        if step is None:
            self.plan.state = PlanState.FAILED
            self.ledger.set_plan_state(self.alert.alert_id, PlanState.FAILED)
            self._log("R6_UNRESOLVED", "ABORT", None,
                      "No gated-in candidates at plan time; alert unresolved.")
            return
        snap = self.snapshots[step.stakeholder_id]
        self._start_send_for(step.stakeholder_id, self.plan.level,
                             self._primary_rationale(step, snap))

    def _primary_rationale(self, step: RouteStep, snap: SnapshotEntry) -> str:
        """Why the primary was chosen over every other candidate (the 'why you')."""
        a = self.alert
        reasons = []
        for other_sid, other in self.snapshots.items():
            if other_sid == step.stakeholder_id:
                continue
            if other.qualification > snap.qualification:
                reasons.append(f"{other.name} ({other.qualification:.2f}) ranked higher "
                               "but was unavailable at snapshot")
            else:
                state = "offline at snapshot" if not other.online else \
                        "not on call" if not self.stakeholders[other_sid].on_call else \
                        "lower qualification"
                reasons.append(f"{other.name} ({other.qualification:.2f}) skipped: {state}")
        why = f"{step.name} is the highest-qualified available stakeholder for {a.domain} " \
              f"(qualification {snap.qualification:.2f})."
        if reasons:
            why += " " + " ".join(reasons[:3])
        return why

    def acknowledge(self) -> None:
        """Finalize the in-flight send (the 'ack' from the channel).

        Does NOT set the plan terminal: the parallel-escalate path (R3/R4b)
        must still be able to react to events that arrive after the ack but
        before the dispatch is closed. close() finalizes the plan state."""
        if self.pending is None:
            return
        n = self.pending
        self.ledger.set_status(n.notification_id, NotificationStatus.DELIVERED, self.clock.now())
        self.trace.append(TraceLine(self.clock.now(), "ledger",
                                    f"DELIVERED {n.notification_id}"))
        self.pending = None
        self.trace.append(TraceLine(self.clock.now(), "notify",
                                    f"DELIVERED body for {n.stakeholder_name}:\n{n.body}"))

    def close(self) -> None:
        """End the dispatch: finalize plan state (DELIVERED or ESCALATED)."""
        if self.plan is None or self.plan.state.value in ("ABORTED", "FAILED"):
            return
        if self.pending is not None:
            self.acknowledge()
        escalated = any(
            n["escalation_level"] >= 1 and n["status"] in ("DELIVERED", "ESCALATED")
            for n in self.ledger.notifications_for(self.alert.alert_id))
        self.plan.state = PlanState.ESCALATED if escalated else PlanState.DELIVERED
        self.ledger.set_plan_state(self.alert.alert_id, self.plan.state)
        self.trace.append(TraceLine(self.clock.now(), "summary",
                                    f"plan={self.plan.state.value}"))

    # -------------------------------------------------------------- events

    def on_event(self, notice: ChangeNotice) -> None:
        if self.plan is None or self.plan.state.value in (
                "DELIVERED", "ESCALATED", "ABORTED", "FAILED"):
            return
        a = self.alert
        self.trace.append(TraceLine(self.clock.now(), "event",
                                    f"{notice.event} {notice.stakeholder_id} {notice.payload}"))
        change = detect(notice, self.snapshots)
        self._apply_event_to_snapshot(change)
        if change.ctype.value == "no_change":
            self._log("NO_CHANGE", "IGNORE", None,
                      f"Event for {notice.stakeholder_id} matches frozen snapshot; no action.")
            return
        verdict = decide(a, self.plan, self.snapshots, self.stakeholders,
                         self._view(), change, self.config)
        self._apply_verdict(verdict)

    def _apply_event_to_snapshot(self, change: DetectedChange) -> None:
        """Fold event truth into the frozen snapshot (a diff, NOT a re-query).

        The no-double-query guarantee forbids *reading presence again*; it does
        not forbid recording what an event already told us. A candidate who came
        online must be treated as online by adapters and reroute logic."""
        import dataclasses
        from .models import ChannelState
        sid = change.stakeholder_id
        snap = change.snapshot
        if snap is None or sid not in self.snapshots:
            return
        if change.ctype in (ChangeType.CANDIDATE_AVAILABLE, ChangeType.RECIPIENT_OFFLINE):
            self.snapshots[sid] = dataclasses.replace(snap, online=change.online)
        elif change.ctype == ChangeType.CHANNEL_FAILED and change.channel:
            health = dict(snap.channel_health)
            health[change.channel] = ChannelState.DOWN
            self.snapshots[sid] = dataclasses.replace(snap, channel_health=health)

    def evaluate_ack_timeout(self) -> None:
        """Called by the ack-timer / control points. Pure policy evaluation.

        Determinism enforcement: the ack-timeout path is evaluated against the
        *current* clock time, so it must only run under a scripted (injected)
        clock. A bare SystemClock would make the decision wall-clock-dependent
        and break the P5 "same state ⇒ same decision" guarantee — fail loudly
        instead of silently depending on the wall clock.
        """
        if isinstance(self.clock, SystemClock):
            raise RuntimeError(
                "evaluate_ack_timeout requires an injected scripted clock "
                "(SimClock); a wall-clock SystemClock would make R4c "
                "nondeterministic. Inject a SimClock and advance it explicitly.")
        if self.plan is None or self.plan.state.value in (
                "DELIVERED", "ESCALATED", "ABORTED", "FAILED"):
            return
        if self.pending is not None and self.pending.escalation_level >= 1:
            return
        verdict = decide(self.alert, self.plan, self.snapshots, self.stakeholders,
                         self._view(), None, self.config, ack_timeout=True)
        self._apply_verdict(verdict)

    # -------------------------------------------------------------- verdicts

    def _apply_verdict(self, verdict: Verdict) -> None:
        if self.plan is None:
            return
        self._log(verdict.decision_code, verdict.action, verdict.target, verdict.rationale)
        action = verdict.action
        if action == "IGNORE":
            return
        if action == "COMPLETE":
            return
        if action == "RETRY_CHANNEL":
            self._cancel_pending()
            step = self.plan.current_step()
            step.channel_index = step.channel_order.index(verdict.channel)
            self._start_send_for(step.stakeholder_id, self.plan.level, verdict.rationale)
            return
        if action == "REROUTE":
            self._cancel_pending()
            self._move_to_target(verdict.target)
            self._start_send_for(self.plan.current_step().stakeholder_id,
                                 self.plan.level, verdict.rationale)
            return
        if action == "ESCALATE_PARALLEL":
            new_level = self.plan.level + 1
            if new_level >= self.plan.escalation_cap:
                self.plan.state = PlanState.ABORTED
                self.ledger.set_plan_state(self.alert.alert_id, PlanState.ABORTED)
                self._log("R6_CAP", "ABORT", None,
                          f"Escalation cap ({self.plan.escalation_cap}) reached.")
                return
            self.plan.level = new_level
            self.ledger.set_plan_level(self.alert.alert_id, new_level)
            self._move_to_target(verdict.target, level=new_level)
            self._start_send_for(self.plan.current_step().stakeholder_id,
                                 new_level, verdict.rationale)
            return
        if action == "ABORT":
            self._cancel_pending()
            self.plan.state = PlanState.FAILED
            self.ledger.set_plan_state(self.alert.alert_id, PlanState.FAILED)
            self.trace.append(TraceLine(self.clock.now(), "summary",
                                        "plan=FAILED (unresolved); context preserved in ledger"))
            return
        raise RuntimeError(f"unhandled verdict action: {action}")

    def _move_to_target(self, sid: str, level: Optional[int] = None) -> None:
        for i, step in enumerate(self.plan.route):
            if step.stakeholder_id == sid:
                self.plan.step_index = i
                step.channel_index = 0
                return
        s = self.stakeholders.get(sid)
        snap = self.snapshots.get(sid)
        q = snap.qualification if snap else \
            (s.expertise.get(self.alert.domain, 0) * (1.0 + (s.seniority - 1) * 0.15) if s else 0.0)
        channel_order = _channel_order(s, snap) if s else []
        self.plan.route.append(RouteStep(sid, s.name if s else sid, q, channel_order))
        self.plan.step_index = len(self.plan.route) - 1

    def _cancel_pending(self) -> None:
        if self.pending is not None:
            self.ledger.set_status(self.pending.notification_id,
                                   NotificationStatus.CANCELLED, self.clock.now())
            self.trace.append(TraceLine(self.clock.now(), "ledger",
                                        f"CANCELLED {self.pending.notification_id}"))
            self.pending = None

    def _log(self, code: str, action: str, target: Optional[str], rationale: str) -> None:
        if self.alert is None:
            return
        seq = self.ledger.next_seq(self.alert.alert_id)
        self.ledger.log_decision(self.alert.alert_id, seq, code, action, target,
                                 rationale, self.clock.now())
        self.trace.append(TraceLine(self.clock.now(), "policy",
                                    f"[{code}] {action} target={target} :: {rationale}"))
