# author: Victor Ibhafidon
# date: 2026-08-14
"""Decision policy tests: R1-R6, MIN_REROUTE_DELTA gate, ack timeout, duty manager."""
import unittest

from alert_routing.channels import BaseAdapter
from alert_routing.decision import decide
from alert_routing.models import Config, DeliveryReceipt, PlanState

from .helpers import make_alert, make_router


class _AlwaysFailAdapter(BaseAdapter):
    """Every send fails RETRIABLE — forces the full fallback chain."""

    def _do_send(self, notification, snapshot_online, health) -> DeliveryReceipt:
        return DeliveryReceipt.RETRIABLE


class TestPolicy(unittest.TestCase):
    # --------------------------------------------------------------- R1 channel fallback
    def test_r1_channel_failure_retries_same_recipient(self):
        r = make_router()
        a = make_alert(alert_id="r1", severity="MEDIUM")
        r.dispatch(a)
        r.presence.set_channel_health("STK-001", "slack", "DOWN")
        r.acknowledge()
        r.close()
        rows = r.ledger.notifications_for(a.alert_id)
        names = {(n["stakeholder_id"], n["channel"], n["status"]) for n in rows}
        self.assertIn(("STK-001", "slack", "CANCELLED"), names)
        self.assertIn(("STK-001", "email", "DELIVERED"), names)  # same recipient, new channel
        self.assertEqual(len({n["stakeholder_id"] for n in rows}), 1)
        self.assertIn("R1_RETRY", [d["code"] for d in r.ledger.decision_log(a.alert_id)])

    # ------------------------------------------------------- R2 abort + reroute (unacked)
    def test_r2_offline_unacked_reroutes_to_next_backup(self):
        r = make_router()
        a = make_alert(alert_id="r2", severity="HIGH")
        r.dispatch(a)
        r.presence.set_online("STK-001", False)
        self.assertIn("R2_ABORT_REROUTE",
                      [d["code"] for d in r.ledger.decision_log(a.alert_id)])
        r.acknowledge()
        r.close()
        rows = r.ledger.notifications_for(a.alert_id)
        by_sid = {n["stakeholder_id"]: n for n in rows}
        self.assertEqual(by_sid["STK-001"]["status"], "CANCELLED")
        self.assertEqual(by_sid["STK-002"]["status"], "DELIVERED")  # David, next-ranked
        self.assertEqual(len([n for n in rows if n["status"] == "DELIVERED"]), 1)

    # --------------------------------------------------- R3 complete + escalate (acked)
    def test_r3_offline_after_ack_escalates_in_parallel(self):
        r = make_router()
        a = make_alert(alert_id="r3", severity="HIGH")
        r.dispatch(a)
        r.acknowledge()                                   # Sarah delivered (email semantics)
        r.presence.set_online("STK-001", False)           # offline discovered AFTER ack
        r.acknowledge()                                   # ack the escalation to David
        r.close()
        rows = r.ledger.notifications_for(a.alert_id)
        sarah = [n for n in rows if n["stakeholder_id"] == "STK-001"][0]
        david = [n for n in rows if n["stakeholder_id"] == "STK-002"][0]
        self.assertEqual(sarah["status"], "DELIVERED")
        self.assertEqual(sarah["escalation_level"], 0)
        self.assertEqual(david["status"], "DELIVERED")
        self.assertEqual(david["escalation_level"], 1)
        self.assertEqual(r.ledger.plan_state(a.alert_id), "ESCALATED")
        codes = [d["code"] for d in r.ledger.decision_log(a.alert_id)]
        self.assertIn("R3_ESCALATE", codes)

    # ----------------------------------------------- R4a reroute to better match (unacked)
    def test_r4a_better_match_unacked_reroutes(self):
        r = make_router()
        a = make_alert(alert_id="r4a", severity="HIGH")
        r.dispatch(a)                                    # pending Sarah (q 6.50)
        r.presence.set_online("STK-007", True)           # Maya online (q 8.00, delta 1.5 >= 1.5)
        codes = [d["code"] for d in r.ledger.decision_log(a.alert_id)]
        self.assertIn("R4A_REROUTE", codes)
        r.acknowledge()
        r.close()
        rows = r.ledger.notifications_for(a.alert_id)
        maya = [n for n in rows if n["stakeholder_id"] == "STK-007"][0]
        self.assertEqual(maya["status"], "DELIVERED")

    # --------------------------------------------------- R4b escalate to better match (acked)
    def test_r4b_better_match_after_ack_escalates(self):
        r = make_router()
        a = make_alert(alert_id="r4b", severity="HIGH")
        r.dispatch(a)
        r.acknowledge()                                  # Sarah delivered
        r.presence.set_online("STK-007", True)           # Maya online mid-flight, after ack
        r.acknowledge()
        r.close()
        codes = [d["code"] for d in r.ledger.decision_log(a.alert_id)]
        self.assertIn("R4B_ESCALATE", codes)
        maya = [n for n in r.ledger.notifications_for(a.alert_id)
                if n["stakeholder_id"] == "STK-007"][0]
        self.assertEqual(maya["escalation_level"], 1)

    # ------------------------------------------------- delta gate: marginal gain is ignored
    def test_marginal_better_match_is_ignored_delta_gate(self):
        r = make_router()
        a = make_alert(alert_id="delta", severity="HIGH")
        r.dispatch(a)                                    # Sarah q 6.50
        r.presence.set_online("STK-006", True)           # Priya q 7.25 -> delta 0.75 < 1.5
        codes = [d["code"] for d in r.ledger.decision_log(a.alert_id)]
        self.assertIn("R5_NO_DOWNGRADE", codes)          # refused: not a material improvement
        r.acknowledge()
        r.close()
        sarah = [n for n in r.ledger.notifications_for(a.alert_id)
                 if n["stakeholder_id"] == "STK-001"][0]
        self.assertEqual(sarah["status"], "DELIVERED")   # Sarah stays primary

    # ------------------------------------------------------- R5 no downgrade (Elena case)
    def test_r5_no_downgrade_senior_but_low_qualification(self):
        r = make_router()
        a = make_alert(alert_id="r5", severity="CRITICAL")
        r.dispatch(a)
        r.presence.set_online("STK-003", True)           # Elena seniority 5, q 3.20 vs 6.50
        codes = [d["code"] for d in r.ledger.decision_log(a.alert_id)]
        self.assertIn("R5_NO_DOWNGRADE", codes)
        r.acknowledge()
        r.close()
        notified = {n["stakeholder_id"] for n in r.ledger.notifications_for(a.alert_id)
                    if n["status"] == "DELIVERED"}
        self.assertEqual(notified, {"STK-001"})          # Sarah only — no downgrade

    # ----------------------------------------- no-downgrade holds after a reroute (David)
    def test_r5_still_applies_after_reroute(self):
        r = make_router()
        a = make_alert(alert_id="r5b", severity="HIGH")
        r.dispatch(a)
        r.presence.set_online("STK-001", False)          # R2 -> David (q 5.80)
        r.presence.set_online("STK-003", True)           # Elena (q 3.20) < David -> ignore
        codes = [d["code"] for d in r.ledger.decision_log(a.alert_id)]
        self.assertEqual(codes.count("R5_NO_DOWNGRADE"), 1)
        r.acknowledge()
        r.close()
        self.assertEqual(r.ledger.plan_state(a.alert_id), "DELIVERED")

    # ------------------------------------------------------------- R4c ack-timeout escalate
    def test_r4c_ack_timeout_escalates(self):
        r = make_router()
        a = make_alert(alert_id="r4c", severity="HIGH")
        r.dispatch(a)                                    # pending Sarah, never acked
        r.evaluate_ack_timeout()                         # timer fires
        codes = [d["code"] for d in r.ledger.decision_log(a.alert_id)]
        self.assertIn("R4C_TIMEOUT", codes)
        r.acknowledge()
        r.close()
        self.assertEqual(r.ledger.plan_state(a.alert_id), "ESCALATED")
        # A second timer fire must not double-escalate.
        before = len(r.ledger.notifications_for(a.alert_id))
        r.evaluate_ack_timeout()
        self.assertEqual(len(r.ledger.notifications_for(a.alert_id)), before)

    def test_r4c_not_armed_for_low_severity(self):
        r = make_router()
        a = make_alert(alert_id="r4c-low", severity="MEDIUM")
        r.dispatch(a)
        r.evaluate_ack_timeout()
        codes = [d["code"] for d in r.ledger.decision_log(a.alert_id)]
        self.assertNotIn("R4C_TIMEOUT", codes)

    # ------------------------------------------------- unknown domain -> duty manager
    def test_unknown_domain_goes_to_duty_manager(self):
        cfg = Config(duty_manager_ids=("STK-002",))
        r = make_router(config=cfg)
        a = make_alert(domain="quantum", alert_id="dm", severity="HIGH")
        r.dispatch(a)
        r.acknowledge()
        r.close()
        delivered = [n for n in r.ledger.notifications_for(a.alert_id)
                     if n["status"] == "DELIVERED"]
        self.assertEqual([n["stakeholder_id"] for n in delivered], ["STK-002"])

    # ------------------------------------------------------------ R6 escalation cap
    def test_r6_escalation_cap_aborts(self):
        r = make_router()
        a = make_alert(alert_id="cap", severity="HIGH")
        r.dispatch(a)
        r.plan.level = 2                                 # simulate two escalations done
        verdict = decide(a, r.plan, r.snapshots, r.stakeholders, r._view(), None,
                         Config(escalation_cap=3), ack_timeout=True)
        self.assertEqual(verdict.action, "ABORT")
        self.assertEqual(verdict.decision_code, "R6_CAP")

    def test_terminal_plan_returns_complete(self):
        r = make_router()
        a = make_alert(alert_id="term", severity="HIGH")
        r.dispatch(a)
        r.plan.state = PlanState.DELIVERED
        verdict = decide(a, r.plan, r.snapshots, r.stakeholders, r._view(), None,
                         Config(), ack_timeout=True)
        self.assertEqual(verdict.decision_code, "TERMINAL")

    # --------------------------- R1 retry must target the CURRENT step, not route[0]
    def test_r1_retry_uses_current_step_after_reroute(self):
        r = make_router()
        for name in ("email", "slack", "sms"):
            r.adapters[name] = _AlwaysFailAdapter(r.presence)
        a = make_alert(alert_id="chain", severity="HIGH")
        r.dispatch(a)                                   # full fallback + reroute chain
        r.acknowledge()
        r.close()
        # Every channel fails for every candidate -> alert resolves as unresolved.
        self.assertEqual(r.ledger.plan_state(a.alert_id), "FAILED")
        codes = [d["code"] for d in r.ledger.decision_log(a.alert_id)]
        self.assertIn("R6_EXHAUSTED", codes)

    # ---------- a retriable primary releases its claim so R1 delivers on a
    # ---------- SECOND channel (the real-recipient fallback that used to die).
    def test_retriable_send_releases_claim_for_r1_fallback(self):
        r = make_router()
        r.adapters["slack"] = _AlwaysFailAdapter(r.presence)   # primary is retriable
        a = make_alert(alert_id="retry-claim", severity="HIGH")
        r.dispatch(a)
        r.acknowledge()
        r.close()
        rows = r.ledger.notifications_for(a.alert_id)
        by = {(n["stakeholder_id"], n["channel"], n["status"]) for n in rows}
        self.assertIn(("STK-001", "slack", "CANCELLED"), by)   # failed attempt released
        self.assertIn(("STK-001", "email", "DELIVERED"), by)   # same recipient, next channel
        self.assertEqual(len({n["stakeholder_id"] for n in rows}), 1)


if __name__ == "__main__":
    unittest.main()
