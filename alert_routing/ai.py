# author: Victor Ibhafidon
# date: 2026-08-15
"""Post-decision AI prose layer (optional, Anthropic via stdlib urllib).

Design contract (defensible in code review):
  * The ROUTING DECISION is always deterministic. AI is invoked only AFTER the
    decision engine has chosen a recipient, to write human-friendly prose for
    the notification body and to summarize an incident for a timeline/runbook.
    AI output can never change who is notified or how.
  * Everything here is opt-in and fails safe: with no key configured, or on any
    network/API error, we return the deterministic template. Tests and the walkthrough
    therefore always run the same way (P5 determinism is untouched).
  * The prompt is built ONLY from validated internal fields — raw alert context
    is treated as untrusted data (prompt-injection hardening).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from . import settings


# ------------------------------------------------------------------ fallbacks

def fallback_notification_body(alert, recipient_name: str, rationale: str,
                               level: int) -> str:
    """Deterministic prose used when AI is off or unreachable."""
    return (f"Hi {recipient_name},\n\n"
            f"An automated alert was routed to you for the **{alert.metric}** "
            f"threshold breach (severity {alert.severity}).\n\n"
            f"{rationale}\n\n"
            f"Reference: {alert.alert_id} · escalation level {level}.")


def fallback_incident_summary(alert, plan_state: str, final_sid: Optional[str]) -> str:
    final = final_sid or "unresolved"
    return (f"Incident {alert.alert_id}: {alert.metric} breached "
            f"{alert.threshold} (observed {alert.value}, severity "
            f"{alert.severity}). Plan ended {plan_state}; final recipient "
            f"{final}. Decisions are in the decision_log.")


# ------------------------------------------------------------------ provider

class AnthropicProse:
    """Minimal Anthropic Messages client (zero deps).

    ALERT_AI_BASE_URL can point at api.anthropic.com or a local mock for tests.
    """

    def __init__(self) -> None:
        self.url = settings.ai_base_url()
        self.model = settings.ai_model()
        self.api_key = settings.ai_api_key()

    def complete(self, system: str, prompt: str, max_tokens: int = 600) -> str:
        """Raw completion; the single choke point for all AI calls (agent layer
        reuses this). Returns trimmed text or raises on any failure."""
        return self._complete(system, prompt, max_tokens=max_tokens)

    def _complete(self, system: str, prompt: str, max_tokens: int = 600) -> str:
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(self.url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        return data["content"][0]["text"].strip()

    def notification_body(self, alert, recipient_name: str, rationale: str,
                          level: int) -> str:
        system = ("You write short, calm, professional on-call notification "
                  "messages. No markdown headers, no emoji, max 3 sentences.")
        prompt = (
            f"An alert was routed to {recipient_name} for the metric "
            f"'{alert.metric}' (severity {alert.severity}, value "
            f"{alert.value}, threshold {alert.threshold}, domain "
            f"'{alert.domain}'). Routing rationale: {rationale}. "
            f"Escalation level {level}. Write the notification body."
        )
        return self._complete(system, prompt, max_tokens=250)

    def incident_summary(self, alert, plan_state: str, final_sid: Optional[str],
                         trace: list[str], runbook: str = "") -> str:
        system = ("You write concise, factual one-paragraph incident summaries "
                  "for an on-call runbook. No markdown, max 5 sentences.")
        prompt = (
            f"Incident {alert.alert_id}: metric '{alert.metric}' breached "
            f"threshold {alert.threshold} (observed {alert.value}, severity "
            f"{alert.severity}). Final plan state: {plan_state}, final "
            f"recipient: {final_sid or 'unresolved'}. Trace:\n"
            + "\n".join(trace[-25:])
        )
        if runbook:
            prompt += (
                f"\n\nMatching runbook steps (reference only, do not invent "
                f"more detail):\n{runbook}"
            )
        return self._complete(system, prompt, max_tokens=350)


def prose_or_fallback(kind: str, *args, fallback) -> str:
    """Try the AI provider; on any failure return the deterministic fallback."""
    if not settings.ai_enabled():
        return fallback()
    try:
        provider = AnthropicProse()
        if kind == "body":
            alert, recipient_name, rationale, level = args
            return provider.notification_body(alert, recipient_name, rationale, level)
        if kind == "summary":
            alert, plan_state, final_sid, trace, runbook = args
            return provider.incident_summary(alert, plan_state, final_sid, trace, runbook)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
            ValueError, OSError, RuntimeError):
        pass
    return fallback()


def make_notification_prose():
    """Return a Router-compatible prose writer, or None when AI is not enabled.

    The writer is injected at the entry point (CLI/server), NEVER inside the
    routing core. When None (the default in tests and unconfigured runs) the
    router stays 100% deterministic — no network, no variance.
    """
    if not settings.ai_enabled():
        return None

    def writer(alert, recipient_name: str, rationale: str, level: int) -> str:
        try:
            return AnthropicProse().notification_body(
                alert, recipient_name, rationale, level)
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
                ValueError, OSError, RuntimeError):
            return ""

    return writer
