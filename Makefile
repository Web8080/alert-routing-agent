# Alert Routing Agent — developer convenience targets.
# The core has no third-party dependencies; everything works with plain
# `python3`. These targets just make the common commands shorter.

.PHONY: run1 run2 run3 run4 run5 run6 run7 run-all test test-v serve ui install

## Scripted scenarios
run1: ; python3 -m alert_routing.cli scenarios/scenario_1_offline.json
run2: ; python3 -m alert_routing.cli scenarios/scenario_2_channel_fail.json
run3: ; python3 -m alert_routing.cli scenarios/scenario_3_no_downgrade.json
run4: ; python3 -m alert_routing.cli scenarios/scenario_4_simultaneous.json
run5: ; python3 -m alert_routing.cli scenarios/scenario_5_contract_expiry.json
run6: ; python3 -m alert_routing.cli scenarios/scenario_6_sla_breach_ack_timeout.json
run7: ; python3 -m alert_routing.cli scenarios/scenario_7_anomaly_score_medium.json

## Run all seven scenarios back-to-back
run-all:
	python3 -m alert_routing.cli scenarios/scenario_1_offline.json
	python3 -m alert_routing.cli scenarios/scenario_2_channel_fail.json
	python3 -m alert_routing.cli scenarios/scenario_3_no_downgrade.json
	python3 -m alert_routing.cli scenarios/scenario_4_simultaneous.json
	python3 -m alert_routing.cli scenarios/scenario_5_contract_expiry.json
	python3 -m alert_routing.cli scenarios/scenario_6_sla_breach_ack_timeout.json
	python3 -m alert_routing.cli scenarios/scenario_7_anomaly_score_medium.json

## Tests (stdlib unittest; -v for verbose)
test: ; python3 -m unittest discover -v
test-v: ; python3 -m unittest discover -v

## Zero-dependency web UI dashboard (stdlib only)
ui: ; python3 -m alert_routing.ui --port 8000

## Optional HTTP API (requires: pip install -r requirements.txt)
serve: ; python3 -m uvicorn alert_routing.server:app --reload

## Install as a real package (gives you the `alert-routing` command)
install: ; pip install -e .
