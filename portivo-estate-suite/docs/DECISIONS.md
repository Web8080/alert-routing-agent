# Product and Engineering Decision Register

| ID | Date | Category | Decision | Status | Source | Reversal conditions |
|---|---|---|---|---|---|---|
| D-001 | 2026-11-23 | Product | Domain is property management | Confirmed | Owner | Owner changes scope |
| D-002 | 2026-11-23 | Product | Portivo is a real product, not a portfolio demo | Confirmed | Owner | Owner changes intent |
| D-003 | 2026-11-23 | Process | Quality and support function is modelled on a product quality and support engineer role profile, adapted to software only | Confirmed | Owner | None |
| D-004 | 2026-11-23 | Repo | Portivo should live in its own repository, not inside alert-routing-agent. Currently a subdirectory. | Proposed, not approved | Engineering advice | Owner prefers monorepo, accepting shared CI and release coupling |
| D-005 | 2026-11-23 | Assumption | UK jurisdiction | Likely, still unconfirmed (public site prices in GBP, targets landlords and letting agents) | Observed on production site | Owner confirms another jurisdiction |
| D-006 | 2026-11-23 | Testing | SUPERSEDED by D-009. Originally: production demo account tested read-mostly. | Superseded | Owner | n/a |
| D-007 | 2026-11-23 | Security | Credentials go through the session credential vault only, never chat. Vault cannot drive form logins. | Confirmed | Engineering | Token auth or SSO test path is provided |
| D-008 | 2026-11-23 | Process | First public-surface report filed at docs/quality/reports/2026-11-23-public-surface.md | Done | Engineering | None |
| D-009 | 2026-11-23 | Testing | Production is live. The Smoke Test & Quality Instructions govern all testing: [LIVE-SAFE] reads only on orgs I did not create; every write, send, AI call, billing action needs a disposable QA org named to the cleanup pattern; [NO-LIVE] checks never run on production. | Adopted per owner document | Owner | Owner revises the document or a non-production environment exists |
| D-010 | 2026-11-23 | Testing | Demo workspace 'Portivo Trial' (org 15) is treated as potentially real customer data until the owner confirms it is disposable. Read-only only. | Open, awaiting owner confirmation | Engineering | Owner confirms it is a disposable test org |
| D-011 | 2026-11-23 | Quality | Finding A2 (client-side API returns 401 after valid login while dashboard renders) is unresolved. Needs Vercel/Render/Sentry log access or an engineer. | Open | Engineering | Mechanism identified |
| D-012 | 2026-11-23 | Security | Demo password was shared in chat. Must be rotated when testing ends. | Open, owner action | Engineering | Password rotated |

Flags: D-004 introduces repo-coupling debt if left as is. D-005 drives compliance scope and must be confirmed before requirements are written.
