# author: Victor Ibhafidon
# date: 2026-08-16
"""Deferred-delivery guarantee: only the FINAL recipient physically receives a
notification.

The real Slack/SMTP adapters are two-phase: send() decides channel viability at
claim time (deterministic, no I/O) and deliver() performs the physical I/O only
at the commit point (acknowledge/close). This closes the double-notification
bug where a recipient who was aborted/rerouted mid-flight (R2/R2B) had ALREADY
received a real Slack message at claim time — the ledger said CANCELLED but the
message was physically out.

These tests run the real scenario files through run_scenario_data with recording
adapters (no network) and assert the physical-delivery log contains ONLY the
final recipient. The existing suite keeps the stubs; this file proves the
live-delivery path honors the same no-duplicate guarantee.
"""

import json
import unittest
from pathlib import Path
from unittest import mock

import alert_routing.router as router_module
from alert_routing.channels import EmailAdapter, SlackAdapter, SMSAdapter
from alert_routing.cli import run_scenario_data
from alert_routing.models import DeliveryReceipt

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = str(ROOT / "registry.json")


class _RecordingEmail(EmailAdapter):
    def __init__(self, presence, delivered, failures):
        super().__init__(presence)
        self._delivered = delivered
        self._failures = list(failures)

    def deliver(self, notification):
        if self._failures:
            self._failures.pop(0)
            return DeliveryReceipt.RETRIABLE
        self._delivered.append((notification.stakeholder_id, notification.channel))
        return DeliveryReceipt.ACKED


class _RecordingSlack(SlackAdapter):
    def __init__(self, presence, delivered, failures):
        super().__init__(presence)
        self._delivered = delivered
        self._failures = list(failures)

    def deliver(self, notification):
        if self._failures:
            self._failures.pop(0)
            return DeliveryReceipt.RETRIABLE
        self._delivered.append((notification.stakeholder_id, notification.channel))
        return DeliveryReceipt.ACKED


class TestDeferredDelivery(unittest.TestCase):
    def _run_scenario(self, stem, slack_failures=(), email_failures=()):
        """Drive a scenario with recording adapters (no network)."""
        delivered = []

        def fake_adapter_for(name, presence):
            if name == "email":
                return _RecordingEmail(presence, delivered, email_failures)
            if name == "slack":
                return _RecordingSlack(presence, delivered, slack_failures)
            return SMSAdapter(presence)

        data = json.loads((ROOT / "scenarios" / f"{stem}.json").read_text())
        with mock.patch.object(router_module, "adapter_for",
                               side_effect=fake_adapter_for):
            router, aid = run_scenario_data(data, REGISTRY, ":memory:",
                                            alert_id=f"alert-{stem}")
        return router, aid, delivered

    def _statuses(self, router, aid):
        return {(n["stakeholder_id"], n["channel"]): n["status"]
                for n in router.ledger.notifications_for(aid)}

    def test_rerouted_recipient_never_physically_delivered(self):
        """Scenario 1: Sarah aborted mid-flight, David rerouted — only David receives."""
        router, aid, delivered = self._run_scenario("scenario_1_offline")

        self.assertEqual(delivered, [("STK-002", "slack")])
        self.assertNotIn(("STK-001", "slack"), delivered)

        statuses = self._statuses(router, aid)
        self.assertEqual(statuses[("STK-001", "slack")], "CANCELLED")
        self.assertEqual(statuses[("STK-002", "slack")], "DELIVERED")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")

    def test_physical_failure_retries_next_channel_then_delivers_once(self):
        """David's Slack commit fails -> R1 retry via email; one real message."""
        router, aid, delivered = self._run_scenario(
            "scenario_1_offline", slack_failures=(DeliveryReceipt.RETRIABLE,))

        self.assertEqual(delivered, [("STK-002", "email")])
        self.assertEqual(len(delivered), 1)
        statuses = self._statuses(router, aid)
        self.assertEqual(statuses[("STK-002", "slack")], "CANCELLED")
        self.assertEqual(statuses[("STK-002", "email")], "DELIVERED")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")

    def test_acked_recipient_commits_exactly_once(self):
        """Scenario 3: Sarah acked (R5 keeps her) — she is the only delivery."""
        router, aid, delivered = self._run_scenario("scenario_3_no_downgrade")

        self.assertEqual(delivered, [("STK-001", "slack")])
        self.assertEqual(self._statuses(router, aid)[("STK-001", "slack")], "DELIVERED")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")

    def test_channel_fail_recipient_commits_on_fallback_channel(self):
        """Scenario 2: Sarah's Slack fails -> same recipient retried via email."""
        router, aid, delivered = self._run_scenario("scenario_2_channel_fail")

        self.assertEqual(delivered, [("STK-001", "email")])
        statuses = self._statuses(router, aid)
        self.assertEqual(statuses[("STK-001", "slack")], "CANCELLED")
        self.assertEqual(statuses[("STK-001", "email")], "DELIVERED")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")

    def test_batch_reroute_commits_only_best_target(self):
        """Scenario 4: offline + better candidate in one window -> single hop to Priya."""
        router, aid, delivered = self._run_scenario("scenario_4_simultaneous")

        self.assertEqual(delivered, [("STK-006", "email")])
        statuses = self._statuses(router, aid)
        self.assertEqual(statuses[("STK-001", "slack")], "CANCELLED")
        self.assertEqual(statuses[("STK-006", "email")], "DELIVERED")
        self.assertEqual(router.ledger.plan_state(aid), "DELIVERED")


if __name__ == "__main__":
    unittest.main()
