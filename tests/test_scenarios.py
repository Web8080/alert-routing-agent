# author: Victor Ibhafidon
# date: 2026-08-14
"""End-to-end scenario tests: run the 3 demo scenarios, assert terminal state,
invariants (no duplicate, no downgrade, single-eval) and full context."""
import unittest
from unittest import mock

from alert_routing import settings
from alert_routing.cli import run_scenario

from .helpers import SCENARIOS


def _run(name):
    # Hermetic: a dev `.env` may enable live SMTP/Slack/AI, but the suite must
    # never touch the network. Force the deterministic stub path.
    with mock.patch.object(settings, "smtp_enabled", return_value=False), \
         mock.patch.object(settings, "slack_enabled", return_value=False), \
         mock.patch.object(settings, "ai_enabled", return_value=False):
        return run_scenario(str(SCENARIOS / name), str(SCENARIOS.parent / "registry.json"))


class TestScenarios(unittest.TestCase):
    def _assert_no_duplicate(self, router, alert_id):
        rows = router.ledger.notifications_for(alert_id)
        delivered = [n for n in rows if n["status"] in ("DELIVERED", "ESCALATED")]
        sids = [n["stakeholder_id"] for n in delivered]
        self.assertEqual(len(sids), len(set(sids)), "duplicate delivery for same stakeholder")

    def _assert_single_eval(self, router, alert_id):
        cur = router.ledger.conn.execute(
            "SELECT COUNT(*) AS n FROM snapshots WHERE alert_id=?", (alert_id,))
        self.assertEqual(cur.fetchone()["n"], len(router.stakeholders))

    def test_scenario_1_offline_reroutes(self):
        router, aid = _run("scenario_1_offline.json")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")
        rows = router.ledger.notifications_for(aid)
        self.assertEqual({n["stakeholder_id"] for n in rows}, {"STK-001", "STK-002"})
        codes = [d["code"] for d in router.ledger.decision_log(aid)]
        self.assertIn("R2_ABORT_REROUTE", codes)
        self._assert_no_duplicate(router, aid)
        self._assert_single_eval(router, aid)
        # Context preserved verbatim in the delivered body.
        body = [n for n in rows if n["status"] == "DELIVERED"][0]["body"]
        self.assertIn('"sku": "ACME-100"', body)
        self.assertIn("why you", body)

    def test_scenario_2_channel_fail_falls_back(self):
        router, aid = _run("scenario_2_channel_fail.json")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")
        rows = router.ledger.notifications_for(aid)
        self.assertEqual({n["stakeholder_id"] for n in rows}, {"STK-001"})  # recipient unchanged
        codes = [d["code"] for d in router.ledger.decision_log(aid)]
        self.assertIn("R1_RETRY", codes)
        self._assert_no_duplicate(router, aid)
        body = [n for n in rows if n["status"] == "DELIVERED"][0]["body"]
        self.assertIn("ACME-204", body)

    def test_scenario_3_no_downgrade(self):
        router, aid = _run("scenario_3_no_downgrade.json")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")
        rows = router.ledger.notifications_for(aid)
        delivered = [n for n in rows if n["status"] == "DELIVERED"]
        self.assertEqual([n["stakeholder_id"] for n in delivered], ["STK-001"])
        codes = [d["code"] for d in router.ledger.decision_log(aid)]
        self.assertIn("R5_NO_DOWNGRADE", codes)
        self._assert_no_duplicate(router, aid)
        body = delivered[0]["body"]
        self.assertIn("ACME-77", body)
        self.assertIn("highest-qualified available", body)

    def test_replay_same_input_same_trace(self):
        r1, aid1 = _run("scenario_1_offline.json")
        r2, aid2 = _run("scenario_1_offline.json")
        t1 = [(l.kind, l.text) for l in r1.trace]
        t2 = [(l.kind, l.text) for l in r2.trace]
        self.assertEqual(t1, t2)  # deterministic replay

    def test_scenario_4_simultaneous_fold(self):
        router, aid = _run("scenario_4_simultaneous.json")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")
        rows = router.ledger.notifications_for(aid)
        delivered = [n for n in rows if n["status"] == "DELIVERED"]
        # One window, one hop: Sarah offline + Priya online fold into a single
        # reroute STRAIGHT to Priya (STK-006) — never stranded on David.
        self.assertEqual([n["stakeholder_id"] for n in delivered], ["STK-006"])
        codes = [d["code"] for d in router.ledger.decision_log(aid)]
        self.assertIn("R2B_REROUTE_BEST", codes)
        reroutes = [d for d in router.ledger.decision_log(aid)
                    if d["action"] == "REROUTE"]
        self.assertEqual(len(reroutes), 1)  # no double-hop
        self._assert_no_duplicate(router, aid)
        self._assert_single_eval(router, aid)
        body = delivered[0]["body"]
        self.assertIn('"sku": "ACME-88"', body)

    def test_scenario_5_contract_expiry_routes_to_nina(self):
        router, aid = _run("scenario_5_contract_expiry.json")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")
        rows = router.ledger.notifications_for(aid)
        delivered = [n for n in rows if n["status"] == "DELIVERED"]
        self.assertEqual([n["stakeholder_id"] for n in delivered], ["STK-008"])  # Nina Osei
        self._assert_no_duplicate(router, aid)
        self._assert_single_eval(router, aid)
        self.assertIn("CT-1042", delivered[0]["body"])

    def test_scenario_6_sla_breach_ack_timeout_escalates(self):
        router, aid = _run("scenario_6_sla_breach_ack_timeout.json")
        self.assertEqual(router.ledger.plan_state(aid), "ESCALATED")
        codes = [d["code"] for d in router.ledger.decision_log(aid)]
        self.assertIn("R4C_TIMEOUT", codes)
        rows = router.ledger.notifications_for(aid)
        nina = [n for n in rows if n["stakeholder_id"] == "STK-008"][0]
        self.assertEqual(nina["status"], "DELIVERED")
        self.assertEqual(nina["escalation_level"], 1)
        self._assert_no_duplicate(router, aid)
        self._assert_single_eval(router, aid)
        self.assertIn("billing-api", nina["body"])

    def test_scenario_7_anomaly_medium_does_not_escalate(self):
        router, aid = _run("scenario_7_anomaly_score_medium.json")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")
        codes = [d["code"] for d in router.ledger.decision_log(aid)]
        self.assertIn("R4C_LOW_SEVERITY", codes)          # MEDIUM never auto-escalates
        self.assertNotIn("R4C_TIMEOUT", codes)
        rows = router.ledger.notifications_for(aid)
        delivered = [n for n in rows if n["status"] == "DELIVERED"]
        self.assertEqual([n["stakeholder_id"] for n in delivered], ["STK-009"])  # Leo Park
        self._assert_no_duplicate(router, aid)
        self._assert_single_eval(router, aid)
        self.assertIn("isolation_forest", delivered[0]["body"])


if __name__ == "__main__":
    unittest.main()
