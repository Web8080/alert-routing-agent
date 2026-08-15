# author: Victor Ibhafidon
# date: 2026-08-14
"""Incident timeline UI: renders the decision_log + notifications for an alert
into a human-readable incident timeline (who was notified, when, what changed,
why rerouted, and why the final recipient was chosen).

This is pure presentation over the ledger — the data is already there; this
module just makes the 'explanation of why they were chosen over others'
requirement visible to a human reviewer.
"""

from __future__ import annotations

from typing import Optional

from .ledger import Ledger

_KIND_LABEL = {
    "ingress": "INGEST",
    "rank": "RANK",
    "plan": "PLAN",
    "send": "SEND",
    "event": "EVENT",
    "policy": "DECISION",
    "ledger": "LEDGER",
    "notify": "NOTIFY",
    "summary": "RESULT",
}


def _decision_summary(row: dict) -> str:
    target = f" -> {row['target']}" if row.get("target") else ""
    return f"{row['code']} [{row['action']}{target}] {row['rationale']}"


def render_timeline(ledger: Ledger, alert_id: str) -> str:
    alert = _load_alert(ledger, alert_id)
    plan_state = ledger.plan_state(alert_id)
    rows = ledger.decision_log(alert_id)
    notifications = ledger.notifications_for(alert_id)

    out: list[str] = []
    out.append("=" * 78)
    out.append(f"INCIDENT TIMELINE  {alert_id}")
    if alert is not None:
        out.append(
            f"  {alert['metric']}={alert['value']} (threshold {alert['threshold']}) "
            f"| {alert['severity']} | domain={alert['domain']} | ts={alert['ts']}")
    out.append(f"  plan_state={plan_state}")
    out.append("=" * 78)

    # The decision log is the spine of the incident. Each entry is timestamped
    # by the simulated clock so the 'what changed -> what we did' story reads top
    # to bottom, exactly as it happened.
    if not rows:
        out.append("(no decisions recorded for this alert)")
    for row in rows:
        out.append(f"[{row['logged_ts']}] {_decision_summary(row)}")

    out.append("-" * 78)
    out.append("NOTIFICATIONS (dedup ledger)")
    for n in notifications:
        mark = {"INTENT": "  ", "SENT": "  ", "DELIVERED": "OK",
                "FAILED": "XX", "CANCELLED": "--", "ESCALATED": "ES"}[n["status"]]
        out.append(f"  [{mark}] {n['stakeholder_name']:14s} via {n['channel']:5s} "
                   f"level={n['escalation_level']} status={n['status']}")
    out.append("-" * 78)

    # Final recipient's full context + 'why you were chosen' — the exact payload
    # that satisfied the 'explanation of why they were chosen over others' brief.
    delivered = [n for n in notifications
                 if n["status"] in ("DELIVERED", "ESCALATED")]
    if delivered:
        out.append(f"FINAL RECIPIENT: {delivered[-1]['stakeholder_name']} "
                   f"(level {delivered[-1]['escalation_level']}, {delivered[-1]['channel']})")
        out.append("MESSAGE AS SENT:")
        for line in delivered[-1]["body"].splitlines():
            out.append(f"    {line}")
    else:
        out.append("FINAL RECIPIENT: none — alert unresolved (no delivery acked)")
    out.append("=" * 78)
    return "\n".join(out)


def _load_alert(ledger: Ledger, alert_id: str) -> Optional[dict]:
    cur = ledger.conn.execute(
        "SELECT metric, value, threshold, severity, domain, ts FROM alerts WHERE alert_id=?",
        (alert_id,))
    row = cur.fetchone()
    return dict(row) if row else None
