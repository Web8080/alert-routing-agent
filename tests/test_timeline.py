# author: Victor Ibhafidon
# date: 2026-08-14
"""Incident timeline renderer tests."""
import unittest

from alert_routing.timeline import render_timeline

from .helpers import make_alert, make_router


class TestTimeline(unittest.TestCase):
    def test_timeline_shows_decision_spine_and_final_recipient(self):
        r = make_router()
        a = make_alert(alert_id="tl-1", severity="HIGH")
        r.dispatch(a)
        r.presence.set_online("STK-001", False)
        r.acknowledge()
        r.close()
        text = render_timeline(r.ledger, a.alert_id)
        self.assertIn("INCIDENT TIMELINE", text)
        self.assertIn("R2_ABORT_REROUTE", text)
        self.assertIn("FINAL RECIPIENT: David Miller", text)
        self.assertIn("MESSAGE AS SENT", text)
        self.assertIn("alert_id : tl-1", text)

    def test_timeline_shows_why_you_for_escalation(self):
        r = make_router()
        a = make_alert(alert_id="tl-2", severity="HIGH")
        r.dispatch(a)
        r.acknowledge()
        r.presence.set_online("STK-001", False)  # R3 parallel escalation
        r.acknowledge()
        r.close()
        text = render_timeline(r.ledger, a.alert_id)
        self.assertIn("R3_ESCALATE", text)
        self.assertIn("FINAL RECIPIENT: David Miller", text)


if __name__ == "__main__":
    unittest.main()
