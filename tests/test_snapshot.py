# author: Victor Ibhafidon
# date: 2026-08-14
"""Snapshot tests: the single-evaluation guarantee (no double-query)."""
import unittest

from alert_routing.ledger import SnapshotAlreadyExists
from alert_routing.snapshotter import snapshot
from alert_routing.ranker import rank

from .helpers import make_alert, make_router


class TestSnapshot(unittest.TestCase):
    def test_second_evaluation_is_physically_impossible(self):
        r = make_router()
        a = make_alert(alert_id="snap-1")
        ranked = rank(a, r.stakeholders)
        entries = snapshot(a, ranked, r.presence, r.ledger, r.clock.now())
        self.assertTrue(entries)
        # A re-evaluation of the SAME (alert, stakeholder) is rejected by the
        # schema PRIMARY KEY — the no-double-query constraint is enforced
        # physically, not by convention.
        with self.assertRaises(SnapshotAlreadyExists):
            snapshot(a, ranked, r.presence, r.ledger, r.clock.now())

    def test_offline_candidate_is_gated(self):
        r = make_router()  # STK-003/006/007 offline by default
        a = make_alert(alert_id="snap-2")
        entries = snapshot(a, rank(a, r.stakeholders), r.presence, r.ledger, r.clock.now())
        by_id = {e.stakeholder_id: e for e in entries}
        self.assertTrue(by_id["STK-003"].gated)    # offline at snapshot
        self.assertFalse(by_id["STK-001"].gated)   # online + on-call + healthy channel

    def test_gated_candidate_retains_frozen_score(self):
        r = make_router()
        a = make_alert(alert_id="snap-3")
        entries = snapshot(a, rank(a, r.stakeholders), r.presence, r.ledger, r.clock.now())
        by_id = {e.stakeholder_id: e for e in entries}
        self.assertAlmostEqual(by_id["STK-007"].qualification, 8.00)  # Maya, offline
        self.assertTrue(by_id["STK-007"].gated)


if __name__ == "__main__":
    unittest.main()
