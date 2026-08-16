# Runbook: Anomaly Score Breach

## Applies when
- metric contains: anomaly_score, score, outlier
- domain: anomaly
- severity: critical, high, medium

## Playbook
1. Pull the raw features behind the anomaly score before acting — the model can
   flag a genuine incident, a stale feed, or a newly deployed traffic pattern.
2. A critical score with high model confidence warrants a human check even when
   the metric looks normal on the dashboard.
3. Replay the model over the last 60 minutes to see if the outlier is sustained.
4. If the score is a one-off spike with no operational impact, mark it noise;
   if it recurs across services, escalate to the observability engineer.
5. Document the feature window so the model owner can review the detection.

## Owner
observability-oncall
