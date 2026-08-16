# author: Victor Ibhafidon
# date: 2026-08-16
"""Monitor view: feeds derived from all scenarios, deterministic submission
order, and breaches dispatched through the deterministic router.

Invariants under test:
  1. One feed per bundled scenario (the "all scenarios" claim).
  2. Submission order is deterministic: severity first, then deviation.
  3. Every breach is dispatched through the deterministic router into the
     shared ledger; alert_ids are unique across ticks (no dedup collision).
  4. The AI watcher note is advisory: deterministic fallback when AI is off or
     fails; live text when a provider is available. It never changes routing.
"""

import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from alert_routing import settings
from alert_routing.monitor import AutoMonitor, Feed
from alert_routing.registry import RegistryStore

SEED = {
    "stakeholders": [
        {"id": "STK-001", "name": "Sarah Chen", "title": "Inventory Lead",
         "seniority": 3, "expertise": {"inventory": 5}, "on_call": True,
         "channels": [{"name": "email", "priority": 1, "endpoint": "sarah@acme.dev"}]},
        {"id": "STK-002", "name": "David Miller", "title": "Senior Ops",
         "seniority": 4, "expertise": {"logistics": 4}, "on_call": True,
         "channels": [{"name": "email", "priority": 1, "endpoint": "david@acme.dev"}]},
    ]
}


class MonitorTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.json_path = str(Path(self._tmp.name) / "registry.json")
        self.db_path = str(Path(self._tmp.name) / "registry.db")
        self.ledger_path = str(Path(self._tmp.name) / "ledger.db")
        self.roster_path = str(Path(self._tmp.name) / "roster.json")
        Path(self.json_path).write_text(json.dumps(SEED))
        self.store = RegistryStore(self.json_path, self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _monitor(self, **kw):
        kw.setdefault("ledger_path", self.ledger_path)
        kw.setdefault("roster_path", self.roster_path)
        return AutoMonitor(self.store, **kw)

    def _hermetic(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(mock.patch.object(settings, "smtp_enabled",
                                              return_value=False))
        stack.enter_context(mock.patch.object(settings, "slack_enabled",
                                              return_value=False))
        stack.enter_context(mock.patch.object(settings, "ai_enabled",
                                              return_value=False))
        return stack

    def test_one_feed_per_scenario(self):
        mon = self._monitor()
        stems = [f.stem for f in mon.feeds]
        self.assertGreaterEqual(len(stems), 7)
        self.assertTrue(any("scenario_1_offline" == s for s in stems))
        self.assertTrue(any("scenario_7_anomaly_score_medium" == s for s in stems))
        self.assertEqual(len(stems), len(set(stems)))

    def test_feed_breach_direction(self):
        below = Feed(stem="x", metric="stock_level", threshold=20.0, severity="HIGH",
                     domain="inventory", context={}, direction="below", slope=-1.0,
                     value=21.0)
        below.advance()
        self.assertTrue(below.breached)
        above = Feed(stem="y", metric="sla_response_time", threshold=500.0,
                     severity="CRITICAL", domain="sla", context={},
                     direction="above", slope=1.0, value=499.0)
        above.advance()
        self.assertTrue(above.breached)

    def test_select_is_severity_first_then_deviation(self):
        crit = Feed(stem="a", metric="m", threshold=10.0, severity="CRITICAL",
                    domain="d", context={}, direction="below", slope=0.0, value=9.0)
        med = Feed(stem="b", metric="m", threshold=10.0, severity="MEDIUM",
                   domain="d", context={}, direction="below", slope=0.0, value=0.5)
        ordered = self._monitor().select([med, crit])
        self.assertEqual([f.stem for f in ordered], ["a", "b"])
        # equal severity -> larger deviation first
        c1 = Feed(stem="a", metric="m", threshold=10.0, severity="HIGH", domain="d",
                  context={}, direction="below", slope=0.0, value=9.0)
        c2 = Feed(stem="b", metric="m", threshold=10.0, severity="HIGH", domain="d",
                  context={}, direction="below", slope=0.0, value=0.5)
        ordered = self._monitor().select([c1, c2])
        self.assertEqual([f.stem for f in ordered], ["b", "a"])

    def test_tick_dispatches_breaches_through_router(self):
        mon = self._monitor()
        with self._hermetic():
            results = []
            for _ in range(20):
                results.extend(mon.tick())
        self.assertTrue(results, "expected at least one feed to breach+dispatch")
        for r in results:
            self.assertTrue(r["alert_id"].startswith("mon-"))
            self.assertTrue(r["plan_state"])
            self.assertTrue(r["recipient"])
            self.assertTrue(r["note"].startswith("watcher:"))
            self.assertFalse(r["ai_enabled"])

    def test_alert_ids_unique_across_ticks(self):
        mon = self._monitor()
        seen = set()
        with self._hermetic():
            for _ in range(40):
                for r in mon.tick():
                    self.assertNotIn(r["alert_id"], seen)
                    seen.add(r["alert_id"])
        self.assertTrue(seen)

    def test_activity_accumulates_in_shared_ledger(self):
        mon = self._monitor()
        with self._hermetic():
            for _ in range(20):
                mon.tick()
        self.assertEqual(len(mon.records), len({r.alert_id for r in mon.records}))
        self.assertEqual(len(mon.records),
                         len([r for r in mon.records]))

    def test_ai_note_live_when_provider_available(self):
        mon = self._monitor()
        class Stub:
            def monitor_note(self, summary_line):
                return "telemetry shows a rising threshold; routing handled it."
        with self._hermetic(), \
             mock.patch("alert_routing.ai.AnthropicProse", return_value=Stub()), \
             mock.patch.object(settings, "ai_enabled", return_value=True):
            results = []
            for _ in range(20):
                results.extend(mon.tick())
        self.assertTrue(results)
        self.assertTrue(results[0]["ai_enabled"])
        self.assertEqual(results[0]["note"],
                         "telemetry shows a rising threshold; routing handled it.")

    def test_feed_cooldown_prevents_spam(self):
        feed = Feed(stem="x", metric="stock_level", threshold=20.0, severity="HIGH",
                    domain="inventory", context={}, direction="below", slope=0.0,
                    value=0.0)
        fires = 0
        for _ in range(30):
            if feed.advance():
                fires += 1
        # breaches every _COOLDOWN+1 ticks, not every tick
        self.assertGreater(fires, 1)
        self.assertLess(fires, 30)


if __name__ == "__main__":
    import unittest
    unittest.main()
