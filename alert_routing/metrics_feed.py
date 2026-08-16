#!/usr/bin/env python3
# author: Victor Ibhafidon
# date: 2026-08-15
"""Live metrics feed — pushes real metric values into the routing API/CLI.

Simulates a warehouse telemetry stream so the walkthrough is driven by changing data,
not just a one-shot script. Points at the FastAPI server by default:

    python -m alert_routing.metrics_feed --interval 2

It POSTs a metric when its value crosses the threshold, then continues feeding
so a later breach (or recovery) fires another alert. Set `--cli scenario.json`
to drive a scripted scenario instead of the HTTP API.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Optional

SENSORS = [
    {"metric": "stock_level", "domain": "inventory", "threshold": 20,
     "initial": 38.0, "slope": -1.8, "context": {"warehouse": "WH-4", "sku": "ACME-100"}},
    {"metric": "freezer_temp_c", "domain": "cold_chain", "threshold": -18.0,
     "initial": -24.0, "slope": 0.9, "context": {"zone": "FREEZER-A"}},
    {"metric": "cpu_utilization", "domain": "compute", "threshold": 85.0,
     "initial": 40.0, "slope": 4.2, "context": {"host": "node-07"}},
    {"metric": "contract_expiry", "domain": "contracts", "threshold": 30,
     "initial": 60.0, "slope": -2.1, "direction": "below",
     "context": {"vendor": "Northwind", "contract": "CT-1042"}},
    {"metric": "sla_response_time", "domain": "sla", "threshold": 500.0,
     "initial": 300.0, "slope": 18.0, "direction": "above",
     "context": {"service": "billing-api", "window": "p99"}},
    {"metric": "anomaly_score", "domain": "anomaly", "threshold": 0.9,
     "initial": 0.2, "slope": 0.06, "direction": "above",
     "context": {"service": "checkout", "model": "isolation_forest"}},
]


def post_alert(url: str, sensor: dict, value: float) -> bool:
    payload = {
        "metric": sensor["metric"],
        "value": round(value, 2),
        "threshold": sensor["threshold"],
        "severity": "HIGH",
        "domain": sensor["domain"],
        "context": sensor["context"],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read())
    print(f"[feed] {sensor['metric']}={payload['value']:.2f} (threshold "
          f"{sensor['threshold']}) -> alert_id {body.get('alert_id')}")
    return True


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/alert")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--steps", type=int, default=20,
                        help="feed steps before stopping (default 20)")
    parser.add_argument("--cli", default=None,
                        help="run a scripted scenario file instead of HTTP")
    args = parser.parse_args(argv)

    if args.cli:
        from .cli import run_scenario
        print(f"[feed] driving scripted scenario {args.cli}")
        router, aid = run_scenario(args.cli, "registry.json", ":memory:")
        for line in router.trace:
            print(f"{line.ts} {line.kind.upper():7s} {line.text}")
        return

    print(f"[feed] posting to {args.url} every {args.interval}s "
          f"(Ctrl-C to stop)")
    crossed = {s["metric"]: False for s in SENSORS}
    t = 0.0
    try:
        for _ in range(args.steps):
            for sensor in SENSORS:
                value = sensor["initial"] + sensor["slope"] * t
                if sensor.get("direction") == "above":
                    breached = value >= sensor["threshold"]
                else:
                    breached = value <= sensor["threshold"]
                if breached and not crossed[sensor["metric"]]:
                    post_alert(args.url, sensor, value)
                    crossed[sensor["metric"]] = True
            t += 1.0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[feed] stopped")


if __name__ == "__main__":
    main()
