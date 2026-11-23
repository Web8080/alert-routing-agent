# Portivo Estate Suite: Product Quality and Support Engineering Charter

Status: draft v0.1. Confirmed facts and assumptions are labelled.

## Confirmed by owner
- Product domain: property management.
- This is a real product, not a demo.
- The quality and support function is adapted from a product quality and support engineer role profile (customer-facing investigation, end-to-end testing, tooling and automation).

## Not yet known (do not treat as decided)
- Target users: landlords, letting agents, tenants, contractors, or a mix.
- Jurisdiction (UK assumed, unconfirmed).
- Stack, hosting, payment provider, existing code or data.
- Team size, who owns engineering, release cadence.

## Purpose
Provide an independent, end-to-end view of whether Portivo works in the real world, and close the loop between customer problems and engineering fixes. Engineers remain responsible for the quality of what they build. This function verifies it, makes failures diagnosable, and makes sure each failure is learned from once.

## Operating principles
1. Reproduce, do not just record. A bug report without a reproduction is incomplete.
2. Root cause over workaround. "Restarting fixed it" is a symptom, not an answer.
3. Every customer-reported failure becomes a regression test before the ticket closes.
4. Diagnosability is a feature. Features ship with the logs, IDs and audit trail needed to investigate them.
5. Build the tool if the task repeats. Scripts and AI-assisted triage are expected, not optional.
6. Say it straight. Report real quality, including bad news, with evidence.
7. Never claim a test passed unless it was executed and the output was seen.

## Scope of responsibility
| Area | In scope |
|---|---|
| Customer problems | Technical triage, reproduction, escalation with evidence, follow-through to resolution, systemic-vs-isolated classification |
| Release quality | End-to-end testing of releases, release readiness report, verification of fixes |
| Regression | Maintain a suite covering critical workflows, grown from every confirmed bug |
| Diagnostics | Logging standards, correlation IDs, audit trail review, support tooling |
| Automation | Scripts, test generation, AI-assisted log analysis, triage tooling |
| Out of scope | Writing production features, owning product decisions, giving legal advice |

## Support and triage process (target state)
1. Intake: capture who, which property/tenancy/account, what they did, what they expected, what happened, when (with timezone), environment.
2. Classify severity (see below).
3. Reproduce in a non-production environment with synthetic data. Never reproduce with real tenant data unless there is a documented, access-controlled need.
4. Diagnose using logs, traces and the audit trail.
5. Escalate to engineering with: reproduction steps, evidence, suspected scope, severity, customer impact.
6. Communicate with the customer at agreed intervals.
7. Verify the fix, add the regression test, close the loop with the customer.
8. Review: isolated incident or pattern? Update risk register.

## Severity model (proposed)
| Level | Definition | Examples |
|---|---|---|
| S1 | Money, legal duty or personal data affected, or system unusable | Wrong rent amount charged, deposit not protected on time, tenant data visible to another tenant, outage |
| S2 | Core workflow broken, workaround exists | Repair request cannot be raised, statements fail to generate |
| S3 | Degraded or confusing, no financial or legal effect | Notification delayed, UI glitch |
| S4 | Cosmetic | Typos, alignment |

Response targets are not set. They need owner agreement and should not be invented here.

## 30/60/90 day plan for this function
Because there is no product yet, days are measured from the first runnable build.

- Days 1 to 30, learn and break: use the product end to end as each user type; interrupt payments, uploads and sessions; review logs; write the first critical-workflow tests; list the top quality and diagnostic gaps.
- Days 30 to 60, own the loop: stand up intake and triage, reproduction environment with synthetic data, bug-to-regression-test rule.
- Days 60 to 90, scale: automate diagnostics collection, AI-assisted triage, release readiness report, dashboards.

## Definition of done for quality work
A change is not done until: acceptance criteria exist and are testable, tests were executed with results recorded, failure paths were exercised, logs and audit events exist for the feature, and docs are updated. Status labels (Designed, Implemented, Tested, and so on) are only awarded with evidence.
