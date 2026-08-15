# Runbook: Inventory / Stock Level

## Applies when
- metric contains: stock_level, reorder_level, inventory
- domain: inventory

## Playbook
1. Confirm the value against the source of truth (warehouse system), not the alert.
2. Check for duplicate or stale feeds — re-query before acting.
3. If stock_level < reorder_level, trigger a replenishment order (owner: procurement).
4. If a reorder is already in flight, no action needed — mark as noise.
5. Escalate to duty manager if the SKU is a top-20 revenue item and level is critical.

## Owner
inventory-ops-oncall
