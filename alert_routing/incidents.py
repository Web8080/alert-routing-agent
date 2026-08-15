# author: Victor Ibhafidon
# date: 2026-08-15
"""Past-incident knowledge base for the agentic layer (post-decision only).

A JSON folder of past dispatch summaries used by the triage-brief agent to find
"similar incidents" — the correlation slice (BigPanda-style) done deterministically
and cheaply. Incidents are recorded AFTER a dispatch completes, never during.

    incidents/
      <incident_id>.json   {"id", "metric", "domain", "severity", "value",
                            "threshold", "context", "plan_state",
                            "final_recipient", "resolution"}

Similarity is a deterministic overlap score over metric/domain/severity (plus a
context-key bonus), so the demo and tests are hermetic: no embeddings, no LLM.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

_INCIDENTS_DIR = Path(__file__).resolve().parents[1] / "incidents"


def load_incidents(path=None) -> list[dict]:
    """Load all incident summaries from a directory of JSON files (sorted)."""
    root = Path(path) if path is not None else _INCIDENTS_DIR
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id"):
            out.append(data)
    return out


def _score(alert, inc: dict) -> int:
    """Deterministic similarity: metric + domain + severity + context keys."""
    score = 0
    for key in ("metric", "domain", "severity"):
        if str(getattr(alert, key, "")).lower() == str(inc.get(key, "")).lower():
            score += 2
    ctx = dict(alert.context or {})
    inc_ctx = dict(inc.get("context") or {})
    overlap = set(ctx) & set(inc_ctx)
    score += len(overlap)
    return score


def similar_incidents(alert, incidents: Sequence[dict], k: int = 3) -> list[dict]:
    """Top-k most similar past incidents, descending, each with a similarity
    score in [0, 1] (normalized by the max possible)."""
    scored = []
    for inc in incidents:
        raw = _score(alert, inc)
        if raw > 0:
            scored.append((raw, inc))
    scored.sort(key=lambda t: (-t[0], t[1].get("id", "")))
    if not scored:
        return []
    top = scored[0][0]
    return [
        {**inc, "similarity": round(raw / top, 2)}
        for raw, inc in scored[:k]
    ]


def record_incident(alert, plan_state: str, final_recipient: Optional[str],
                    resolution: str = "", path=None) -> Path:
    """Write a past-incident summary to the KB (opt-in at the entry point).

    Returns the file path written. The alert_id is sanitized for a filename.
    """
    root = Path(path) if path is not None else _INCIDENTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in alert.alert_id if c.isalnum() or c in "-_")
    target = root / f"{safe}.json"
    payload = {
        "id": safe,
        "metric": alert.metric,
        "domain": alert.domain,
        "severity": alert.severity,
        "value": alert.value,
        "threshold": alert.threshold,
        "context": dict(alert.context or {}),
        "plan_state": plan_state,
        "final_recipient": final_recipient,
        "resolution": resolution,
        "recorded_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return target
