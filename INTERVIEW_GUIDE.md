# INTERVIEW_GUIDE.md — Alert Routing Agent

> **Purpose:** your personal prep guide for the walkthrough/defense. Contains the
> edge cases you handled, the decisions you can defend, likely interviewer
> questions with answers, and a quick-summary cheat sheet.
>
> **Auto-update rule (same as ROADMAP.md):** after every phase, this file is
> refreshed so it never goes stale. If a new chat picks up, it updates this file
> with new edge cases/questions that came up during the build.

---

## 1. THE 30-SECOND PITCH

> "I built a deterministic alert-routing agent. An alert (metric, value,
> threshold, severity) is ranked against a stakeholder registry by domain
> expertise and seniority — qualification first, availability is only a gate,
> never a ranking key. The best *available* candidate is dispatched through
> their preferred channel. Mid-flight, if the recipient goes offline, the
> channel fails, or a more-qualified person becomes available, an event-driven
> change detector diffs the change against a frozen snapshot and a policy
> engine decides: abort-and-reroute if the send isn't acknowledged, or
> complete-and-escalate-in-parallel if it is. Three guarantees are enforced
> physically, not by convention: no duplicate notifications, no double-querying
> availability, no downgrade to a less-qualified person just because they're
> online — and the final recipient always gets full context plus an explicit
> 'why you were chosen over X.'"

---

## 2. EDGE CASES HANDLED (be ready to demo or explain each)

| # | Edge case | How handled | Rule / mechanism |
|---|-----------|-------------|------------------|
| E1 | Recipient goes offline mid-flight, send **not acked** | Abort + reroute to next-ranked backup (snapshot order) | R2 |
| E2 | Recipient offline, send **already acked** (esp. email) | Complete original + escalate in parallel to next backup. Email is fire-and-forget — can't recall it | R3 |
| E3 | Preferred channel DOWN at snapshot time | Planner filters channel order by snapshot health → falls back silently, rationale notes it | planner gate |
| E4 | Channel fails at send time | Retry **same recipient** on next channel (transport ≠ recipient problem) | R1 |
| E5 | Best-qualified candidate is offline | Gated out but **retained** in snapshot with frozen score; becomes live escalation target if they come online | snapshot+gating |
| E6 | Escalation depth reached | Cap (default 3) bounds notifications; further requests refused → ABORT/UNRESOLVED | R6 + cap |
| E7 | Reroute target = the person who just went offline | Route cursor walks forward; skip offline + skip already-notified (I1 guard) — structurally impossible | cursor + I1 |
| E8 | Unknown metric domain (no experts) | Sanctioned fallback → duty manager (highest-seniority on-call), rationale states no expert exists | duty-manager |
| E9 | Empty roster / all gated out | ABORT → FAILED, alert UNRESOLVED, context parked in ledger for a human | R6 |
| E10 | Re-routing loop / repeated events | Terminal states are terminal (no transitions out); repeated identical events diff empty → IGNORE | state machine |
| E11 | Malformed alert | Ingress validation rejects before any ledger write | fail-loud |
| E12 | Concurrent alerts to same recipient | Dedup keyed per `alert_id` → both deliver correctly | ledger |
| E13 | Crash between claim (INTENT) and send | INTENT row blocks re-send on restart → possible miss, never a duplicate; escalation path covers it | at-most-once |
| E14 | Duplicate/replayed events | Diff against snapshot is empty → IGNORE | idempotent |
| E15 | Clock skew on timestamps | Timestamps are display-only; ordering is emitter arrival order (total order, in-process) | design |
| E16 | Recipient has no healthy channel | Gated out; only reachable via duty-manager exception | gate |
| E17 | Ack timer fires twice | Timer cancelled when plan leaves SENDING; late fire → IGNORE | R4c |
| E18 | LOW/MEDIUM severity | No ack timer armed → no R4c path | config |
| E19 | Corrupt registry entry | Fail-fast at load with named error | validation |
| E20 | Same alert ingested twice | Idempotent ingress keyed on alert_id | ingress |

**The "double-eval" trap** (the brief's nastiest constraint): availability is
read **once** per stakeholder per dispatch, and a second read is *physically
impossible* — `snapshots` has `PRIMARY KEY (alert_id, stakeholder_id)`, so a
re-evaluation raises `IntegrityError`. After the snapshot, the system only ever
*reads the snapshot* and diffs *events* against it. Events are the query.

---

## 3. DECISIONS YOU MADE — AND WHY (defendable)

1. **Deterministic policy engine, not an LLM.**
   The four constraints are *correctness* properties; an LLM can't prove them
   and adds nondeterminism (temperature, prompt drift) to paths the brief says
   "must not" fail. Determinism makes the demo replayable and the invariants
   testable. LLM is scoped to prose only, downstream.
2. **Qualification-first ranking; availability is a gate.**
   `qualification = expertise × (1 + (seniority−1)×0.15)`. Expertise dominates;
   seniority only breaks ties. Availability never reorders — so the system
   structurally cannot notify a worse match just because they're online.
3. **Abort only what you can actually recall (delivery-receipt discriminator).**
   Acked (email) ⇒ complete + escalate in parallel; unacked (Slack/SMS before
   ack) ⇒ abort + reroute. Matches real SMTP semantics.
4. **MIN_REROUTE_DELTA = 1.5.** Don't interrupt an active dispatch for a
   marginal improvement. `candidate_q − current_q >= delta` required for
   reroute/escalate. Sub-threshold candidates are IGNOREd with a logged refusal.
5. **At-most-once dedup.** Claim (INTENT) before send, in the same transaction
   as the duplicate-check. Crash between claim and send ⇒ miss (never a dup),
   covered by the escalation path.
6. **SQLite as ledger.** ACID transactions are the *enforcement mechanism* for
   dedup and terminal states, not a convention. One file, zero deps, crash-safe.
7. **Event-driven changes, never polling.** Polling = double-query (banned).
   Events carry before/after — exactly the diff the policy consumes.
8. **Route built once, cursor moves.** Rebuilding the route on events would
   re-rank against *current* availability and reopen the downgrade bug.
9. **Channels stubbed but faithful.** Email = fire-and-forget, Slack/SMS =
   presence-aware, DOWN ⇒ RETRIABLE. Real adapters drop in behind the interface.
10. **Stakeholder never evaluated → treat event as truth.** A never-snapshotted
    stakeholder's event carries availability; qualification comes from the
    registry (a config read, not a presence query).

---

## 4. LIKELY INTERVIEWER QUESTIONS (with answers)

**Q1. Why didn't you use an LLM agent? The brief said "agent."**
> The brief's hard constraints — no duplicates, no double-query, no downgrade —
> are correctness guarantees. An LLM can't *guarantee* them; a deterministic
> policy engine can, and I can prove it with tests. I treated "agent" as
> "autonomous decision-maker," which this is: it ranks, plans, executes, and
> re-plans on its own. The LLM's appropriate place is downstream — explaining
> alerts to humans — not in a delivery-critical decision path.

**Q2. How do you guarantee no duplicate notifications?**
> Two layers. Physical: `notifications` has `UNIQUE (alert_id, stakeholder_id,
> channel, escalation_level)`, plus a claim-time guard that rejects any second
> non-cancelled row for the same stakeholder on the same alert (invariant I1),
> and escalation targets must be a different, un-notified person (I2). Behavioral:
> check-then-claim — every send registers an INTENT in the same transaction that
> checks for an existing delivery. A reroute that tries the same person is a no-op.

**Q3. How does this compare to PagerDuty / Opsgenie / Grafana OnCall? (market map)**
> Same pipeline, different scope. Every one of them does:
> *ingress* (webhook, dedup/grouping) → *on-call schedule* → *escalation
> policy* → *notification channels* (email/Slack/SMS) → *ack/escalation* →
> *incident timeline* → *postmortem*. PagerDuty is the enterprise incumbent
> (700+ integrations, AIOps as a paid add-on); Opsgenie is its Atlassian-priced
> clone; Grafana OnCall was archived in 2026 in favor of Grafana IRM; incident.io
> is Slack-native coordination; Rootly/Better Stack are mid-market. The
> ingestion pipeline is the same one Prometheus Alertmanager already speaks
> (webhook receiver → dedup → route → receiver). This repo is a **deterministic,
> testable slice of that pipeline** — specifically the routing/escalation core,
> with the guarantees the incumbents sell but can't prove. That's the honest
> positioning: not a PagerDuty clone, but the provable core of one.

**Q4. Embedded API vs standalone tool — which would you build?**
> **API-first engine + console.** That's how PagerDuty/Opsgenie/Grafana IRM are
> actually structured: an ingestion API (`POST /alert`, and a Prometheus
> Alertmanager webhook receiver) plus a configuration console. For a sellable
> product you'd keep this exact seam: the routing core is a service other tools
> call (their monitoring stack posts alerts; your API returns the dispatch
> result + timeline), and the console is how teams configure registry/on-call.
> Standalone-with-own-monitoring (Better Stack's model) is a different, heavier
> bet. For this repo, the "plug into existing software" answer is the API: any
> system that can POST JSON (or an Alertmanager webhook) can drive it.

**Q3. How do you ensure you never query availability twice for the same person?**
> Availability is read exactly once per stakeholder per dispatch, in the
> snapshot phase, and the schema makes a second read an `IntegrityError`. After
> that the change detector never asks "is X online?" — it *receives* an event
> that says X changed and diffs it against the frozen snapshot. The event is the
> query. There is no code path that polls.

**Q4. What if the best-qualified person is offline? Do you page a worse person?**
> No — that's exactly the bug the brief forbids. The offline best stays in the
> snapshot with a frozen score, marked gated. I dispatch to the best-ranked
> *available* candidate, and if the offline best comes online mid-flight,
> `candidate_q − current_q >= MIN_REROUTE_DELTA` triggers a reroute (unacked) or
> parallel escalation (acked). A worse match arriving online is refused by R5.

**Q5. Abort the dispatch, or complete and escalate in parallel? How do you decide?**
> The discriminator is the delivery receipt. If the send hasn't been
> acknowledged, it's recallable — abort and reroute. If it's been acknowledged
> (email especially), it's terminal — you can't un-send an accepted email, so
> completing it and escalating in parallel to the next-ranked person is the only
> honest option. I don't pretend to abort what I can't recall.

**Q6. What does "escalate in parallel" mean for dedup?**
> It's a new notification row at `escalation_level+1` for a *different*
> stakeholder. Same alert_id (so it's one incident), different recipient (so I1/I2
> hold). The original delivery stands; the escalation carries full context plus a
> note that it's an escalation because the primary was offline/wasn't responding.

**Q7. What does MIN_REROUTE_DELTA protect against?**
> Churn. If a candidate only 0.5 points better becomes available, interrupting
> an active dispatch for that marginal gain risks: an aborted half-delivered
> message, recipient confusion, and a slower overall path. The delta (1.5 on a
> 1–8 scale) says: only move if it's materially better. It's a config knob, and
> sub-threshold candidates get an R5 IGNORE logged so the decision is auditable.

**Q8. Why is the route immutable?**
> If I rebuilt the route on every event, I'd re-rank against *current*
> availability — which silently reintroduces the availability-first bug the
> brief bans. A frozen route means "next-ranked backup" is well-defined and the
> no-downgrade property is structural. New rostering is picked up by the next
> alert's dispatch, which is the right boundary.

**Q9. How is this tested?**
> Three layers: unit tests on the pure ranker/policy/snapshot; scenario tests
> that run scripted event timelines through the full router and assert terminal
> state + invariants + context preservation; and invariant assertions after
> every event. The two invariants (no duplicate per stakeholder; no
> same-person-escalation) are asserted as post-conditions of every scenario.

**Q10. What would you do with more time?**
> Real channel adapters (SMTP/Slack/Twilio) behind the same interface; real
> on-call rotation calendars instead of a boolean; a real event bus (Kafka) with
> replay — my event replay is already idempotent; ack/escalation incident UI
> (the timeline renderer already reads the ledger); LLM-generated prose for
> rationale; calibrating the seniority weight from real resolution data; chaos
> tests that kill the process at random ledger points.

**Q11. Isn't two notifications (primary + escalation) a duplicate?**
> No — dedup means *the same stakeholder* doesn't get the same alert twice. A
> primary to one person and an escalation to a different person is the required
> "complete and escalate in parallel" behavior. I1/I2 are explicit: one row per
> stakeholder per alert.

**Q12. Why SQLite instead of Redis?**
> For this scale and the demo, SQLite gives ACID transactions — the actual
> enforcement mechanism for dedup — with zero dependencies and crash safety. The
> same schema and claim protocol move to Postgres/Redis unchanged if we scale
> out; the protocol is storage-agnostic.

**Q13. How do you handle the case where the escalation itself fails?**
> Escalation cap (3) bounds attempts; when every target is exhausted or failed,
> R6 marks the plan FAILED/UNRESOLVED with full context parked in the ledger and
> the complete attempted chain in the decision log — a human or follow-on system
> can pick it up. The alert is never silently lost.

**Q14. What did you *deliberately* not do?**
> Real provider APIs (creds + nondeterminism would ruin a reproducible 3-min
> demo), an LLM in the hot path, property-based fuzzing at scale, a background
> ack-timer thread (control-point evaluation keeps it deterministic). Each is
> documented as deliberate with a cost/benefit in BLUEPRINT.md §12.

---

## 5. SUMMARY / CHEAT SHEET

- **Input:** `{metric, value, threshold, severity, domain, context}` → `alert_id`.
- **Flow:** rank (qualification) → snapshot availability once → plan route →
  claim + send via preferred healthy channel → events → policy verdict.
- **Policy rules:** R1 retry channel · R2 abort+reroute (unacked) ·
  R3 complete+escalate (acked) · R4 reroute/escalate to better match (delta) ·
  R4c ack-timeout escalate · R5 ignore downgrade · R6 abort/resolve.
- **Three hard guarantees:** no dup (ledger), no double-query (snapshot PK),
  no downgrade (qualification-first + delta gate).
- **Run it:** `python -m alert_routing.cli scenarios/scenario_1_offline.json`
  → trace + incident timeline. `python -m unittest discover -s tests` → suite.
- **Numbers:** Sarah q=6.50 primary · David q=5.80 backup · Elena q=3.20
  (senior 5, low domain → no-downgrade demo) · Priya q=7.25 / Maya q=8.00
  (delta gate: marginal vs pass) · MIN_REROUTE_DELTA=1.5 · cap=3.
- **Demo beats:** scenario 1 offline→reroute; scenario 2 channel-fail→fallback;
  scenario 3 senior-but-low-qualification→refused.

---

## 6. THE FIVE CLAIMS — PROOF (verdict + mechanism)

| Claim | Verdict | Mechanism |
|---|---|---|
| P1 Never loses the alert | ✅ YES — durable ledger is the default | alert + context + decision-log persisted before any send; CLI/UI default to a **durable temp file** (not `:memory:`), so a crash cannot lose it; re-renderable from a file ledger; at-most-once |
| P2 Never notifies same stakeholder twice | ✅ YES — structural | `UNIQUE(alert, sid, channel, level)` + I1/I2 check-then-claim in one SQLite transaction |
| P3 Never re-queries availability | ✅ YES — structural | availability read only in `snapshotter.py:27-28`; `PRIMARY KEY(alert_id, sid)` makes a 2nd read a physical `IntegrityError`; events are diffs, never polls |
| P4 Never downgrades "because they're online" | ✅ YES — by construction | qualification is availability-free; availability is a gate; route frozen; R5 + `MIN_REROUTE_DELTA` |
| P5 Same state ⇒ same decision | ✅ YES — enforced | pure policy, no random/uuid/hash-order, no set-iteration; empirically identical across 5 hash seeds; the only wall-clock path (R4c ack-timeout) **raises** unless a scripted clock was injected |

### Why each (say it this way)

**P1 — yes, and now the default is safe.** The alert row + full context +
decision log are written before any send, and a "crash + reopen + re-render
timeline" round-trip against a file ledger loses nothing. We closed the one gap:
the CLI/UI previously defaulted to `--ledger :memory:`, so a crash wiped the
ledger. Now they default to a **durable temp file** — a crash leaves the file on
disk and the timeline re-renders from it. Explicit `--ledger PATH` gives
cross-run persistence; `:memory:` is now the opt-in throwaway mode. One honest
limit remains: a stale INTENT from a crash-before-send isn't auto-reconciled on
restart — that's a *miss*, never a duplicate (by design), but not auto-rerouted.

**P2 — provable.** `ledger.py:202-218` does the I1/I2 duplicate check and the
INTENT insert inside a single `with self.conn:` transaction, and the
`UNIQUE(alert_id, stakeholder_id, channel, escalation_level)` constraint is the
physical backstop — even if two threads raced the check, the loser gets a hard
`IntegrityError`, never a second row. Verified: scenario output (Sarah CANCELLED
once, David DELIVERED once) + `test_dedup.py`.

**P3 — provable.** `presence.online()` / `channel_health()` are called in
exactly one place — the snapshot phase. The schema
(`PRIMARY KEY (alert_id, stakeholder_id)`) makes a second evaluation an
`IntegrityError`. After snapshot, the change detector diffs events against the
frozen snapshot and folds event truth in — a diff, not a query. There is no
poller.

**P4 — by construction, so say it precisely.** The system never upgrades to or
reroutes to a worse person while a better one is available/appropriate (R5 +
delta gate, frozen route). It can page a less-qualified person only when every
more-qualified candidate is gated out — that's "best available," which is the
correct semantics, not "worse because online." Scenario 3 proves it live:
seniority-5 Elena (q 3.20) is refused over Sarah (q 6.50).

**P5 — yes for all shipped entry points, now enforced.** The policy is a pure
function; no random/uuid/shuffle; ties break on (qualification, id); decisions
consume SQLite rows only as membership sets, never iterated; `decision_log` is
`ORDER BY seq`. CLI output is byte-identical under `PYTHONHASHSEED`
1/2/42/1337/999. The remaining caveat — `Router` silently falling back to
`SystemClock` made R4c wall-clock-dependent — is closed: `evaluate_ack_timeout`
now **raises `RuntimeError`** unless a scripted clock was injected
(`router.py`). CLI/UI/API/tests always inject `SimClock`, so the R4c path is
deterministic and cannot silently degrade.

---

## 7. LIVING LOG — build-time Q&A added by sessions

(Updated as the build reveals new edge cases, interviewer-style questions, or
design re-explanations. Keep this section appended, never overwritten.)

- **[build] Q: In scenario 2, why did the reroute NOT go to David, who is also
  qualified?** A: Channel failure is a transport problem, not a recipient
  problem. Sarah is still the correct qualified recipient; we only change the
  transport (slack → email, rule R1). Re-routing on a transport failure would
  waste qualified capacity and risk the downgrade bug.
- **[build] Q: Why is Maya (q=8.00) ranked above Sarah (q=6.50) but not chosen
  in the demo?** A: She's gated — off-call and offline at snapshot. Ranking
  shows raw qualification; the route only contains gated-in candidates. This
  is exactly the "qualified-but-unavailable" case that must be handled without
  paging a worse person.
- **[build] UI: what did the dashboard add?** A zero-dependency
  (`http.server` + 3 static files) operator console that reuses the exact CLI
  code path (`run_scenario_data`). It makes the invisible visible: a ranking
  table (qual + availability + GATED), a live state machine, an animated
  dispatch trace, a decision card with rationale, the notification ledger, an
  incident timeline, and R1–R6 policy chips. Principle from review: it's a
  demo console, not a SaaS product — its only job is to make five facts
  undeniable: who was selected, why, what changed mid-flight, why the agent
  rerouted/escalated/continued, and how we know nothing was duplicated or lost.
- **[build] Q: how does the UI dispatch a custom alert?** A: `POST /api/dispatch`
  with `{"alert": {...}}` builds a scenario dict and calls the same
  `run_scenario_data`. Unknown domains default the duty manager to the most
  senior on-call stakeholder instead of an arbitrary tie-break.
- **[hardening] Q: you claimed P1 "never loses the alert" — but the default
  ledger was `:memory:`. Wasn't that a real gap?** A: Yes, and it's closed. The
  CLI/UI now default to a **durable temp file** ledger, so a process crash
  leaves the alert on disk and the timeline re-renders from it. `:memory:` is
  now the *opt-in* throwaway mode. This removed the last "mostly yes" from the
  proof table.
- **[hardening] Q: P5 said the R4c ack-timeout became wall-clock dependent when
  no clock was injected. Did you fix that or just document it?** A: Fixed by
  enforcement. `evaluate_ack_timeout` now raises `RuntimeError` if the router
  is running on a wall-clock `SystemClock`, instead of silently making the
  decision time-dependent. Every shipped entry point injects `SimClock`, so the
  R4c path is deterministic — and now it cannot silently degrade.
- **[hardening] Why is this better than "just testing more"?** A: Both
  caveats were closed by *making the unsafe state impossible* (a durable default
  ledger; a loud error instead of a silent wall-clock fallback) rather than by
  more tests that could be skipped. That is the difference between "mostly
  yes, with a caveat" and "yes, structurally."
- **[live-delivery] Q: you said real email + Slack are opt-in. How does the
  core stay deterministic?** A: The adapters are selected by `adapter_for` at
  construction: real SMTP/webhook when `.env` is configured, stub otherwise.
  And the AI prose is *injected* (`Router(prose=...)`) from the entry point —
  the routing core never makes a network call, so tests are hermetic and P5 is
  untouched. Real delivery changes *transport*, never *decisions*.
- **[ai-layer] Q: why not use the LLM to route?** A: Routing is a set of
  provable correctness properties (no-dup, no-double-query, no-downgrade,
  determinism). An LLM can't prove them and its output isn't reproducible. So
  the LLM writes the *explanation* ("why you", incident summary) after the
  decision, with a deterministic template as the always-available fallback.
  This is also the industry shape: PagerDuty's AIOps is an analysis layer, not
  the router.
- **[ai-layer] Q: you used AI to generate scenarios. Isn't that the same
  thing as trusting it?** A: No — the LLM only *proposes*. A deterministic
  invariant suite ADOPTS or REJECTS each candidate (clean run, P2 no-dup, P5
  reproducibility, exercises an availability change). The model can't ship a
  scenario that violates a guarantee; it can only suggest new ones to test.
  That's the same "LLM proposes, evals decide" pattern I use for eval
  workflows.
- **[market] Q: what did the competitive research change?** A: It confirmed
  the pipeline (ingress → on-call → escalation → channels → ack → timeline)
  and where we sit: the routing/escalation core, with guarantees the incumbents
  sell but don't prove. It also settled the product-form answer — API-first
  engine + console — and that the Alertmanager webhook receiver is the
  standard "plug us into your monitoring" seam.

(Keep adding entries here after each phase; the next session appends, never replaces.)

---

## 7.5 RECENT CHALLENGES FACED — AND HOW I OVERCAME THEM

Keep this fresh: it's the "how you think" part of the assessment. Each one is
a real bug/decision from this build, with the fix and what it taught.

- **Challenge: the invariant suite over-rejected a legitimate test.**
  When the AI proposed "all candidates offline at once", the suite rejected it
  because the plan ended FAILED with zero notifications. That was wrong: an
  abort path (R6) is a *valid scenario to test*, not a violation. Fix: the
  invariants now check the **guarantees** (P2 no-dup, P5 reproducibility, the
  scenario exercises an availability change) — not that the plan must succeed.
  Lesson: an eval suite should assert *properties*, not *outcomes*; a test that
  "fails" because it reached the designed failure path is a broken test.

- **Challenge: the AI invented a scenario the engine can't run.**
  The model proposed an in-step alert dispatch (a second alert nested inside
  `steps`) which the scenario driver doesn't support. First reaction: "broken".
  Second look: the suite **correctly rejected** it — that's the safety net
  working as designed. I then tightened the prompt (exactly one top-level alert;
  steps may only use the supported ops) so well-formed proposals get adopted
  more often. Lesson: with "LLM proposes → evals decide", a rejection is a
  feature; you make the prompt stricter *without* weakening the gate.

- **Challenge: the durable-ledger change broke determinism.**
  The new default ledger writes a random temp filename into stdout — so the
  P5 cross-hash-seed diff failed (each run had a different path). Fix: ledger
  diagnostics go to **stderr**; stdout stays byte-identical. Caught by the very
  determinism check the change was meant to preserve — the check earned its
  keep.

- **Challenge: a committed API key would make tests hit the network.**
  Once `.env` had a live key, `ai_enabled()` returned True for unit tests →
  tests would have made real Anthropic calls (slow, flaky, non-hermetic). Fix:
  the AI prose is **dependency-injected** (`Router(prose=...)`) from the entry
  point, never referenced inside the routing core. Tests construct the Router
  without prose → hermetic. Lesson: injection is also a *testability* strategy,
  not just an architecture fashion.

- **Challenge: the wrong model name returned HTTP 404.**
  `claude-3-5-haiku-latest` doesn't exist on the current Anthropic API; the
  whole AI layer failed on first live call. Fix: probed the API directly with
  candidate model names, found `claude-haiku-4-5`, made it the default. Lesson:
  never assume a model alias is still valid — probe the real API before wiring
  it into production paths, and always keep a deterministic fallback.

- **Challenge: real adapters must not fake delivery.**
  A Slack endpoint without a wired webhook had two bad options: fake ACK
  (dishonest) or hard-fail the demo. Fix: missing webhook → **RETRIABLE** →
  the router honestly falls back to the next preferred channel. Delivery is
  real where configured, and never lied about where it isn't.

- **Challenge: what's the right scope under a hard deadline?**
  The brief says "small that genuinely works beats large that does not", so
  RAG/multi-agent/Kafka were consciously *not* built into the core. They're
  documented as the scale-out design (see §3 decisions and the roadmap), and
  only the cheap, high-signal slice shipped: env-gated real delivery, injected
  AI prose, CI, Docker. Lesson for the interview: saying *no* to a shiny thing
  is a decision you can defend; shipping an unproven pile is not.

---

## 8. WHAT I LEARNED — PHASE-BY-PHASE JOURNAL

For the "tell me about your process" question. Speak from the *difficulty + how I
overcame it* angle — interviewers remember the struggle, not the smooth parts.
Each phase below: **what I learned → the difficulty → how I overcame it.**

### Phase 1 — Spec & design (BLUEPRINT.md)
- **Learned:** writing the full spec *before* code is what exposed the two
  hidden traps — the "double-eval" ban and the "no downgrade" rule. On paper
  they sound like conventions; they only become real when you design an
  *enforcement mechanism* for each. That's the whole lesson of this project:
  **guarantees you enforce physically (DB constraints, immutable structures)
  survive; guarantees you enforce by discipline don't.**
- **Difficulty:** the abort-and-reroute vs complete-and-escalate decision was
  ambiguous until I thought in terms of *delivery-receipt semantics* — an
  unacked send is recallable, an acked email is not (SMTP is fire-and-forget).
- **Overcame it:** built a decision matrix early (R1–R6) and paired every rule
  with a mechanism, not a hope: snapshot PK → no double-query; UNIQUE + cursor →
  no dup; qualification-first ranking → no downgrade.

### Phase 2 — Core build (models/registry/ranker/snapshotter/planner/ledger/
presence/channels/changes/decision/router/cli/timeline)
- **Learned:** availability is a **gate, never a rank key**; the route is built
  **once** and a *cursor* walks forward — rebuilding the route on events would
  re-rank against current availability and silently reintroduce the downgrade
  bug. Change detection must be **event-driven** (diffs of before/after), never
  polling — polling *is* the forbidden double-query.
- **Difficulty:** defining "next-ranked backup" under a stream of events. The
  plan is immutable, so "next" had to be a well-defined position, not a re-rank.
- **Overcame it:** snapshot frozen in a dataclass; policy engine written as a
  **pure function** (no I/O) so every rule is unit-testable in isolation;
  ledger claim = check-then-claim in one SQLite transaction.

### Phase 3 — Optional HTTP API (server.py)
- **Learned:** keep optional dependencies truly optional — a guarded import and
  a thin adapter (`run_scenario`) mean the core stays 100% stdlib and the HTTP
  layer contains zero routing logic.
- **Difficulty:** staying honest about scope — the API was easy to gold-plate
  but it's not in the demo, so it had to be cheap to maintain.
- **Overcame it:** `server.py` builds a scenario and calls the *exact same*
  router code path. One code path, two entry points. Nothing duplicated.

### Phase 4 — Seed data + scenarios
- **Learned:** scenario design is where guarantees become *demonstrable*.
  Choosing registry numbers on purpose — Priya q=7.25 (marginal, sub-delta) vs
  Maya q=8.00 (passes the gate) — makes the MIN_REROUTE_DELTA behavior visible
  in a demo, not just in tests.
- **Difficulty:** making the no-downgrade scenario (3) *real* needed a
  stakeholder who is senior but low in domain qualification (Elena, seniority 5,
  inventory expertise 2 → q=3.20 vs Sarah q=6.50).
- **Overcame it:** derived all qualifications from the same formula
  (`expertise × (1 + (seniority−1)×0.15)`) so every scenario number is
  reproducible and defensible, and gated-but-qualified candidates (Priya/Maya)
  stay visible in the RANK output — which is itself a talking point.

### Phase 5 — Tests (this is where the bugs surfaced)
- **Learned:** the test suite earned its keep immediately — it caught **three
  real design bugs** and forced me to *name* a subtle semantic:
  1. **Ack ≠ plan-terminal (the big one).** `acknowledge()` originally set the
     plan straight to DELIVERED, which made rules R3/R4b (recipient goes offline
     *after* an email was already accepted → complete + escalate in parallel)
     unreachable dead code. **Overcame it:** `acknowledge()` only finalizes the
     notification; a new `close()` finalizes the plan (DELIVERED or ESCALATED)
     at the end of the dispatch session. The acked-but-now-offline case is what
     the brief literally asks for — I almost shipped a system that couldn't do
     it.
  2. **Stale-snapshot adapter loop.** A candidate who came online via event
     (Maya, R4a) was still `online=False` in the frozen snapshot, so the
     Slack/SMS adapters returned RETRIABLE forever → infinite retry loop.
     **Overcame it:** `_apply_event_to_snapshot()` folds the event's truth into
     the snapshot — that's a *diff*, not a re-query, so the no-double-query
     guarantee still holds. Event truth and snapshot truth are now consistent.
  3. **Unknown-domain routing.** With no experts, tie-breaking picked an
     arbitrary person. **Overcame it:** sanctioned fallback to the duty manager,
     with the rationale explicitly stating no expert exists.
- **Learned (semantic):** a cancelled notification slot stays *consumed* for the
  same (recipient, channel, level) — the UNIQUE key never forgets, so a reroute
  can't sneak a duplicate past the claim guard — but a **different channel for
  the same recipient is still allowed** (that's exactly rule R1 retry).
- **Difficulty (tooling):** `python -m unittest discover -s tests` loaded test
  modules as top-level, breaking relative imports. **Overcame it:** run
  discovery from the project root (`python -m unittest discover`) so `tests` is
  imported as a package.

### Build-hygiene lesson (applies to every phase)
Streaming terminal output in my environment glitched once (duplicated/merged
lines) and nearly made me "fix" a bug that didn't exist. **Overcame it:** I
verified against exit codes, files on disk, and the test suite — never trust a
single output path when the stakes are "did the system behave correctly." The
tests were the ground truth; the terminal was just a view.

---

**One-line summary if asked "what did you learn?":** *Correctness requirements
must be enforced structurally, not by discipline — and the demo only works
because the tests proved the two invariants hold on every path.*
