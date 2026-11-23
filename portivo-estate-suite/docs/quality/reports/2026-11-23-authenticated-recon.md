# Authenticated Recon Report: portivoestatesuite.com (production, demo account)

Date: 2026-11-23
Account: provided demo user, role shown in UI as "Manager", workspace named "Portivo Trial". Whether this workspace is a disposable test org or holds real customer data is NOT yet confirmed by the owner.
Policy applied: the Smoke Test & Quality Instructions (section 2). Everything below is [LIVE-SAFE] read-only.

## What was done
- Two normal logins with the correct credentials, no retries and no wrong-password attempts. (An earlier message promised one attempt. The second login was a deliberate diagnostic step to investigate finding A2.)
- Read-only page loads of 15 desk pages and record views.
- A safeguard aborted any non-GET API request after login. Blocked writes: 0.
- Not done: any create, edit, delete, import, email send, AI query, sweep, billing action, admin or super-admin access, rate-limit or lockout probing, cross-org probing.
- No credentials, tokens or customer records are stored in this repository. Raw screenshots and session state stay in the scratch area only.

## Findings
| ID | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| A1 | S3 | Edge gate checks only that a session cookie exists, not that it is valid | `/app` with a fabricated 43-char `pmos_at` returns 200 and an app shell. With no cookie it returns 307 to login. Shell content was "Loading…" with no data. | Confirmed. Defence in depth, same class as F4. No data seen. |
| A2 | Probably a test artifact of my own setup (see Correction). Unconfirmed. | Browser-side API calls via `/api/backend/*` return 401 even with a freshly issued valid session, while the dashboard still renders portfolio figures | After a successful login: `GET /api/backend/auth/me` returns 401 both before and after page reload, with the cookie attached (cookie flags: HttpOnly, Secure, SameSite=Lax, 7 days). Same 401 with the token sent as a Bearer header via curl. No data API calls were visible from the browser, yet the dashboard shows counts. | Mechanism NOT established. |
| A3 | S3 | Every page load calls `/api/backend/health` which returns 404 | Reconfirmed on authenticated and unauthenticated loads | Same as F1 |
| A4 | Info | Observed latencies: first login about 10 s end to end; page loads 2.3 to 3.6 s to network idle | Timings from headless Chromium through a proxy | Consistent with Render cold start (P2) but not verified. Not a clean benchmark. |
| A5 | Info | Object IDs are small sequential integers (organization 15, units 130 to 134, work orders 50 to 72) | Visible in API paths | Makes the N1 ID-swap sweep high value. Nothing tested, since only one org is available to me. |

### A2 in detail (read this one)
Facts:
1. Login succeeds. `POST /api/backend/auth/login` and `POST /api/auth/session` return 200, and `pmos_at` is set.
2. `/app` with that cookie returns 200 and shows "Good morning, Manager" with portfolio counts.
3. `GET /api/backend/auth/me` returns 401 with the same cookie, before and after reload, from both the browser and curl.
4. The browser makes no data API calls to any origin.

Hypotheses, none confirmed:
- H1: Data is fetched server-side (Next.js server to backend) using the cookie, and the client-side proxy path fails to convert cookie to bearer in my session. If the proxy genuinely fails for normal users, every client-side action would fail after login. That is hard to reconcile with a working product, so I rate it unlikely.
- H2: Something specific to my test setup breaks the proxy's cookie-to-bearer conversion or IP handling (headless browser, the sandbox's outbound proxy, or an IP-bound session).
- H3: `/auth/me` is called before or independently of the session being usable and the 401 is benign, with other calls working through a path I could not observe.

Why this is not closed: confirming or ruling out H1 to H3 needs Vercel and Render logs for these request IDs, or an engineer reading the proxy handler. I cannot get there from the outside, and I will not guess. Per the working principles, an unexplained failure is not a finding to close.

## Safety observations
- Nothing in this run suggested cross-tenant exposure. It also did not test it: with a single org, isolation is untested.
- Because the workspace may contain real customer data, nothing from it was copied into the repo.

## Blocked until owner decisions
- All [TEST-ORG-ONLY] checks need disposable orgs named to the cleanup pattern and inboxes I control. I have neither.
- N1, N14, F32 and F42 (isolation and cross-portal) need at least two orgs and tokens for each portal.
- Billing (F52 to F57) needs team sign-off and ideally Stripe test mode.
- [NO-LIVE] checks (F55, F56, N22 to N30) need a non-production environment. None exists, so these stay untested and must be stated as such in release sign-offs.

## Correction to A2 (added same day)
The most likely cause of A2 is something I did, not a product defect. Earlier I saved a Basic-auth credential for this host in the session's credential vault. That vault works by having the sandbox proxy attach the saved credential to every request to the host, overwriting any `Authorization` header. If the Portivo proxy or backend sees `Authorization: Basic ...` it can reject the request as unauthenticated and ignore the session cookie. That fits every observation:
- client-side API calls with a valid session cookie return 401;
- sending my own Bearer header also returned 401 (the proxy would have overwritten it);
- the dashboard still showed data, because server-side rendering calls the backend directly and is not affected by my proxy.

Status: plausible and consistent with the evidence, but NOT confirmed. The test that would confirm it is removing the saved credential and repeating the login-and-reload check. Until that is done, A2 must not be reported to the Portivo team as a bug. The first report's A1 (cookie-presence gate) is independent and stands.
Action for owner: remove the saved "Portivo demo account" network secret for portivoestatesuite.com in the environment settings. I have no tool that can remove it.
