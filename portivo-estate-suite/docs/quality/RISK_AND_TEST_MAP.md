# Risk-Based Test Map v0.1

Status: hypotheses to validate against real requirements. Nothing here is a confirmed feature. Legal and regulatory items are prompts for review by a qualified person, not legal advice, and current rules and commencement dates must be verified before relying on them.

Assumption: UK lettings market. Unconfirmed.

## Why risk-first
In property management the costly failures are not cosmetic. They are wrong money, missed legal duties, wrong person seeing wrong data, and lost records. Test effort goes there first.

## Critical workflow areas
| ID | Area | What can go wrong | Failure tests to design |
|---|---|---|---|
| R1 | Rent and money | Wrong amount, double charge, missed payment, rounding, proration on partial months, payment webhook replay or arriving out of order | Idempotency on payment callbacks, duplicate and delayed webhooks, partial payments, mid-month tenancy start/end, currency rounding, ledger reconciles to provider |
| R2 | Deposits | Deposit not registered or prescribed information not served within statutory time, wrong amount held, release disputes | Date arithmetic across timezones and DST, deadline alerts, state transitions, audit trail |
| R3 | Compliance documents and deadlines | Expired or missing gas safety, electrical, energy performance and other certificates; reminders not sent | Expiry calculations, reminder scheduling, behaviour when a document is replaced or deleted, missed-job recovery |
| R4 | Tenancy lifecycle and state | Invalid transitions (for example ending a tenancy with unpaid rent, overlapping tenancies on one unit), notice handling after law changes | State machine tests, property-based tests on overlapping dates, backdating, rule versioning |
| R5 | Access control and tenancy isolation | One landlord, agent, tenant or contractor seeing another's data | Cross-tenant and cross-role access tests on every endpoint and every file download, ID enumeration |
| R6 | Personal data | Excess retention, leaks in logs, exports, third-party processors | Log redaction tests, retention and deletion jobs, subject access export completeness |
| R7 | Maintenance and repairs | Request lost, wrong contractor, no status updates, emergency not escalated | End-to-end request lifecycle, SLA timers, notification delivery failure and retry |
| R8 | Documents and uploads | Interrupted upload, corrupt files, malware, wrong file attached to wrong tenancy | Interrupted and resumed uploads, size and type limits, scanning, storage integrity |
| R9 | Notifications | Not sent, sent twice, sent to wrong person, sent at wrong time | Provider outage, retries, dedupe, timezone handling |
| R10 | Integrations | Accounting, payments, referencing, identity checks fail or return unexpected data | Timeouts, partial failures, schema drift, contract tests, circuit breakers |
| R11 | Reporting and statements | Totals disagree with ledger, owner statements wrong | Independent recomputation from ledger, golden-file tests |
| R12 | Reliability | Jobs silently failing, backups untested, long-running usage degradation | Job failure alerting, restore drill, soak tests |

## Real-world conditions to simulate (replacing the device conditions in the role profile)
- Poor mobile connectivity during upload, payment, or form submission
- Browser closed or session expired mid-workflow
- Same action performed from two tabs or two users at once
- Clock and timezone edge cases: month end, leap day, DST change, tenancy starting at midnight
- Third-party outage and delayed or duplicated callbacks
- Bulk data import with dirty or partial records
- Users who do things in the wrong order

## Diagnostic standards to require from engineering
- Correlation ID on every request, job and outbound call, visible to support
- Structured logs with no personal data or secrets
- Immutable audit trail for money, deposits, document changes and permission changes
- Support view that shows entity state history without database access
- Feature flags and version recorded with every error

## First automation candidates
1. Synthetic data generator for properties, tenancies, ledgers, and edge-case dates
2. Support diagnostic bundle script (state history, recent events, versions) with redaction
3. Regression harness seeded from every confirmed bug
4. AI-assisted log summariser for triage, human-verified, run only on redacted data
5. Release readiness report generated from test results

## Open questions that change this map
1. Jurisdiction and regulated activities (does Portivo hold client money or process payments itself?)
2. Who are the users and who is the paying customer?
3. Payment, accounting and referencing providers
4. Hosting region and data processors
5. Existing code, data or customers
