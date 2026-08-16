# author: Victor Ibhafidon
# date: 2026-08-15
"""Runtime settings — env vars + a tiny .env loader (zero third-party deps).

Everything here is OPTIONAL. The agent runs fully with nothing set (the channel
adapters fall back to their deterministic stubs). Set the delivery vars only to
turn on REAL email/Slack delivery and the AI prose layer.

Secrets policy (security-by-design): real credentials belong in `.env`
(never committed; see `.env.example`). The loader reads `.env` from the CWD or
from `ALERT_ENV_FILE` if set, and `os.environ` always wins.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _load_dotenv(path: Optional[Path] = None) -> None:
    """Minimal .env parser: KEY=VALUE lines, '#' comments, optional quotes."""
    p = path or Path(os.environ.get("ALERT_ENV_FILE", ".env"))
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        os.environ.setdefault(key, value)


_load_dotenv()


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def get_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ------------------------------------------------------------------ live delivery

def smtp_enabled() -> bool:
    """Live SMTP needs a COMPLETE relay config: host + user + pass + from.

    A half-filled block (host only) must NOT silently turn on real delivery —
    that is how a misconfigured deploy turns into 530 auth-error spam. Incomplete => stub.
    """
    return bool(get("ALERT_SMTP_HOST")
                and get("ALERT_SMTP_USER")
                and get("ALERT_SMTP_PASS")
                and get("ALERT_EMAIL_FROM"))


def slack_enabled() -> bool:
    return bool(get("ALERT_SLACK_WEBHOOKS"))


def slack_webhook_for(endpoint: str) -> Optional[str]:
    """Resolve a Slack channel endpoint (e.g. 'sarah.slack') to a webhook URL.

    `ALERT_SLACK_WEBHOOKS` is a JSON object mapping endpoint -> webhook URL:
        {"sarah.slack": "https://hooks.slack.com/services/T/B/X", ...}
    """
    if not slack_enabled():
        return None
    import json
    try:
        mapping = json.loads(get("ALERT_SLACK_WEBHOOKS"))
    except (ValueError, TypeError):
        return None
    return mapping.get(endpoint)


def ai_enabled() -> bool:
    """Anthropic prose layer: off unless a key (or a local mock URL) is set."""
    return bool(get("ALERT_AI_API_KEY") or get("ALERT_AI_BASE_URL"))


def ai_base_url() -> str:
    return get("ALERT_AI_BASE_URL", "https://api.anthropic.com/v1/messages")


def ai_model() -> str:
    return get("ALERT_AI_MODEL", "claude-haiku-4-5")


def ai_api_key() -> str:
    return get("ALERT_AI_API_KEY")
