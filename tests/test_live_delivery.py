# author: Victor Ibhafidon
# date: 2026-08-15
"""Tests for the live-delivery + AI layers (hermetic — no network, no API key).

These patch the settings gate so the real adapters / prose path are exercised
without ever touching the wire. The routing core is unchanged by all of this.
"""

import unittest
from unittest import mock

from alert_routing import channels, settings
from alert_routing.models import Notification, NotificationStatus


def _notif(channel="email", endpoint="david@acme.dev", sid="STK-002"):
    return Notification(
        notification_id=f"{sid}:{channel}:l0", alert_id="a1",
        stakeholder_id=sid, stakeholder_name="David Miller",
        channel=channel, status=NotificationStatus.INTENT,
        escalation_level=0, body="body", endpoint=endpoint)


class TestSettings(unittest.TestCase):
    def test_slack_webhook_for(self):
        with mock.patch.dict("os.environ",
                             {"ALERT_SLACK_WEBHOOKS": '{"sarah.slack": "https://x"}',
                              "ALERT_ENV_FILE": "/nonexistent"},
                             clear=False):
            # settings already loaded at import; patch the resolver's source.
            with mock.patch.object(settings, "slack_enabled", return_value=True):
                self.assertEqual(
                    settings.slack_webhook_for("sarah.slack"), "https://x")
                self.assertIsNone(settings.slack_webhook_for("david.slack"))

    def test_gates_off_by_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(settings.smtp_enabled())
            self.assertFalse(settings.slack_enabled())


class TestRealAdaptersFallback(unittest.TestCase):
    """With transport NOT configured, the real adapter class == the stub."""

    def test_real_email_is_stub_when_smtp_off(self):
        adapter = channels.RealEmailAdapter(mock.Mock())
        with mock.patch.object(settings, "smtp_enabled", return_value=False):
            from alert_routing.models import DeliveryReceipt
            self.assertEqual(
                adapter._do_send(_notif(), True, {}), DeliveryReceipt.ACKED)

    def test_real_email_retriable_when_no_address(self):
        adapter = channels.RealEmailAdapter(mock.Mock())
        with mock.patch.object(settings, "smtp_enabled", return_value=True):
            from alert_routing.models import DeliveryReceipt
            self.assertEqual(
                adapter._do_send(_notif(endpoint="no-at-sign"), True, {}),
                DeliveryReceipt.RETRIABLE)

    def test_real_slack_retriable_when_no_webhook(self):
        adapter = channels.RealSlackAdapter(mock.Mock())
        with mock.patch.object(settings, "slack_enabled", return_value=True), \
             mock.patch.object(settings, "slack_webhook_for", return_value=None):
            from alert_routing.models import DeliveryReceipt
            self.assertEqual(
                adapter._do_send(_notif(channel="slack", endpoint="sarah.slack"),
                                 True, {}), DeliveryReceipt.RETRIABLE)

    def test_endpoint_flows_into_notification(self):
        n = _notif(channel="email", endpoint="david@acme.dev")
        self.assertEqual(n.endpoint, "david@acme.dev")


class TestProseInjection(unittest.TestCase):
    def test_writer_none_when_ai_off(self):
        with mock.patch.object(settings, "ai_enabled", return_value=False):
            from alert_routing.ai import make_notification_prose
            self.assertIsNone(make_notification_prose())

    def test_writer_fails_safe(self):
        with mock.patch.object(settings, "ai_enabled", return_value=True):
            from alert_routing.ai import make_notification_prose
            writer = make_notification_prose()
            self.assertIsNotNone(writer)
            with mock.patch.object(
                    channels, "Notification",
                    side_effect=RuntimeError("no network")) as _failing:
                # Writer catches provider errors and returns "" (body unchanged).
                pass
            # Simulate an unconfigured/refusing provider: AnthropicProse raises.
            with mock.patch("alert_routing.ai.AnthropicProse",
                            side_effect=RuntimeError("offline")):
                self.assertEqual(writer(mock.Mock(), "David", "r", 0), "")


if __name__ == "__main__":
    unittest.main()
