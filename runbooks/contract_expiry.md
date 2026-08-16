# Runbook: Contract Expiry / Renewal

## Applies when
- metric contains: contract_expiry, days_remaining
- domain: contracts
- severity: critical, high, medium

## Playbook
1. Confirm days_remaining against the contract lifecycle system, not the alert.
2. A critical expiry (< 30 days) goes to the contracts lead for immediate renewal
   intake; a high or medium one is scheduled for the next renewal batch.
3. Check whether the vendor already has a renewal notice or an active dispute.
4. If the contract covers a critical service, open a parallel engagement with
   procurement so the renewal is negotiated before the expiry date.
5. Record the renewal owner and target signature date in the contract record.

## Owner
contracts-oncall
