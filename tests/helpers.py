# author: Victor Ibhafidon
# date: 2026-08-14
"""Shared test helpers."""

from pathlib import Path
from unittest import mock

from alert_routing import settings
from alert_routing.models import Alert, Config
from alert_routing.presence import Presence
from alert_routing.router import Router, SimClock

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = str(ROOT / "registry.json")
SCENARIOS = ROOT / "scenarios"


def make_router(ledger_path=":memory:", config=None, presence=None, clock=None,
                online_offline=None) -> Router:
    """Router with defaults matching the demo seed (senior/offline candidates offline).

    Tests are HERMETIC: the dev `.env` may enable live delivery / AI, but the
    suite must never hit the network. We force the deterministic stub adapters
    (and no AI prose) for the duration of construction.
    """
    if presence is None:
        presence = Presence()
        presence.seed(online_offline or {"STK-003": False, "STK-006": False,
                                         "STK-007": False, "STK-010": False,
                                         "STK-011": False})
    with mock.patch.object(settings, "smtp_enabled", return_value=False), \
         mock.patch.object(settings, "slack_enabled", return_value=False):
        return Router(REGISTRY, ledger_path, config=config, clock=clock or SimClock(),
                      presence=presence)


def make_alert(metric="stock_level", value=12, threshold=20, severity="HIGH",
               domain="inventory", context=None, alert_id="test-alert") -> Alert:
    return Alert(alert_id=alert_id, metric=metric, value=value, threshold=threshold,
                 severity=severity, domain=domain, context=context or {},
                 ts=SimClock().now())
