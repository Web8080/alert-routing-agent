# Runbook: Cold Chain Temperature

## Applies when
- metric contains: temp, freezer, cold_chain, temperature
- domain: cold_chain

## Playbook
1. Verify the sensor reading with a second probe before dispatching anyone.
2. If a defrost cycle is running, wait 10 minutes and re-read.
3. If sustained > threshold, transfer product to backup storage.
4. Escalate to quality + duty manager; product in the breach window is inspected
   for spoilage before release.

## Owner
cold-chain-oncall
