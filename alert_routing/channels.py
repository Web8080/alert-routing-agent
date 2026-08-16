# author: Victor Ibhafidon
# date: 2026-08-14
"""Channel adapters (stubbed, faithful to real provider semantics).

The routing logic never inspects adapter internals — adapters are behind a
uniform interface, so real SMTP/Slack/Twilio adapters are drop-in replacements.
"""

from __future__ import annotations

import sys

from . import settings
from .models import ChannelState, DeliveryReceipt, Notification
from .presence import Presence


class ChannelError(RuntimeError):
    pass


class BaseAdapter:
    name = ""

    def __init__(self, presence: Presence, fail_next: list[DeliveryReceipt] | None = None):
        self.presence = presence
        self.fail_next = list(fail_next or [])

    def _next_forced(self) -> DeliveryReceipt | None:
        if self.fail_next:
            return self.fail_next.pop(0)
        return None

    def send(self, notification: Notification, snapshot_online: bool,
             health: dict[str, ChannelState]) -> DeliveryReceipt:
        forced = self._next_forced()
        if forced is not None:
            return forced
        return self._do_send(notification, snapshot_online, health)

    def _do_send(self, notification: Notification, snapshot_online: bool,
                 health: dict[str, ChannelState]) -> DeliveryReceipt:
        raise NotImplementedError

    def deliver(self, notification: Notification) -> DeliveryReceipt:
        """Physically deliver an already-committed notification (stubs: no-op).

        send() decides channel viability at claim time (deterministic, no I/O);
        deliver() is the irreversible I/O the stubs were emulating. The router
        calls deliver() only once the recipient is committed (acknowledge/close),
        so an INTENT that gets aborted/rerouted mid-flight never reaches a real
        transport. A real transport failure here is RETRIABLE — nothing was
        delivered — so the policy may retry the next channel or reroute.
        """
        return DeliveryReceipt.ACKED


class EmailAdapter(BaseAdapter):
    """Fire-and-forget: an ACK means the mail server accepted the message.

    SMTP acceptance is NOT recallable — this is the semantic basis of rule R3
    (a delivered email cannot be 'aborted', so we complete + escalate in parallel).
    """
    name = "email"

    def _do_send(self, notification, snapshot_online, health) -> DeliveryReceipt:
        if health.get("email") == ChannelState.DOWN:
            return DeliveryReceipt.RETRIABLE
        return DeliveryReceipt.ACKED


class SlackAdapter(BaseAdapter):
    """Presence-aware: an ACK implies the recipient is reachable on Slack."""
    name = "slack"

    def _do_send(self, notification, snapshot_online, health) -> DeliveryReceipt:
        if health.get("slack") == ChannelState.DOWN:
            return DeliveryReceipt.RETRIABLE
        if not snapshot_online:
            return DeliveryReceipt.RETRIABLE
        return DeliveryReceipt.ACKED


class SMSAdapter(BaseAdapter):
    name = "sms"

    def _do_send(self, notification, snapshot_online, health) -> DeliveryReceipt:
        if health.get("sms") == ChannelState.DOWN:
            return DeliveryReceipt.RETRIABLE
        if not snapshot_online:
            return DeliveryReceipt.RETRIABLE
        return DeliveryReceipt.ACKED


class RealEmailAdapter(EmailAdapter):
    """Real SMTP delivery, enabled when ALERT_SMTP_HOST is set.

    Two-phase delivery: send() decides channel viability at claim time
    (deterministic, no I/O); deliver() performs the physical relay only once the
    recipient is committed. SMTP acceptance is non-recallable (R3 basis), so an
    ACK means the relay accepted the message. Transport errors, auth failures,
    and missing/invalid recipient addresses are RETRIABLE (the router falls back
    to the next preferred channel). When SMTP is NOT configured this behaves
    exactly like the stub.
    """

    def _do_send(self, notification, snapshot_online, health) -> DeliveryReceipt:
        stub = super()._do_send(notification, snapshot_online, health)
        if stub != DeliveryReceipt.ACKED:
            return stub
        if not settings.smtp_enabled():
            return DeliveryReceipt.ACKED
        recipient = (notification.endpoint or "").strip()
        if "@" not in recipient:
            print(f"[smtp] no deliverable address for {notification.stakeholder_id}; "
                  f"RETRIABLE -> fallback channel", file=sys.stderr)
            return DeliveryReceipt.RETRIABLE
        return DeliveryReceipt.ACKED

    def deliver(self, notification: Notification) -> DeliveryReceipt:
        if not settings.smtp_enabled():
            return DeliveryReceipt.ACKED
        recipient = (notification.endpoint or "").strip()
        if "@" not in recipient:
            print(f"[smtp] no deliverable address for {notification.stakeholder_id}; "
                  f"RETRIABLE -> fallback channel", file=sys.stderr)
            return DeliveryReceipt.RETRIABLE
        try:
            self._deliver(recipient, notification)
            return DeliveryReceipt.ACKED
        except Exception as exc:  # network / auth / relay errors
            print(f"[smtp] delivery failed for {recipient}: {exc}", file=sys.stderr)
            return DeliveryReceipt.RETRIABLE

    def _deliver(self, recipient: str, notification: Notification) -> None:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = f"[ALERT] {notification.alert_id}"
        msg["From"] = settings.get("ALERT_EMAIL_FROM", "alerts@alert-routing.local")
        msg["To"] = recipient
        msg.set_content(notification.body)

        host = settings.get("ALERT_SMTP_HOST")
        port = int(settings.get("ALERT_SMTP_PORT", "587"))
        user = settings.get("ALERT_SMTP_USER")
        password = settings.get("ALERT_SMTP_PASS")

        if settings.get("ALERT_SMTP_SSL", "").lower() in ("1", "true", "yes"):
            with smtplib.SMTP_SSL(host, port, timeout=30) as s:
                if user:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls()
                if user:
                    s.login(user, password)
                s.send_message(msg)


class RealSlackAdapter(SlackAdapter):
    """Real Slack delivery via incoming webhooks, enabled when ALERT_SLACK_WEBHOOKS is set.

    Two-phase delivery: send() decides channel viability at claim time
    (deterministic, no I/O); deliver() performs the physical webhook POST only
    once the recipient is committed, so a rerouted INTENT never posts. An
    endpoint without a wired webhook is RETRIABLE (the router falls back to the
    next preferred channel) — faithful, honest, and it never fakes an ACK.

    `ALERT_SLACK_WEBHOOKS` is a JSON map of endpoint -> webhook URL:
        {"sarah.slack": "https://hooks.slack.com/services/T/B/X", ...}
    """

    def _do_send(self, notification, snapshot_online, health) -> DeliveryReceipt:
        stub = super()._do_send(notification, snapshot_online, health)
        if stub != DeliveryReceipt.ACKED:
            return stub
        if not settings.slack_enabled():
            return DeliveryReceipt.ACKED
        if settings.slack_webhook_for((notification.endpoint or "").strip()):
            return DeliveryReceipt.ACKED
        print(f"[slack] no webhook wired for '{notification.endpoint}' "
              f"({notification.stakeholder_id}); RETRIABLE -> fallback channel",
              file=sys.stderr)
        return DeliveryReceipt.RETRIABLE

    def deliver(self, notification: Notification) -> DeliveryReceipt:
        if not settings.slack_enabled():
            return DeliveryReceipt.ACKED
        webhook = settings.slack_webhook_for((notification.endpoint or "").strip())
        if not webhook:
            print(f"[slack] no webhook wired for '{notification.endpoint}' "
                  f"({notification.stakeholder_id}); RETRIABLE -> fallback channel",
                  file=sys.stderr)
            return DeliveryReceipt.RETRIABLE
        try:
            self._post(webhook, notification)
            return DeliveryReceipt.ACKED
        except Exception as exc:
            print(f"[slack] webhook failed for {notification.endpoint}: {exc}",
                  file=sys.stderr)
            return DeliveryReceipt.RETRIABLE

    def _post(self, webhook: str, notification: Notification) -> None:
        import json
        import urllib.request

        payload = json.dumps({"text": notification.body}).encode()
        req = urllib.request.Request(webhook, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"webhook HTTP {resp.status}")


def adapter_for(name: str, presence: Presence) -> BaseAdapter:
    """Return the real adapter when its transport is configured, else the stub.

    Real delivery is an opt-in, env-gated enhancement: with nothing set the
    adapters are the deterministic stubs the tests and the walkthrough depend on.
    """
    if name == "email" and settings.smtp_enabled():
        return RealEmailAdapter(presence)
    if name == "slack" and settings.slack_enabled():
        return RealSlackAdapter(presence)
    adapters = {a.name: a for a in (EmailAdapter(presence), SlackAdapter(presence), SMSAdapter(presence))}
    return adapters[name]
