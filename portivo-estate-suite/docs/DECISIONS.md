# Product and Engineering Decision Register

| ID | Date | Category | Decision | Status | Source | Reversal conditions |
|---|---|---|---|---|---|---|
| D-001 | 2026-11-23 | Product | Domain is property management | Confirmed | Owner | Owner changes scope |
| D-002 | 2026-11-23 | Product | Portivo is a real product, not a portfolio demo | Confirmed | Owner | Owner changes intent |
| D-003 | 2026-11-23 | Process | Quality and support function is modelled on a product quality and support engineer role profile, adapted to software only | Confirmed | Owner | None |
| D-004 | 2026-11-23 | Repo | Portivo should live in its own repository, not inside alert-routing-agent. Currently a subdirectory. | Proposed, not approved | Engineering advice | Owner prefers monorepo, accepting shared CI and release coupling |
| D-005 | 2026-11-23 | Assumption | UK jurisdiction | Unconfirmed assumption | Inferred | Owner confirms another jurisdiction |

Flags: D-004 introduces repo-coupling debt if left as is. D-005 drives compliance scope and must be confirmed before requirements are written.
