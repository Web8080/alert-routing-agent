# author: Victor Ibhafidon
# date: 2026-08-14
"""End-to-end scenario tests: run the 3 demo scenarios, assert terminal state,
invariants (no duplicate, no downgrade, single-eval) and full context."""
import unittest

from alert_routing.cli import run_scenario

from .helpers import SCENARIOS


def _run(name):
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


if __name__ == "__main__":
    unittest.main()
