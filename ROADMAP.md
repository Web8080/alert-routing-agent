# ROADMAP — Alert Routing Agent

> **READ THIS FIRST.** This file is the single source of truth for project state.
> It is updated at the end of every phase. If you are a new chat session, read
> this file before doing anything else — it tells you exactly what exists, what
> changed, what decisions were made and why, and precisely what remains.

---

## ⚠️ HANDOFF PROTOCOL (MANDATORY — READ BEFORE ANY WORK)

Every session working on this project **must** follow this protocol:

1. **Start:** Read this file (`ROADMAP.md`) in full, plus `BLUEPRINT.md` (the
   design spec) if you need depth on any decision.
2. **Work:** Complete work in the order of the Roadmap (next phase = first
   `[ ]` item in the current phase section).
3. **Before switching phases or ending the session:** update this file:
   - Move completed items to "Done" (mark `[x]`).
   - Record **what changed** under the active phase's "Phase notes".
   - Record **tradeoffs/decisions made** in the active phase.
   - Update the "Remaining Roadmap" section.
   - Add any new instructions for the next session under "Next session".
4. **Self-trigger rule:** after finishing any phase, automatically update this
   file *before* starting the next phase. If you anticipate hitting context or
   usage limits, update this file **first**, then continue. If you cannot finish
   a phase, update this file with an honest "stopped mid-phase at X" note.
5. **Never delete** history here. Append. A future session must be able to
   reconstruct the entire story from this file alone.

---

## 1. PROJECT SUMMARY

Automated alert-routing agent. An alert event (metric,
value, threshold, severity) is ranked against a stakeholder registry by domain
expertise and seniority; the best *available* candidate is dispatched via their
preferred channel; and when availability/channel health/the qualified population
changes **mid-flight**, the system re-routes or escalates in parallel —
guaranteeing: **no duplicate notifications, no double-querying availability,
no downgrade to a less-qualified person, full alert context preserved.**

Deliverables: a public GitHub repo, a ≤3-minute walkthrough video, and a README
explaining how to run it + what's next.

### Core decisions (locked, rationale in BLUEPRINT.md §12)
- **Python 3, standard-library only** core (`sqlite3`, `dataclasses`, `json`, `argparse`, `threading`).
- **Deterministic policy engine**, no LLM in the routing hot path.
- **SQLite ledger** = source of truth (check-then-claim dedup).
- **Event-driven change detection** — availability is evaluated **once** per
  stakeholder per dispatch (schema-enforced `PRIMARY KEY (alert_id, stakeholder_id)`).
- **Qualification-first ranking** (`expertise × seniority_weight`); availability/on-call are gates, never rank keys.
- **`MIN_REROUTE_DELTA`** (default 1.5): only interrupt/escalate to a better
  candidate if `candidate_q - current_q >= delta`.
- **Delivery-receipt discriminator**: abort+reroute only while un-acked;
  acked (esp. email) → complete + escalate in parallel.
- Channel prefs are `[{name, priority, endpoint}]`; planner builds the fallback chain.
- Escalation cap = 3. Ack timers for HIGH/CRITICAL.

---

## 2. BUILD STATUS (at a glance)

| Area | Status |
|---|---|
| BLUEPRINT.md (15,247 words + §21 addendum) | ✅ Done |
| models / registry / ranker / snapshotter | ✅ Done |
| planner / ledger / presence / channels / changes | ✅ Done |
| decision policy (R1–R6 + delta) | ✅ Done |
| router / CLI / timeline UI | ✅ Done |
| server.py (optional FastAPI) | ✅ Done |
| scenario JSON files | ✅ Done |
| registry.json seed | ✅ Done |
| tests (101 passing) | ✅ Done |
| README.md (+ TOC) | ✅ Done |
| Packaging (pyproject + entry point + Makefile) | ✅ Done |
| author/date headers on all scripts | ✅ Done (23 files) |
| Web UI dashboard (stdlib-only) | ✅ Done |
| Dashboard: hybrid 3-view (Console/Policy/Registry) | ✅ Done |
| Editable registry (CRUD via UI + API) | ✅ Done |
| On-call roster (roster.py + roster.json shifts) | ✅ Done |
| Run + verify all 7 scenarios | ✅ Done |
| Incident timeline verified | ✅ Done |
| Public repo (github.com/Web8080/alert-routing-agent) | ✅ Done |
| Walkthrough video (≤3 min) | ⏳ Not started |

---

## 3. ROADMAP — PHASES

### Phase 1 — Design spec ✅ DONE
- [x] `BLUEPRINT.md` written (>10,000 words: 14,740).
- [x] Decision on stack (Python stdlib), deterministic core, SQLite ledger,
      event-driven detection, MIN_REROUTE_DELTA, ack timers, 3 scripted scenarios.

**Phase 1 notes / decisions:** See section 1. Detailed design feedback (channel
prefs as priority list, MIN_REROUTE_DELTA, don't over-build infrastructure,
scenario structure) was incorporated.

### Phase 2 — Core modules ✅ DONE
- [x] `alert_routing/models.py` — dataclasses: Alert, Stakeholder, ChannelPref,
      SnapshotEntry, Plan (+ step_index cursor), RouteStep, Notification,
      ChangeNotice, Verdict, Config, LedgerView, TraceLine.
- [x] `alert_routing/registry.py` — JSON loader + validation.
- [x] `alert_routing/ranker.py` — qualification = expertise × (1+(seniority−1)×0.15).
- [x] `alert_routing/snapshotter.py` — one-time eval, re-eval raises.
- [x] `alert_routing/ledger.py` — SQLite, check-then-claim, I1/I2 dedup guards,
      decision_log with UNIQUE(alert_id, seq).
- [x] `alert_routing/presence.py` — presence sim + synchronous event emitter.
- [x] `alert_routing/channels.py` — email/slack/sms stubs (email = fire-and-forget;
      slack/sms = presence-aware; DOWN ⇒ RETRIABLE).
- [x] `alert_routing/changes.py` — change detector (diff vs snapshot, never re-query).
- [x] `alert_routing/decision.py` — policy R1–R6 + MIN_REROUTE_DELTA + R4c ack-timeout.
- [x] `alert_routing/router.py` — orchestrator: dispatch/on_event/acknowledge/
      evaluate_ack_timeout/_apply_verdict, body composition with "why you".
- [x] `alert_routing/cli.py` — scenario driver + trace printing.
- [x] `alert_routing/timeline.py` — **incident timeline UI** (renders
      decision_log + notifications + final message + why-you). Added per user request.

**Phase 2 notes / decisions:**
- Plan route is built once and immutable except a `step_index`/`channel_index`
  cursor — rebuilding on events would re-open the downgrade bug.
- Notification id = `alert_id:sid:channel:l{level}` (deterministic → replayable).
- `delivered_sids` = DELIVERED/ESCALATED only; `notified_sids` includes INTENT
  (for I1/I2 no-second-notification guard).
- R4c ack-timeout is evaluated at control points (`evaluate_ack_timeout()`);
  a background thread is intentionally NOT in the core to keep the walkthrough
  deterministic. If real timers are needed later, wrap in a daemon thread
  calling `router.evaluate_ack_timeout()`.
- **Known minor wart (fix in Phase 5):** `router._start_send_for` imports
  `ChangeType`/`DetectedChange` inline; fine but move to top-level import.

### Phase 3 — Optional HTTP API ✅ DONE
- [x] `alert_routing/server.py` — optional FastAPI `POST /alert`, `GET /alerts/{id}`, `GET /alerts/{id}/timeline`.
- [x] Guarded import (`try: from fastapi import ...`) so core stays stdlib.
- [x] NOT part of the core; README documents `pip install fastapi uvicorn` (optional).

**Phase 3 notes:** server builds a temp scenario JSON and reuses `run_scenario`
— no duplicated logic. `app = build_app()` at module import creates the app;
if FastAPI is absent, `build_app()` returns None (module still imports cleanly).

### Phase 4 — Seed data + scenarios ✅ DONE
- [x] `registry.json` — 7 stakeholders. Sarah Chen (STK-001, inventory 5, sen 3,
      q=6.50, primary), David Miller (STK-002, inventory 4, sen 4, q=5.80, backup),
      Elena Ross (STK-003, sen 5, inventory 2, q=3.20 → no-downgrade case),
      Frank Dubois (STK-004), Grace Lin (STK-005), Priya Nair (STK-006, q=7.25,
      delta-gate marginal), Maya Khan (STK-007, q=8.00, delta-gate pass).
      STK-003 offline+off-call; STK-006/007 online-in-absentia (on-call but
      offline at snapshot → they come online mid-flight for R4 tests).
- [x] `scenarios/scenario_1_offline.json` — stock alert HIGH → Sarah via Slack →
      Sarah offline mid-flight → R2 abort+reroute → David email.
- [x] `scenarios/scenario_2_channel_fail.json` — Slack DOWN mid-flight → R1 retry
      same recipient via email (recipient unchanged).
- [x] `scenarios/scenario_3_no_downgrade.json` — Elena (senior 5, q 3.20) comes
      online vs Sarah (q 6.50) → R5 IGNORE, Sarah stays primary.

**Phase 4 notes:**
- Channel prefs are `[{name, priority, endpoint}]`; planner builds the fallback
  chain (never embeds channel logic in dispatcher).
- `Presence.seed_defaults()` fills unlisted channels with OK + default online=True
  at router init (no events fired), so scenarios only override what matters.
- Duty-manager fallback added in router for unknown domains (E8): if top
  qualification == 0 and `duty_manager_ids` configured, route to the duty
  manager instead of an arbitrary tie-broken candidate.

### Phase 5 — Tests ✅ DONE (29 passing)
- [x] `tests/test_ranker.py` — determinism, qualification order, expertise>seniority.
- [x] `tests/test_snapshot.py` — single-eval IntegrityError (physical), gating,
      frozen scores for gated candidates.
- [x] `tests/test_dedup.py` — claim-twice rejected, I2 escalation-to-same-person
      rejected, cancelled-slot semantics (UNIQUE key stays consumed; R1 retry on
      a different channel IS allowed), parallel alerts both deliver.
- [x] `tests/test_decision.py` — R1, R2, R3 (acked→escalate), R4a/R4b (delta gate:
      Priya marginal → IGNORE, Maya ≥1.5 → reroute/escalate), R5 (incl. after a
      reroute), R4c timeout (+no double-escalate, +not armed for MEDIUM),
      unknown-domain→duty-manager, R6 cap, terminal guard.
- [x] `tests/test_scenarios.py` — run the 3 scenario JSONs; assert terminal
      state, invariants (no-dup, single-eval), context preserved, replay
      determinism.
- [x] `tests/test_timeline.py` — incident timeline renders decision spine +
      final recipient + message-as-sent.
- [x] Run: `python -m unittest discover` → **OK (29 tests)**.

**Phase 5 notes / BUGS FOUND & FIXED:**
1. **Parallel-escalate-after-ack was unreachable.** `acknowledge()` originally
   set the plan straight to terminal `DELIVERED`, so events arriving after an
   ack (R3/R4b: "recipient offline after email already sent") were dropped.
   Fix: `acknowledge()` marks the notification DELIVERED but leaves the plan
   `SENDING`; a new `close()` finalizes the plan (DELIVERED or ESCALATED) at the
   end of the dispatch session. This is exactly the R3/R4b scenario the brief
   calls "complete the original dispatch and escalate in parallel."
2. **Stale-snapshot adapter bug (R4a).** A candidate who came online (event)
   was still `online=False` in the frozen snapshot, so Slack/SMS adapters
   returned RETRIABLE forever. Fix: `_apply_event_to_snapshot()` folds the
   event's truth into the snapshot (a *diff*, not a re-query — the no-double-
   query guarantee only forbids reading presence again). Adapters and reroute
   logic now see the candidate as online.
3. **Duty-manager route only when no experts exist** — avoids arbitrary
   tie-broken routing on unknown domains.


### Phase 6 — README + repo + walkthrough video 🔄 IN PROGRESS
- [x] `README.md` — run instructions, architecture, project narrative, what's next.
- [x] `LICENSE` — MIT (© 2026 Victor Ibhafidon).
- [x] **Author + date headers on every script** — `# author: Victor Ibhafidon` /
      `# date: 2026-08-14` prepended to all 23 `.py` files (alert_routing/ + tests/).
- [x] **Run-anywhere packaging** — `pyproject.toml` (installable, `alert-routing`
      console command, zero deps, metadata author), `requirements.txt` (optional
      extras only), `Makefile` (run1…run7/run-all/test/serve/install).
      Verified end-to-end in a throwaway venv: `pip install -e .` → `alert-routing`
      + `python -m unittest discover` both green with NO third-party deps.
- [x] **Web UI (built)** — `alert_routing/ui.py` + `static/` (index.html,
      style.css, app.js, favicon.svg), zero deps, stdlib `http.server`,
      `make ui` target. Reuses `run_scenario_data`/`render_timeline` — no
      routing logic in the UI. Verified end-to-end via curl (all endpoints +
      static assets return 200; dispatches return correct JSON).
- [x] `gh repo create` under `github/web8080` (public) — live at
      `github.com/Web8080/alert-routing-agent`; verified in logged-out window.
- [ ] 3-minute walkthrough video per the script in BLUEPRINT.md §14:
      0:00–0:20 architecture → 0:20–1:20 scenario 1 (offline→reroute) →
      1:20–2:10 scenario 2 (channel fail→fallback) → 2:10–2:45 scenario 3
      (senior appears, lower qualification → no downgrade) → 2:45–3:00 tests +
      incident timeline.
- [ ] Upload unlisted, "anyone with link"; verify in logged-out window.
- [ ] Reply to email with repo + video links.

**Phase 6 notes — WEB UI (BUILT — refined per feedback):**
- **Stack:** stdlib-only, keeps the "zero deps" promise. `alert_routing/ui.py`
  serves `static/` via `http.server` + a tiny JSON API
  (`GET /api/scenarios`, `GET /api/registry`, `POST /api/dispatch`). Dispatch
  supports bundled scenarios AND custom alerts. `cli.py` gained
  `run_scenario_data(data, ...)` so CLI and UI share one code path.
- **Layout (dark ops-console, Grafana-meets-PagerDuty):**
  - Left: ALERT panel (ingress KV + custom JSON form) + **DISPATCH STATE**
    (state machine: RECEIVED→RANKED→PLANNED→DISPATCHING→CHANGE DETECTED→
    POLICY DECISION→RESULT, lights up as trace plays) + event stream.
  - Center: **DISPATCH TRACE** (animated, pause/step/replay/speed) +
    **STAKEHOLDER RANKING** table (qual, availability dot, GATED tag, live
    selected-recipient highlight, final statuses).
  - Right: **DECISION** card (last rule: code/action/target/rationale/result +
    full decision list) + **NOTIFICATION LEDGER** + **INCIDENT TIMELINE**.
  - Bottom: **POLICY MATRIX** R1–R6 chips that light when a rule fires.
  - Header: ☰ registry modal (read-only stakeholders, read from
    `/api/registry`).
- **Design principle (from review):** the UI is a *thin operator console*, not a
  SaaS product. Its only job is to make five things undeniable:
  who was selected, why, what changed mid-flight, why the agent
  rerouted/escalated/continued, and how we know nothing was duplicated or lost.
- **Custom-dispatch nicety:** unknown domains now default the duty manager to
  the highest-seniority on-call stakeholder (was: arbitrary tie-break).
- **Hard-hardening pass (bugs found & fixed):**
  - *server.py determinism bug:* alert ids were `hash(metric)` — Python str
    hashing is randomized per process, so the same alert got different ids
    across restarts. Fixed with a SHA-1 of the canonicalized payload
    (`sort_keys=True`). Same determinism class as P5.
  - *server.py state-loss bug:* every request built its own `Ledger(":memory:")`,
    so ingest → query returned `plan_state: null`. Fixed: one durable temp-file
    ledger shared for the app's lifetime.
  - *cli.py alert-id regression* (from the `run_scenario_data` refactor):
    scenario ids silently became `alert-dispatch` instead of
    `alert-{scenario_stem}`. Restored in `run_scenario`.
  - **TESTING.md added** — a step-by-step guide mapping each of the five claims
    (no-loss, no-dup, no-double-query, no-downgrade, determinism) to a concrete
    command the reviewer can run. README links to it.
- **Caveat-closing pass (P1 + P5):**
  - *P1 durable-by-default:* CLI default ledger changed from `:memory:` to a
    durable temp file (`alert_routing/cli.py`) — a crash can no longer lose the
    alert; the file survives and the timeline re-renders. Explicit
    `--ledger PATH` still gives cross-run persistence; `--ledger :memory:` is
    now the *opt-in* throwaway mode. TESTING.md §3 P1 updated.
  - *P5 clock-injection enforcement:* `Router.evaluate_ack_timeout` (the only
    wall-clock-dependent path, R4c) now raises `RuntimeError` if the clock is a
    `SystemClock` instead of silently depending on the wall clock
    (`alert_routing/router.py`). All shipped entry points inject `SimClock`, so
    the R4c path is deterministic and now *cannot* silently degrade. TESTING.md
    §3 P5 updated with the enforcement check.

**Phase 7 — live delivery + AI prose (BUILT, env-gated):**
- **Live email** (`channels.RealEmailAdapter`) via stdlib `smtplib`; SMTP
  acceptance = ACK (R3 semantics), transport errors = RETRIABLE → fallback.
  Env: `ALERT_SMTP_*`. Addresses come from `registry.json` `channels[].endpoint`.
- **Live Slack** (`channels.RealSlackAdapter`) via stdlib `urllib` + incoming
  webhooks; `ALERT_SLACK_WEBHOOKS` is a JSON map of endpoint → webhook. A
  channel without a wired webhook is RETRIABLE (honest, never a faked ACK).
- **AI prose layer** (`ai.py`, Anthropic via stdlib `urllib`, model
  `claude-haiku-4-5`): human notification body + `--summary` incident summary,
  invoked ONLY after the deterministic decision. Deterministic template
  fallback on any failure. **Injected** via `Router(prose=...)` from the CLI/
  server entry points — the routing core never touches the network (tests stay
  hermetic; P5 intact).
- **`metrics_feed.py`** — simulated telemetry streams metric values until each
  threshold crosses, POSTing into the running FastAPI server.
- **`propose_scenario.py`** — LLM proposes candidate scenarios; the invariant
  suite ADOPTS/REJECTS (clean run + P2 no-dup + P5 reproducibility). Adopted
  example shipped in `scenarios/proposed/`.
- **Zero-dep promise held:** SMTP/HTTP/Anthropic all via stdlib; `fastapi`
  stays an optional extra. `.env` gitignored; `.env.example` is the safe template.
- **CI added** (`.github/workflows/ci.yml`): unit tests, cross-seed determinism,
  all-scenarios-clean. `Dockerfile` added for run-anywhere.
- **Runbook retrieval (post-decision "RAG" slice)** — `runbooks/*.md` corpus +
  deterministic keyword scorer (`runbooks.py`, stdlib, no embeddings). Retrieval
  runs only AFTER the decision and feeds the incident summary; the routing path
  never touches runbooks. Determinism preserved even with AI on.
- **Dashboard sidebar nav** — dedicated left-sidebar pages
  (Alert/Trace/Ranking/Decision/Ledger/Timeline/Policy), one view at a time,
  with the plan-state pill + registry in the sidebar footer.

### Phase 8 — Dashboard refinement: hybrid 3-view + editable registry + on-call roster ✅ DONE
- [x] **Hybrid 3-view layout** replaces the single-page stack with a left-sidebar
      nav — **Console / Policy / Registry** (commit 2b1dcd1).
- [x] **Console** — live dispatch in one screen: alert panel + custom-alert form,
      state machine, animated trace (step/pause/replay/speed), ranking table,
      decision card, notification ledger, incident timeline, R1–R6 policy matrix,
      AI incident summary + runbook note with a toggle.
- [x] **Policy** — R1–R6 rule matrix + full decision log + AI summary.
- [x] **Registry** — CRUD over the live registry (add/edit/delete stakeholders,
      per-stakeholder channels + expertise) + on-call shift calendar.
- [x] **`roster.py` + `roster.json`** — on-call shifts (date range, primary +
      backups). `effective_on_call()` = union of primaries/backups on shifts
      covering the day; falls back to static registry `on_call` flags when no
      shift covers the day (backward compatible with the plain registry).
- [x] **Roster-aware dispatch** — `ui.py` `_on_call_overrides()` hands the
      effective on-call map to every dispatch (scenario + custom).
- [x] **Registry/roster JSON API** — `POST /api/registry` (upsert),
      `DELETE /api/registry/{sid}`, `POST /api/registry/{sid}/on-call`,
      `POST/DELETE /api/roster`; shared `_lock` serializes concurrent writes.
- [x] **New tests (72 total)** — `test_roster.py` (shift validation, covering
      days, effective on-call), `test_registry_edit.py` (parse/save/upsert/
      on-call/delete round-trip), `test_runbooks.py` (runbook scorer),
      `test_live_delivery.py` (live adapters, env-gated). Still stdlib
      `unittest` (`python3 -m unittest discover`), also passes under pytest.
- [x] **Dashboard screenshot** in README intro (`dashboard.png`, commit bdd9c6b).

**Phase 8 notes / decisions:**
- The earlier one-page stack + left-sidebar "dedicated pages" (Phase 6/7 notes)
  is superseded by the hybrid 3-view layout. Console keeps the dense ops-console
  feel; Policy and Registry are separate table/CRUD views. No routing logic lives
  in the front-end — the UI only renders `/api/*` JSON.
- On-call is a **soft gate** (same as the static flag): the roster resolves who is
  on call *today*, but the invariant tests still own the guarantees — the roster
  never changes rank ordering, only the gating set.
- Roster semantics kept intentionally small (flat date-range shifts, no
  recurrence/ical yet) — that is the future-work item, not this phase's job.
- Registry edits are validated through the existing `parse_stakeholder` /
  `validate_shift` paths, so a corrupt entry fails fast with a named error
  (E19 preserved).

### Phase 9 — Agentic layer: two-lane AI, read-only agents (§22) ✅ DONE
- [x] **Design first** — BLUEPRINT §22 "AI / Agentic Layer — Design (two-lane
      architecture)": deterministic routing kernel (Lane 1) + advisory AI (Lane 2)
      that is *structurally incapable* of routing; do-not-add table; eval plan
      (retrieval recall@k + safety gate); product story.
- [x] **`agents.py`** — three read-only agents run AFTER the final decision:
      `TriageAgent` (runbook + incident-KB retrieval → strict-JSON brief),
      `CommsAgent` (status draft), `PostmortemAgent` (incident summary).
      `supervise()` orchestrates with a time budget, a per-agent deterministic
      fallback, and an audit trail (`agents: [{name, ok, latency_ms, fallback}]`).
- [x] **Honesty invariant** — `mode` and the audit trail always reflect the
      brief's *real* source: `_parse_brief` raises on malformed JSON so a
      parse failure records a true fallback (never "AI live" with fallback
      content); tolerant JSON extraction (`{...}`) + 800-token budget keep live
      responses parseable.
- [x] **Safety gate** (`safety_check`) — the AI brief may only name stakeholders
      the kernel DELIVERED/ACKED/ESCALATED to (schema check + `STK-###`/name scan);
      `_summary_payload` swaps in the deterministic brief when the gate trips.
- [x] **`incidents.py`** — past-incident KB: deterministic similarity
      (metric/domain/severity + context-key overlap, normalized), `record_incident`
      opt-in via `ALERT_RECORD_INCIDENTS=1`. Seeded `incidents/*.json` so retrieval
      has data on first run.
- [x] **Dashboard wiring** — `_summary_payload` returns `ai_triage`; Console +
      Policy cards render the brief (cause + confidence, first checks,
      remediation, runbook, past incidents, mode badge + audit line).
- [x] **New tests (101 total)** — `test_agents.py`: KB retrieval order + record
      round-trip, brief schema, supervisor audit trail + fallback determinism,
      honesty (valid JSON → ai; garbage/schema-mismatch → fallback), safety-gate
      defect/clean/missing-keys.
- [x] **Live-verified** — real Anthropic run returns `mode=ai, source=ai` with
      runbook + similar incidents; keyless fallback stays fully deterministic.

### Phase 10 — Simultaneous evaluation + new scenario domains ✅ DONE

- [x] **One window, one decision (`R2B`)** — `decide_batch()` folds every change
      queued before a control point into a SINGLE verdict. A recipient going
      offline while a better candidate comes online is ONE hop straight to the
      best available target (no intermediate backup, no second R4a re-reroute).
      Single-event windows are byte-identical to the old `decide()` path.
- [x] **Deferred decisions at control points** — `on_event` folds events into the
      frozen snapshot immediately but queues the decision; `router.flush()`
      drains at ack / close / ack-timeout. Events are coalesced, not dropped.
- [x] **`scenario_4_simultaneous.json`** — Sarah offline + Priya online in one
      window → exactly one `R2B_REROUTE_BEST` reroute to STK-006.
- [x] **Three new domains** — `contracts` (contract expiry), `sla` (response-time
      breach), `anomaly` (anomaly score). Registry grows to 9 people: **Nina Osei**
      (contracts/sla) and **Leo Park** (anomaly); Elena's `sla_contracts` split
      into `sla` + `contracts`.
- [x] **Scenarios 5–7** — contract expiry → Nina (domain routing); critical SLA
      ack-timeout → parallel escalate (R4c); MEDIUM anomaly → never auto-escalates
      (R4c severity gate).
- [x] **Runbooks 4–6 + incidents 4–6** — `contract_expiry`, `sla_breach`,
      `anomaly_score`; runbook scoring is now **domain-gated** (severity only a
      tie-break — a doc that says "high" can no longer out-rank the right runbook).
- [x] **Metrics feed** — 6 sensors incl. contracts/sla/anomaly, with
      `direction: above|below` so upward breaches (latency, anomaly score) fire.
- [x] **New tests (101 total)** — `TestBatchFold` (single-hop / backup / worse-
      candidate / single-event-equivalence / R4b-after-ack), scenario 4–7
      end-to-end, runbook retrieval for the new domains, ranker order update.

---

## 4. KNOWN DECISIONS / TRADEOFFS RECORDED SO FAR

1. **Deterministic core, no LLM** — the 4 constraints are correctness
   properties; determinism makes them testable/provable. LLM only for prose later.
2. **MIN_REROUTE_DELTA = 1.5** on the 1–8 score scale (≈ one full domain point +
   seniority). Configurable via `--min-reroute-delta`. Interrupt/upgrade only
   when `cand_q - current_q >= delta`; else R5 IGNORE (logs the refusal).
3. **Acked ⇒ complete + escalate; unacked ⇒ abort + reroute.** Email ACK is not
   recallable; only un-acked sends can be aborted. Slack/SMS before-ack are
   recallable. Discriminator = delivery receipt.
4. **At-most-once dedup** — claim (INTENT) before send; crash between claim and
   send ⇒ possible miss (covered by escalation path), never a duplicate.
5. **Single-eval enforced physically** — `snapshots.PRIMARY KEY (alert_id,
   stakeholder_id)`; a second eval is an `IntegrityError`.
6. **No live presence reads after snapshot** — change detector diffs events
   against the frozen snapshot; a stakeholder's availability is read at most once.
7. **Route built once, cursor moves** — re-route stays within snapshot order.
8. **Channels stubbed** but faithful to real semantics; real adapters are
   drop-in behind `BaseAdapter`.
9. **Ack timers at control points, not threads** (determinism for walkthroughs/tests).

---

## 5. REPOSITORY LAYOUT (current)

```
Alert_routing/
├── ROADMAP.md                      ← this file
├── BLUEPRINT.md                    ← full design spec (15,247 words, §21 addendum)
├── README.md                       ✅ (TOC + hybrid dashboard)
├── LICENSE                         ✅ MIT (© 2026 Victor Ibhafidon)
├── pyproject.toml                  ✅ installable, `alert-routing` command
├── requirements.txt                ✅ optional extras only (core is stdlib)
├── Makefile                        ✅ run1/run2/run3/run-all/test/serve/ui/install
├── registry.json                   ✅ stakeholder seed (9 people)
├── roster.json                     ✅ on-call shifts (primary + backups per week)
├── scenarios/                      ✅
│   ├── scenario_1_offline.json
│   ├── scenario_2_channel_fail.json
│   ├── scenario_3_no_downgrade.json
│   ├── scenario_4_simultaneous.json   ✅ (R2B batch fold)
│   ├── scenario_5_contract_expiry.json
│   ├── scenario_6_sla_breach_ack_timeout.json
│   ├── scenario_7_anomaly_score_medium.json
│   └── proposed/                   ✅ (LLM-proposed, invariant-adopted scenarios)
├── runbooks/                       ✅ (post-decision runbook corpus, md)
├── alert_routing/                  ✅ (all files carry author/date headers)
│   ├── __init__.py
│   ├── models.py            registry.py       ranker.py
│   ├── snapshotter.py       planner.py        ledger.py
│   ├── presence.py          channels.py       changes.py
│   ├── decision.py          router.py         cli.py
│   ├── timeline.py          server.py         ui.py   ✅ (built)
│   ├── roster.py            ai.py             runbooks.py
│   ├── agents.py            incidents.py      ✅ (§22 agentic layer)
│   └── static/              (index.html/css/js/favicon — built, dark console)
├── incidents/                          ✅ (seeded triage KB)
├── tests/                            ✅ 106 tests
├── .github/workflows/ci.yml          ✅ (unit tests + cross-seed determinism)
└── Dockerfile                        ✅
```

---

## 6. NEXT SESSION — START HERE

**If `ROADMAP.md` is up to date, the next phase to start is the first `[ ]` item
in the "Build status" table.** As of this update:

1. **Dashboard refinement is DONE** — hybrid 3-view (Console/Policy/Registry),
   editable registry, on-call roster, docs in line (README/TESTING/
   ROADMAP/BLUEPRINT §21).
1b. **Agentic layer is DONE (Phase 9)** — two-lane AI (§22): read-only triage/
   comms/postmortem agents + supervisor + safety gate, `incidents/` KB,
    dashboard triage rendering, 106 tests, docs in line (README/TESTING/
    ROADMAP/BLUEPRINT §22).
1c. **Simultaneous evaluation + new scenario domains is DONE (Phase 10)** — `R2B`
   batch fold (one decision window ⇒ one verdict), 7 scripted scenarios across
   inventory / contracts / sla / anomaly domains, 6 runbooks + 6 incidents,
   6-feed sensors, 106 tests.
2. **Public repo is live** at `github.com/Web8080/alert-routing-agent` (CI
   green, Render-deployed dashboard).
3. **Record/upload the ≤3-min walkthrough video** (script in BLUEPRINT.md §14).
4. **REMINDER (self-trigger):** after each phase, update THIS file before
   starting the next phase.
