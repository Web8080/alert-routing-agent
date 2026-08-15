# Runbook: Generic Alert Escalation

## Applies when
- no specific runbook matched, or metric is unknown

## Playbook
1. Acknowledge within 5 minutes (or the ack window).
2. Confirm the alert is real against the source of truth.
3. If you can fix it, fix it and document the outcome.
4. If you cannot fix it, escalate to the next ranked stakeholder — do not
   silently drop the alert.
5. When the incident is closed, write the post-incident note (see timeline).

## Owner
on-call-rotation
