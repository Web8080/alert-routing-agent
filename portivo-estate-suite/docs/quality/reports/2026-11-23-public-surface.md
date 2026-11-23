# Public Surface Test Report: portivoestatesuite.com (production)

Date: 2026-11-23
Tester: AI quality engineer (Claude), under owner authorisation
Type: unauthenticated, read-only. No logins attempted, no credentials entered, no forms submitted with data, no load applied.
Requests: roughly 60 GET/HEAD/OPTIONS requests plus about 8 headless Chromium page loads.

## Scope and limits (read first)
| Area | Tested | Notes |
|---|---|---|
| Security headers | Yes | Evidence below |
| Login page behaviour | Partly | Empty-submit validation only. No credential flows. |
| Unauthenticated access to /app and related routes | Yes | |
| Error pages | Yes | |
| Accessibility | Automated only | axe-core 4.10.2 plus keyboard tab order. Automated tools find only part of real issues. No screen reader testing. |
| TLS | NOT TESTED | This sandbox's proxy re-signs HTTPS traffic (certificate issuer was the sandbox egress gateway), so the real certificate and protocol support were not observed. Run an external scan (for example SSL Labs) for a valid result. |
| Authenticated behaviour | NOT TESTED | Login is a form session login. The saved credential cannot drive it. |
| Rate limiting, lockout, MFA | NOT TESTED | Would require login attempts. |
| Open redirect via `next=` after login | NOT TESTED | Only observable after a successful login. |
| Performance and load | NOT TESTED | Only incidental timings. |
| Browsers | Chromium only | |

Product context observed on the public page (not owner-confirmed): UK pricing (GBP 99/mo, up to 100 units), for residential landlords and letting agents; features listed include maintenance triage, compliance tracking, tenant portal, AI assistant for arrears and maintenance triage, and Xero/Slack/Zapier integrations.

Architecture observed: Next.js on Vercel; browser calls a same-origin proxy at `/api/backend/*`; the backend answers as FastAPI (uvicorn) hosted on Render; CSP also references Supabase, Sentry and an `api.` subdomain.

## Findings
Severity uses the proposed S1 to S4 model in CHARTER.md. Nothing here is S1 or S2 on the evidence available. Items marked "needs verification" are hypotheses.

| ID | Sev | Finding | Evidence | Recommendation |
|---|---|---|---|---|
| F1 | S3 | Frontend calls `/api/backend/health` on page load and it returns 404 | Browser network log on `/login`: `404 GET /api/backend/health`. Direct call returns `{"detail":"Not Found"}` | Either the route is missing or the call is dead code. Hypothesis: it is a backend wake-up or health probe. Confirm intent, fix or remove, and make sure real uptime monitoring exists. |
| F2 | S3 | CSP allows `'unsafe-inline'` and `'unsafe-eval'` in `script-src` | Response header on all routes | Weakens XSS protection even though the rest of the CSP is strong. Move to nonce-based CSP if the framework setup allows. |
| F3 | S3 | CSP `connect-src` allows a direct Render hostname and an `api.` subdomain, while the app uses the same-origin proxy | CSP header | Needs verification: if the Render backend is reachable directly it may bypass whatever Vercel-level protections apply. Remove unused origins. Direct probing of that host was out of scope. |
| F4 | S3 | `/vendor-portal/dashboard` returns the dashboard shell (200, 12KB) and relies on client-side JS to redirect to login. `/app/*` and `/tenant` redirect server-side. | curl: `/vendor-portal` -> 307 -> `/vendor-portal/dashboard` 200; browser ends on `/vendor-portal/login` | No data exposed in this test (API returned 401). Enforce route gating consistently at the edge or middleware as defence in depth. |
| F5 | S3 | Publicly reachable super-admin sign-in at `/admin` (also `/super-admin` redirects to it), advertised in robots.txt | curl and browser | Not a vulnerability by itself. Needs verification: rate limiting, lockout, MFA, alerting on admin login attempts. Consider restricting by IP or SSO. robots.txt is not a control and lists internal route names. |
| F6 | S3 | Accessibility: landing page has 29 colour-contrast failures (serious), 4 heading-order issues, 34 content items outside landmarks | axe-core on `/`. Visible example: very low-contrast small grey text including the pricing line | Fix contrast tokens in the dark theme. `/login` returned zero axe violations. |
| F7 | S4 | Page titles are not unique: `/`, `/login` and `/admin` all use the marketing title; the 404 page contains two `<title>` elements | Browser and curl | Fails WCAG 2.4.2 intent and hurts navigation and SEO. Set per-route titles. |
| F8 | S4 | Console noise: `GET /api/backend/auth/me` returns 401 on every public page | Browser console | Expected for logged-out users but pollutes error monitoring and the console. Return 200 with an unauthenticated state or suppress. |
| F9 | S4 | Cookie banner: reject option exists at the first layer (good), but "Accept all" is visually dominant, and the banner is last in keyboard tab order | Screenshot and tab order | Review against current ICO guidance on equal prominence. Consider moving banner earlier in focus order. |
| F10 | S4 | Marketing and login pages hotlink six Unsplash images, sending visitor IP and referrer to a third party before any consent | Network log: GET requests to images.unsplash.com | Self-host images and align the privacy policy. Also removes a reliability dependency. |
| F11 | S4 | `Access-Control-Allow-Origin: *` on public HTML pages; no `security.txt` | Headers; `/.well-known/security.txt` 404 | Harmless on static public pages. Make sure it is never applied to authenticated responses. Add a security.txt for vulnerability reports. |

## What passed
- HSTS (2 years, includeSubDomains, preload), `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, strict referrer policy, restrictive Permissions-Policy, `object-src 'none'`, `form-action 'self'`, `base-uri 'self'`
- HTTP redirects to HTTPS (308)
- `TRACE` blocked (403); path traversal attempt blocked (403); `/.env` and `/.git/config` return 404
- API proxy did not reflect a hostile `Origin` and returned no CORS allow headers on preflight
- Unauthenticated `/app`, `/app/*` and `/tenant` redirect to `/login?next=...`; the `next` value was not reflected in the page HTML
- Unauthenticated API returns a clean 401 JSON body (`Authentication required.`), consistently around 0.2 to 0.4 s
- Login form: fields labelled, correct `autocomplete` values, password masked, required-field validation on empty submit, no network request fired on empty submit
- No analytics or Sentry requests observed before consent choice
- No horizontal overflow at 375px and 768px; tab order on login is logical
- `/login` axe-core scan: 0 violations

## Next actions proposed
1. Owner decision: provide an authenticated test route (throwaway demo password for a browser run, or token auth) so authenticated flows can be tested.
2. Run an external TLS scan from outside this sandbox.
3. Owner to confirm whether the direct Render backend host is reachable and whether testing it is in scope (F3).
4. Engineering triage of F1 to F5 and F6.
5. Turn this probe into a scheduled regression check (headers, route gating, axe on public pages). Script is reproducible from this report's method; it is not yet committed.
