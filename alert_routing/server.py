# author: Victor Ibhafidon
# date: 2026-08-14
"""Optional HTTP API (FastAPI). NOT part of the core.

The core never imports this module. Install FastAPI/uvicorn only if you want the
API surface:  pip install fastapi uvicorn
Then:             python -m uvicorn alert_routing.server:app
"""

from __future__ import annotations

from typing import Optional

from .cli import run_scenario
from .ledger import Ledger
from .models import Alert
from .timeline import render_timeline

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - optional dependency
    FastAPI = None
    BaseModel = object
    Field = lambda *a, **k: None  # noqa: E731


class AlertIn(BaseModel):
    metric: str
    value: float
    threshold: float
    severity: str
    domain: str
    context: dict = {}
    alert_id: Optional[str] = None


def build_app(registry_path: str = "registry.json",
              ledger_path: Optional[str] = None,
              store=None) -> object | None:
    """Build the FastAPI app.

    Uses ONE durable ledger shared by every request so ingest → query round-
    trips work (a fresh :memory: DB per request would lose state). If no
    ledger_path is given, a temporary file is created for the app's lifetime."""
    if FastAPI is None:
        return None
    import tempfile as _tempfile
    if ledger_path is None:
        _tmp = _tempfile.NamedTemporaryFile(prefix="alert_ledger_", suffix=".db",
                                            delete=False)
        ledger_path = _tmp.name
        _tmp.close()
    app = FastAPI(title="Alert Routing Agent", version="1.0.0")

    @app.post("/alert")
    def ingest(payload: AlertIn) -> dict:
        alert_id = payload.alert_id or _stable_alert_id(payload)
        alert = Alert(alert_id=alert_id, metric=payload.metric, value=payload.value,
                      threshold=payload.threshold, severity=payload.severity,
                      domain=payload.domain, context=payload.context, ts="http")
        stakeholders = None
        if store is not None:
            stakeholders = store.load()
        _, aid = run_scenario_from_alert(alert, registry_path, ledger_path,
                                         stakeholders=stakeholders)
        ledger = Ledger(ledger_path)
        return {"alert_id": aid, "plan_state": ledger.plan_state(aid),
                "notifications": ledger.notifications_for(aid),
                "decisions": ledger.decision_log(aid)}

    @app.get("/alerts/{alert_id}")
    def get_alert(alert_id: str) -> dict:
        ledger = Ledger(ledger_path)
        return {"alert_id": alert_id, "plan_state": ledger.plan_state(alert_id),
                "notifications": ledger.notifications_for(alert_id),
                "decisions": ledger.decision_log(alert_id)}

    @app.get("/alerts/{alert_id}/timeline")
    def get_timeline(alert_id: str) -> dict:
        ledger = Ledger(ledger_path)
        return {"alert_id": alert_id, "timeline_text": render_timeline(ledger, alert_id)}

    return app


def _stable_alert_id(payload: AlertIn) -> str:
    """Deterministic, cross-process alert id derived from the payload.

    (NOT `hash()` — Python str hashing is randomized per process, which would
    break the 'same input → same id' property and idempotent re-ingest.)"""
    import hashlib
    import json as _json
    canonical = _json.dumps(
        {"metric": payload.metric, "value": payload.value,
         "threshold": payload.threshold, "severity": payload.severity,
         "domain": payload.domain, "context": payload.context},
        sort_keys=True, separators=(",", ":"))
    return "http-" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def run_scenario_from_alert(alert: Alert, registry_path: str,
                            ledger_path: str,
                            stakeholders=None) -> tuple[object, str]:
    """Run a single-alert dispatch (no scripted events → completes immediately)."""
    import tempfile
    import json as _json
    from pathlib import Path

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        _json.dump({"alert": {
            "metric": alert.metric, "value": alert.value, "threshold": alert.threshold,
            "severity": alert.severity, "domain": alert.domain,
            "context": alert.context, "alert_id": alert.alert_id},
            "presence": {}, "steps": []}, fh)
        tmp = fh.name
    try:
        from .ai import make_notification_prose
        return run_scenario(tmp, registry_path, ledger_path, alert_id=alert.alert_id,
                            prose=make_notification_prose(),
                            stakeholders=stakeholders)
    finally:
        Path(tmp).unlink(missing_ok=True)


app = build_app()
