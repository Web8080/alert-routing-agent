# author: Victor Ibhafidon
# date: 2026-08-14
"""Ranker tests: determinism, qualification ordering, no-downgrade order."""
import unittest

from alert_routing.ranker import rank

from .helpers import make_alert, make_router


class TestRanker(unittest.TestCase):
    def test_determinism(self):
        r1 = make_router()
        r2 = make_router()
        a = make_alert(alert_id="det-a")
        order1 = [s.id for s, _ in rank(a, r1.stakeholders)]
        order2 = [s.id for s, _ in rank(a, r2.stakeholders)]
        self.assertEqual(order1, order2)

    def test_qualification_order_for_inventory(self):
        r = make_router()
        a = make_alert(domain="inventory")
        order = [s.id for s, _ in rank(a, r.stakeholders)]
        self.assertEqual(order, ["STK-007", "STK-006", "STK-001", "STK-002",
                                 "STK-004", "STK-003", "STK-005"])

    def test_expertise_dominates_seniority(self):
        r = make_router()
        a = make_alert(domain="inventory")
        scored = {s.id: q for s, q in rank(a, r.stakeholders)}
        self.assertGreater(scored["STK-001"], scored["STK-003"])  # 6.50 (exp 5) > 3.20 (exp 2, senior 5)


if __name__ == "__main__":
    unittest.main()
