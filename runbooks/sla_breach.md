# Runbook: SLA / Response Time Breach

## Applies when
- metric contains: sla_response_time, response_time
- domain: sla
- severity: critical, high, medium

## Playbook
1. Confirm the breach window against the monitoring source of truth, not the alert.
2. A critical p99 breach means customers are already affected — escalate in
   parallel rather than waiting for an ack.
3. Check for a recent deploy or config change in the breach window.
4. If the response time is trending down, hold for recovery; if it is flat or
   rising, engage the owning service team immediately.
5. Update the SLA dashboard and file a post-incident note before closing.

## Owner
sla-ops-oncall
