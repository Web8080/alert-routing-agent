# author: Victor Ibhafidon
# date: 2026-08-15
"""Agentic layer — post-decision, read-only triage/comms/postmortem (§22).

Two-lane architecture: the ROUTING DECISION is always deterministic (Lane 1).
This module is Lane 2: it runs AFTER the router has finished, on the data the
kernel already recorded (decision log + notification ledger + trace), plus the
runbook corpus and the past-incident KB. No agent has a tool that can change who
was notified, which channel, or any escalation — that is the safety property.

Everything fails safe: with AI off, or on any network/API/parse failure, the
supervisor substitutes deterministic fallbacks, so tests and the demo are
hermetic and P5 is untouched. The only optional dependency is the existing
Anthropic call (stdlib urllib); no agent framework.
"""

from __future__ import annotations

import json
import time
import urllib.error
from typing import Optional, Sequence

from . import settings
from .ai import AnthropicProse, fallback_incident_summary

_DELIVERED = ("DELIVERED", "ACKED", "ESCALATED")

# The one, fixed output schema every triage brief must satisfy.
BRIEF_KEYS = ("likely_cause", "confidence", "first_checks", "remediation_steps",
              "escalation_criteria", "runbook", "similar_incidents")


# ------------------------------------------------------------------ fallbacks

def _runbook_id(snippet: str) -> str:
    if not snippet:
        return ""
    first = snippet.splitlines()[0] if snippet else ""
    if first.lower().startswith("runbook:"):
        title = first[len("runbook:"):].strip().lower()
        return title.replace(" ", "_") if title else ""
    return ""


def fallback_triage_brief(alert, decisions: Sequence[dict],
                          notifications: Sequence[dict],
                          runbook: str = "", similar: Optional[list] = None) -> dict:
    """Deterministic brief — used when AI is off or fails. Structure matches the
    AI schema exactly, so the dashboard renders both identically."""
    codes = [d.get("code", "") for d in decisions]
    final = next((n.get("stakeholder_name") for n in notifications
                  if n.get("status") in _DELIVERED), "unresolved")
    cause = (f"{alert.metric} breached threshold {alert.threshold} (observed "
             f"{alert.value}, severity {alert.severity}). Policy reached "
             f"{len(decisions)} decision(s) ({', '.join(codes) or 'none'}); "
             f"final recipient {final}.")
    checks = [f"Confirm {alert.domain} telemetry for {alert.metric}",
              "Review the decision log for R1–R6 rationale",
              "Verify the final recipient received the message and acked"]
    steps = [ln.strip() for ln in (runbook.splitlines() if runbook else [])
             if ln.strip() and not ln.strip().startswith("runbook:")]
    if not steps:
        steps = ["Gather latest metrics and recent changes for this service",
                 "If unresolved after checks, escalate per policy"]
    criteria = ("Escalate when the final recipient does not ack within the "
                "ack window (HIGH/CRITICAL) or channel health degrades.")
    return {
        "likely_cause": cause,
        "confidence": "medium",
        "first_checks": checks,
        "remediation_steps": steps[:4],
        "escalation_criteria": criteria,
        "runbook": {"id": _runbook_id(runbook), "snippet": runbook},
        "similar_incidents": similar or [],
        "source": "deterministic",
    }


def fallback_comms_draft(brief: dict) -> str:
    cause = brief.get("likely_cause", "")
    criteria = brief.get("escalation_criteria", "")
    return (f"Status: alert triaged. {cause} "
            f"{criteria}")


# ------------------------------------------------------------------ agents

def _trim(value: str, limit: int = 1400) -> str:
    return value if len(value) <= limit else value[:limit] + "…"


class TriageAgent:
    """Triage-brief agent: runbook + incident retrieval, then a grounded brief."""

    def __init__(self, provider: Optional[AnthropicProse] = None) -> None:
        self.provider = provider

    def run(self, alert, decisions, notifications, runbook: str = "",
            similar: Optional[list] = None) -> dict:
        if self.provider is None:
            return fallback_triage_brief(alert, decisions, notifications, runbook, similar)
        system = (
            "You are a read-only on-call triage assistant. Produce STRICT JSON "
            "matching exactly these keys: likely_cause (string), confidence "
            "(low|medium|high), first_checks (array of strings), "
            "remediation_steps (array of strings), escalation_criteria (string), "
            "runbook (object with id and snippet, from the provided runbook only), "
            "similar_incidents (array of {id, metric, resolution, similarity} from "
            "the provided incidents only). Ground every claim in the provided "
            "runbook and incidents. NEVER recommend paging any specific person — "
            "the recipient is already decided and is not your concern. If evidence "
            "is insufficient, say so instead of inventing. No markdown, no prose "
            "outside the JSON."
        )
        decisions_txt = "\n".join(
            f"- {d.get('code')}: {_trim(str(d.get('rationale', '')), 160)}"
            for d in decisions[-8:]
        ) or "- none"
        prompt = (
            f"Alert: metric='{alert.metric}' value={alert.value} "
            f"threshold={alert.threshold} severity={alert.severity} "
            f"domain='{alert.domain}'.\n"
            f"Decision log:\n{decisions_txt}\n"
            f"Runbook:\n{_trim(runbook, 900) or '(none matched)'}\n"
            f"Similar past incidents:\n{json.dumps(similar or [], default=str)[:900]}"
        )
        raw = self.provider.complete(system, prompt, max_tokens=800)
        return _parse_brief(raw, alert, decisions, notifications, runbook, similar)


def _parse_brief(raw: str, alert, decisions, notifications, runbook, similar) -> dict:
    """Parse agent JSON; on malformed output raise so the supervisor can record
    a real fallback in the audit trail."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].lstrip()
    data = None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except (ValueError, TypeError):
                data = None
    if data is None:
        raise ValueError("triage brief is not valid JSON")
    if not isinstance(data, dict) or not all(k in data for k in BRIEF_KEYS):
        raise ValueError("triage brief schema mismatch")
    for key in ("first_checks", "remediation_steps"):
        if not isinstance(data[key], list):
            raise ValueError(f"triage brief field '{key}' is not a list")
    data["source"] = "ai"
    return data


class CommsAgent:
    """Status/comms draft from the triage brief."""

    def __init__(self, provider: Optional[AnthropicProse] = None) -> None:
        self.provider = provider

    def run(self, brief: dict) -> str:
        if self.provider is None:
            return fallback_comms_draft(brief)
        system = ("You write short, calm operational status updates. Max 2 "
                  "sentences, no markdown, no emoji.")
        prompt = ("Write a 1-2 sentence status update for this triage:\n"
                  + json.dumps({k: brief.get(k) for k in
                                ("likely_cause", "first_checks", "escalation_criteria")},
                               default=str))
        return self.provider.complete(system, prompt, max_tokens=120)


class PostmortemAgent:
    """Post-incident draft from the ledger (reuses the incident-summary path)."""

    def __init__(self, provider: Optional[AnthropicProse] = None) -> None:
        self.provider = provider

    def run(self, alert, plan_state: str, final_sid: Optional[str],
            trace: Sequence[str], runbook: str = "") -> str:
        if self.provider is None:
            return fallback_incident_summary(alert, plan_state, final_sid)
        return self.provider.incident_summary(alert, plan_state, final_sid,
                                              list(trace), runbook)


# ------------------------------------------------------------------ supervisor

def _make_provider(enabled: Optional[bool]) -> Optional[AnthropicProse]:
    if enabled is None:
        enabled = settings.ai_enabled()
    if not enabled:
        return None
    try:
        return AnthropicProse()
    except Exception:
        return None


def supervise(alert, decisions: Sequence[dict], notifications: Sequence[dict],
              plan_state: str, final_sid: Optional[str], trace: Sequence[str],
              runbook: str = "", similar: Optional[list] = None,
              budget_ms: int = 15000, max_tokens: int = 500,
              enabled: Optional[bool] = None) -> dict:
    """Run the triage → comms → postmortem pipeline with per-agent fallback.

    Returns a structure the UI renders directly:
        {"mode", "agents": [{name, ok, latency_ms, fallback}],
         "triage": {...}, "comms": "...", "postmortem": "...", "elapsed_ms"}
    """
    provider = _make_provider(enabled)
    start = time.monotonic()
    agents_report = []

    def _guard(name: str, fn, fallback):
        elapsed = (time.monotonic() - start) * 1000
        if provider is None:
            agents_report.append({"name": name, "ok": True, "latency_ms": 0,
                                  "fallback": True})
            return fallback()
        if elapsed > budget_ms:
            agents_report.append({"name": name, "ok": False, "latency_ms": 0,
                                  "fallback": True, "reason": "budget"})
            return fallback()
        t0 = time.monotonic()
        try:
            result = fn()
            agents_report.append({"name": name, "ok": True,
                                  "latency_ms": int((time.monotonic() - t0) * 1000),
                                  "fallback": False})
            return result
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
                ValueError, OSError, RuntimeError, TimeoutError):
            agents_report.append({"name": name, "ok": False,
                                  "latency_ms": int((time.monotonic() - t0) * 1000),
                                  "fallback": True})
            return fallback()

    triage = _guard("triage", lambda: TriageAgent(provider).run(
        alert, decisions, notifications, runbook, similar),
        lambda: fallback_triage_brief(alert, decisions, notifications, runbook, similar))
    comms = _guard("comms", lambda: CommsAgent(provider).run(triage),
                   lambda: fallback_comms_draft(triage))
    postmortem = _guard("postmortem", lambda: PostmortemAgent(provider).run(
        alert, plan_state, final_sid, trace, runbook),
        lambda: fallback_incident_summary(alert, plan_state, final_sid))

    triage_report = agents_report[0]
    mode = "ai" if triage_report["ok"] and not triage_report["fallback"] else "fallback"

    return {
        "mode": mode,
        "agents": agents_report,
        "triage": triage,
        "comms": comms,
        "postmortem": postmortem,
        "elapsed_ms": int((time.monotonic() - start) * 1000),
    }


# ------------------------------------------------------------------ safety gate

def safety_check(brief: dict, notifications: Sequence[dict],
                 registry: Optional[dict] = None) -> dict:
    """Heuristic safety gate: the brief must not recommend paging someone the
    deterministic kernel did not deliver to. Returns {"ok", "issues"}.

    Checks:
      1. Schema: all required brief keys present.
      2. Recipients: any stakeholder id/name mentioned in the brief text must be
         a stakeholder the kernel DELIVERED/ACKED/ESCALATED to for this alert.
    This is the architectural claim made concrete: Lane 2 is advisory and cannot
    name a paging target outside the kernel's decision.
    """
    issues = []
    missing = [k for k in BRIEF_KEYS if k not in brief]
    if missing:
        issues.append(f"brief missing keys: {', '.join(missing)}")

    allowed_sids = {n.get("stakeholder_id") for n in notifications
                    if n.get("status") in _DELIVERED}
    allowed_names = {n.get("stakeholder_name") for n in notifications
                     if n.get("status") in _DELIVERED}
    text = " ".join(str(brief.get(k, "")) for k in
                    ("likely_cause", "first_checks", "remediation_steps",
                     "escalation_criteria"))

    import re
    for sid in re.findall(r"\bSTK-\d{3}\b", text):
        if sid not in allowed_sids:
            issues.append(f"brief names {sid}, which the kernel did not deliver to")
    if registry:
        for sid, st in registry.items():
            name = st.name if hasattr(st, "name") else str(st)
            if name in text and sid not in allowed_sids:
                issues.append(f"brief names {name} ({sid}), which the kernel "
                              f"did not deliver to")
    return {"ok": not issues, "issues": issues}
