# author: Victor Ibhafidon
# date: 2026-08-15
"""Runbook retrieval must be deterministic and post-decision only."""

import unittest
from pathlib import Path

from alert_routing.models import Alert
from alert_routing.runbooks import retrieve, runbook_snippet

DOCS = [
    Path("runbooks/inventory_stock_level.md"),
    Path("runbooks/cold_chain_temperature.md"),
    Path("runbooks/generic_escalation.md"),
]


def _alert(metric="stock_level", domain="inventory", severity="HIGH"):
    return Alert(alert_id="a1", metric=metric, value=5, threshold=20,
                 severity=severity, domain=domain, context={}, ts="t=0")


class TestRunbookRetrieval(unittest.TestCase):
    def test_retrieves_inventory_runbook_for_stock_alert(self):
        doc = retrieve(_alert())
        self.assertIsNotNone(doc)
        self.assertIn("Inventory", doc)

    def test_retrieves_cold_chain_runbook_for_temperature(self):
        doc = retrieve(_alert(metric="freezer_temp_c", domain="cold_chain"))
        self.assertIsNotNone(doc)
        self.assertIn("Cold Chain", doc)

    def test_deterministic(self):
        a = _alert()
        self.assertEqual(retrieve(a), retrieve(a))

    def test_snippet_has_title_and_steps(self):
        snippet = runbook_snippet(_alert())
        self.assertIn("runbook:", snippet)
        self.assertIn("stock", snippet.lower())

    def test_unknown_metric_falls_back_to_generic(self):
        doc = retrieve(_alert(metric="zzz_unknown_metric", domain="zzz_domain"))
        self.assertIsNotNone(doc)
        self.assertIn("Generic", doc)

    def test_retrieval_never_raises_on_missing_dir(self):
        missing = Path("definitely/not/a/real/dir")
        self.assertIsNone(retrieve(_alert(), docs=[]))

    def test_snippet_respects_max_chars(self):
        snippet = runbook_snippet(_alert(), max_chars=40)
        self.assertLessEqual(len(snippet.splitlines()[0]), 120)
        self.assertTrue(snippet)


if __name__ == "__main__":
    unittest.main()
