# author: Victor Ibhafidon
# date: 2026-08-15
"""The agentic layer (§22) must stay read-only, post-decision, and fallback-safe.

Three invariants under test:
  1. The triage brief is grounded in runbooks + past incidents and never names a
     paging target the deterministic kernel did not deliver to.
  2. Every agent degrades to a deterministic fallback when the LLM is disabled
     or fails, and the supervisor records an audit trail.
  3. Incident-KB retrieval is deterministic and persisted incidents round-trip.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alert_routing import settings
from alert_routing.agents import (BRIEF_KEYS, fallback_triage_brief,
                                  safety_check, supervise)
from alert_routing.incidents import load_incidents, record_incident, similar_incidents
from alert_routing.ui import _summary_payload
from tests.helpers import make_alert, make_router


class TestIncidentRetrieval(unittest.TestCase):
    def test_most_similar_incident_ranks_first(self):
        sim = similar_incidents(
            make_alert(alert_id="alert-stock_level",
                       context={"sku": "ACME-100", "warehouse": "WH-4"}),
            load_incidents())
        self.assertTrue(sim)
        self.assertEqual(sim[0]["id"], "inc_stock_wh4")
        self.assertEqual(sim[0]["similarity"], 1.0)

    def test_scores_descend(self):
        sim = similar_incidents(
            make_alert(alert_id="alert-stock_level",
                       context={"sku": "ACME-100", "warehouse": "WH-4"}),
            load_incidents())
        scores = [i["similarity"] for i in sim]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_record_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            alert = make_alert(alert_id="alert-cold-chain", domain="cold-chain",
                               metric="freezer_temp_c")
            record_incident(alert, "DELIVERED", "Grace Lin", resolution="fixed",
                            path=tmp)
            loaded = load_incidents(tmp)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["id"], "alert-cold-chain")
            self.assertEqual(loaded[0]["final_recipient"], "Grace Lin")

    def test_record_sanitizes_alert_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            alert = make_alert(alert_id="custom/al:ert#1")
            record_incident(alert, "DELIVERED", "Sarah Chen", path=tmp)
            self.assertIn("customalert1.json", {p.name for p in Path(tmp).iterdir()})


class TestFallbackBrief(unittest.TestCase):
    def test_has_all_brief_keys(self):
        brief = fallback_triage_brief(
            make_alert(), [], [{"stakeholder_id": "STK-001", "status": "INTENT"}],
            "runbook: inventory\nrecount the stock", [{"id": "inc_x"}])
        for key in BRIEF_KEYS:
            self.assertIn(key, brief)

    def test_runbook_is_object(self):
        brief = fallback_triage_brief(
            make_alert(), [], [], "runbook: inventory\nrecount stock", [])
        self.assertIsInstance(brief["runbook"], dict)
        self.assertEqual(brief["runbook"]["id"], "inventory")
        self.assertIn("recount stock", brief["runbook"]["snippet"])

    def test_recommendations_never_name_a_stakeholder(self):
        brief = fallback_triage_brief(
            make_alert(), [], [{"stakeholder_id": "STK-001",
                                "stakeholder_name": "Sarah Chen", "status": "INTENT"}],
            "runbook: inventory\nverify stock", [])
        text = " ".join(str(brief[k]) for k in
                        ("first_checks", "remediation_steps", "escalation_criteria"))
        self.assertNotIn("Sarah", text)


class TestSupervisor(unittest.TestCase):
    def test_fallback_mode_audit_trail(self):
        router = make_router()
        alert = make_alert(alert_id="alert-stock_level")
        router.dispatch(alert)
        notifs = router.ledger.notifications_for(alert.alert_id)
        final_sid = notifs[0]["stakeholder_id"]
        sup = supervise(alert, router.ledger.decision_log(alert.alert_id), notifs,
                        router.ledger.plan_state(alert.alert_id), final_sid,
                        ["t1 ingress"], runbook="", similar=[], enabled=False)
        self.assertEqual(sup["mode"], "fallback")
        self.assertEqual([a["name"] for a in sup["agents"]],
                         ["triage", "comms", "postmortem"])
        self.assertTrue(all(a["fallback"] for a in sup["agents"]))
        self.assertIn("elapsed_ms", sup)

    def test_fallback_when_disabled_is_deterministic(self):
        router = make_router()
        alert = make_alert(alert_id="alert-stock_level")
        router.dispatch(alert)
        notifs = router.ledger.notifications_for(alert.alert_id)
        final_sid = notifs[0]["stakeholder_id"]
        kwargs = dict(decisions=[], notifications=notifs, plan_state="P",
                      final_sid=final_sid, trace=[], runbook="", similar=[],
                      enabled=False)
        a = supervise(alert, **kwargs)["triage"]
        b = supervise(alert, **kwargs)["triage"]
        self.assertEqual(a, b)

    def test_summary_payload_includes_triage(self):
        with mock.patch.object(settings, "ai_enabled", return_value=False):
            router = make_router()
            alert = make_alert(alert_id="alert-stock_level",
                               context={"sku": "ACME-100", "warehouse": "WH-4"})
            router.dispatch(alert)
            payload = _summary_payload(router, alert.alert_id)
            self.assertIn("ai_triage", payload)
            self.assertEqual(payload["ai_triage"]["mode"], "fallback")
            self.assertIn("triage", payload["ai_triage"])


class TestSupervisorHonesty(unittest.TestCase):
    """Mode + audit trail must reflect the brief's real source (no silent AI)."""

    GOOD_JSON = ('{"likely_cause": "stock drop", "confidence": "high", '
                 '"first_checks": ["verify stock"], "remediation_steps": ["recount"], '
                 '"escalation_criteria": "if no ack", '
                 '"runbook": {"id": "g", "snippet": ""}, "similar_incidents": []}')

    class _Stub:
        def __init__(self, out):
            self.out = out

        def complete(self, system, prompt, max_tokens=500):
            return self.out

        def incident_summary(self, alert, plan_state, final_sid, trace, runbook):
            return "postmortem draft"

    def _supervise_with(self, out):
        router = make_router()
        alert = make_alert(alert_id="alert-stock_level")
        router.dispatch(alert)
        notifs = router.ledger.notifications_for(alert.alert_id)
        final_sid = notifs[0]["stakeholder_id"]
        stub = self._Stub(out)
        with mock.patch("alert_routing.agents._make_provider", return_value=stub):
            return supervise(alert, [], notifs, "P", final_sid, [], "", [], enabled=True)

    def test_valid_json_reports_ai(self):
        sup = self._supervise_with(self.GOOD_JSON)
        self.assertEqual(sup["mode"], "ai")
        self.assertEqual(sup["triage"]["source"], "ai")
        self.assertFalse(sup["agents"][0]["fallback"])

    def test_garbage_reports_fallback(self):
        sup = self._supervise_with("not json at all")
        self.assertEqual(sup["mode"], "fallback")
        self.assertNotEqual(sup["triage"].get("source"), "ai")
        self.assertTrue(sup["agents"][0]["fallback"])
        self.assertFalse(sup["agents"][0]["ok"])

    def test_wrong_schema_reports_fallback(self):
        sup = self._supervise_with('{"likely_cause": "x"}')
        self.assertEqual(sup["mode"], "fallback")
        self.assertTrue(sup["agents"][0]["fallback"])


class TestSafetyGate(unittest.TestCase):
    NOTIF = [{"stakeholder_id": "STK-001", "stakeholder_name": "Sarah Chen",
              "status": "DELIVERED"}]

    def test_clean_brief_passes(self):
        brief = {"likely_cause": "x", "confidence": 0.9, "first_checks": [],
                 "remediation_steps": [], "escalation_criteria": "contact Sarah Chen",
                 "runbook": {"id": "g", "snippet": ""}, "similar_incidents": []}
        check = safety_check(brief, self.NOTIF, {"STK-001": "Sarah Chen"})
        self.assertTrue(check["ok"])
        self.assertEqual(check["issues"], [])

    def test_defect_stakeholder_flagged(self):
        brief = {"likely_cause": "Contact Maria Rossi", "confidence": 0.9,
                 "first_checks": [], "remediation_steps": [],
                 "escalation_criteria": "page STK-009",
                 "runbook": {"id": "g", "snippet": ""}, "similar_incidents": []}
        check = safety_check(brief, self.NOTIF,
                             {"STK-001": "Sarah Chen", "STK-009": "Maria Rossi"})
        self.assertFalse(check["ok"])
        self.assertTrue(any("STK-009" in i for i in check["issues"]))
        self.assertTrue(any("Maria Rossi" in i for i in check["issues"]))

    def test_missing_schema_keys_flagged(self):
        check = safety_check({"likely_cause": "x"}, self.NOTIF)
        self.assertFalse(check["ok"])
        self.assertTrue(any("missing keys" in i for i in check["issues"]))


if __name__ == "__main__":
    unittest.main()
