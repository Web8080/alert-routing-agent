# author: Victor Ibhafidon
# date: 2026-08-14
"""Deduplication tests: I1 (one delivery per stakeholder per alert) and
I2 (no primary + escalation to the same person)."""
import unittest

from alert_routing.models import NotificationStatus

from .helpers import make_alert, make_router


class TestDedup(unittest.TestCase):
    def test_claim_twice_same_slot_rejected(self):
        r = make_router()
        a = make_alert(alert_id="dedup-1")
        nid1 = r.ledger.claim(a.alert_id, "STK-001", "Sarah Chen", "slack", 0, "b1")
        nid2 = r.ledger.claim(a.alert_id, "STK-001", "Sarah Chen", "slack", 0, "b1")
        self.assertIsNotNone(nid1)
        self.assertIsNone(nid2)

    def test_escalation_to_same_person_rejected(self):
        r = make_router()
        a = make_alert(alert_id="dedup-2")
        r.ledger.claim(a.alert_id, "STK-001", "Sarah Chen", "slack", 0, "b")
        # Escalation to the SAME stakeholder must be refused (I2).
        self.assertIsNone(r.ledger.claim(a.alert_id, "STK-001", "Sarah Chen", "email", 1, "b"))

    def test_cancelled_slot_semantics(self):
        r = make_router()
        a = make_alert(alert_id="dedup-3")
        nid = r.ledger.claim(a.alert_id, "STK-001", "Sarah Chen", "slack", 0, "b")
        r.ledger.set_status(nid, NotificationStatus.CANCELLED, "t")
        # Same (sid, channel, level) stays consumed — the UNIQUE key never
        # forgets a claim, so a reroute cannot sneak a duplicate past I1.
        from alert_routing.ledger import DuplicateNotification
        with self.assertRaises(DuplicateNotification):
            r.ledger.claim(a.alert_id, "STK-001", "Sarah Chen", "slack", 0, "b")
        # A DIFFERENT channel for the same recipient IS allowed (rule R1 retry),
        # because CANCELLED no longer counts as 'notified'.
        self.assertIsNotNone(
            r.ledger.claim(a.alert_id, "STK-001", "Sarah Chen", "email", 0, "b"))
        self.assertIsNotNone(
            r.ledger.claim(a.alert_id, "STK-002", "David Miller", "email", 0, "b"))

    def test_parallel_alerts_to_same_person_both_deliver(self):
        r = make_router()
        a1 = make_alert(alert_id="par-1")
        a2 = make_alert(alert_id="par-2", severity="LOW")
        n1 = r.ledger.claim(a1.alert_id, "STK-001", "Sarah Chen", "slack", 0, "b")
        n2 = r.ledger.claim(a2.alert_id, "STK-001", "Sarah Chen", "slack", 0, "b")
        self.assertIsNotNone(n1)
        self.assertIsNotNone(n2)  # different alert_id => both legitimately deliver


if __name__ == "__main__":
    unittest.main()
