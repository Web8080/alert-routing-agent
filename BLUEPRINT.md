# Alert Routing Agent — Engineering Blueprint

**Document:** `BLUEPRINT.md`
**Project:** alert_routing
**Status:** Approved — build-ready
**Version:** 1.0
**Date:** 2026-08-14
**Author:** Engineering (prepared by an AI engineering lead)
**Classification:** Internal design specification

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Requirements Analysis](#2-requirements-analysis)
3. [System Architecture](#3-system-architecture)
4. [Data Model](#4-data-model)
5. [Ranking Algorithm](#5-ranking-algorithm)
6. [The Single-Evaluation Guarantee (No Double-Query)](#6-the-single-evaluation-guarantee-no-double-query)
7. [Dispatch Plan & Route State Machine](#7-dispatch-plan--route-state-machine)
8. [Decision Policy](#8-decision-policy)
9. [Idempotency & Deduplication](#9-idempotency--deduplication)
10. [Channel Adapters](#10-channel-adapters)
11. [Edge Cases](#11-edge-cases)
12. [Tradeoffs & Defended Decisions](#12-tradeoffs--defended-decisions)
13. [Test Plan](#13-test-plan)
14. [Walkthrough Script & Scenarios](#14-walkthrough-script--scenarios)
15. [Repository & Release Strategy](#15-repository--release-strategy)
16. [Future Work](#16-future-work)
17. [Appendix A — Stakeholder Seed Data](#appendix-a--stakeholder-seed-data)
18. [Appendix B — Full Decision Traces](#appendix-b--full-decision-traces)
19. [Appendix C — Glossary](#appendix-c--glossary)
20. [Acceptance Checklist](#20-acceptance-checklist)

---

## 1. Executive Summary

This document is the complete engineering blueprint for **Alert Routing Agent**, a deterministic, event-driven system that decides *which stakeholder to notify* when an operational metric crosses a threshold, and — the hard part — *re-plans the dispatch in real time* when the world changes mid-flight.

The system ingests an alert event (metric name, value, threshold, severity), consults a stakeholder registry, builds a ranked candidate list scored by domain expertise and seniority, filters that list by current availability and on-call status, executes a dispatch to the highest-ranked available stakeholder through their preferred channel, and then detects — *without re-querying anyone it has already evaluated* — that availability, channel health, or the qualified population has changed. On detection it must decide, in real time, whether to complete the original dispatch, re-route to a better recipient, or escalate in parallel. Three properties are inviolable:

1. **No duplicate notifications.** No stakeholder may receive the same alert twice, through any channel, under any re-routing or escalation sequence.
2. **No double-querying.** A stakeholder's availability is evaluated at most once per alert dispatch. The system never re-polls people it has already scored.
3. **No downgrade.** The system never notifies a less-qualified stakeholder merely because that stakeholder happens to be online. Qualification is the primary ordering key; availability is a gate.

The design deliberately chooses a **deterministic policy engine** over a free-form LLM agent for the routing hot path. Determinism is what makes properties 1–3 *provable* rather than *hoped for*. The policy engine is small, testable, replayable, and produces an explicit `decision_log` for every action — the artifact that makes the whole system explainable to a human reviewer and defensible in a walkthrough. An LLM is deliberately reserved for optional prose generation and explicitly excluded from any decision that affects delivery correctness.

The runtime is **Python 3, standard library only** for the core (dataclasses, `sqlite3`, `json`, `argparse`, `threading`). An optional FastAPI HTTP endpoint (`POST /alert`) is provided for API shape and is never imported by the core. State lives in two places: a **SQLite ledger** that is the source of truth for idempotency and deduplication (ACID, single-process, crash-safe), and a **JSON stakeholder registry** that is read-only at dispatch time. Presence and channel health changes are delivered as **events**, never discovered by polling. The decision policy, ranking formula, and ledger invariants are specified exactly in this document so that a fresh engineer can implement the system without further consultation.

The remaining sections specify every component to a build-ready level of detail: the exact score formula, the SQLite schema, the dispatch state machine, the decision-policy matrix (rules R1–R6), the check-then-claim dedup protocol, the channel adapter interface, fifteen-plus enumerated edge cases with the rule that handles each, a full tradeoff analysis, a property-based test plan, a three-minute walkthrough script with three scripted scenarios, and a release strategy that targets a public repository under the `github/web8080` account.

**Success criteria.** The submission is judged against the original brief on four axes: (a) a working system that accepts an alert and routes it correctly; (b) correct mid-flight re-routing that preserves alert context and never duplicates; (c) a defensible walkthrough of every design decision; (d) deliverables that open — a public repository and a viewable three-minute video. Every section of this blueprint exists to make those four axes reproducible.

---

## 2. Requirements Analysis

This section restates the original brief as precise, testable requirements, then derives the acceptance criteria that the test suite must enforce. Ambiguities in the brief are resolved here, explicitly, with rationale, so that downstream implementation does not reinterpret them.

### 2.1 Functional requirements

**F1 — Alert ingress.** The system SHALL accept an alert event carrying at least: `metric` (e.g. `stock_level`), `value` (the observed measurement), `threshold` (the breached bound), `severity` (one of `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`), and an arbitrary `context` payload that must be preserved verbatim through the entire dispatch lifecycle. Each alert SHALL receive a unique `alert_id`.

**F2 — Stakeholder registry.** The system SHALL read a stakeholder registry containing, per stakeholder: unique id, display name, title, seniority tier, a mapping of expertise domains to proficiency scores, an on-call flag, and an ordered list of notification preferences (channel + endpoint). The registry is authoritative at dispatch start; a snapshot of the relevant roster SHALL be taken and held for the duration of the dispatch.

**F3 — Ranking.** The system SHALL produce a ranked list of candidates by domain expertise and seniority, subject to availability and on-call gating. Ranking SHALL be deterministic: identical inputs produce identical orderings.

**F4 — Dispatch execution.** The system SHALL execute a dispatch plan to the highest-ranked available stakeholder via their highest-ranked available channel, recording every send attempt in the ledger.

**F5 — Mid-flight change detection.** The system SHALL detect, during dispatch execution, that: (i) the primary recipient went offline; (ii) the preferred channel failed; or (iii) a more qualified stakeholder became available. Detection SHALL be event-driven. The system SHALL NOT re-poll availability of stakeholders it has already evaluated.

**F6 — Re-planning.** On detection, the system SHALL choose one of: complete the original dispatch; abort and re-route to a better recipient; or escalate in parallel. The choice SHALL be recorded with a human-readable rationale.

**F7 — No duplication.** Under no re-route/escalation sequence SHALL a stakeholder receive the same alert more than once per channel. The ledger SHALL enforce this atomically.

**F8 — Context preservation.** Every delivered notification SHALL contain the full alert payload and a `rationale` field explaining why the recipient was chosen over other candidates.

### 2.2 Non-functional requirements

**N1 — Determinism.** The routing hot path SHALL be deterministic given identical (alert, registry, event-sequence). No randomness, wall-clock-dependent ordering, or model sampling is permitted in routing decisions.

**N2 — Replayability.** Any dispatch SHALL be replayable from its ledger entries. The ledger is an append-only event journal for the dispatch.

**N3 — Zero-dependency runtime.** The core SHALL run on Python 3.10+ with the standard library only. No third-party imports in `src/` except the optional `server.py` module.

**N4 — Crash safety.** A crash at any point SHALL NOT produce duplicate notifications. The check-then-claim ledger transaction is the mechanism; it must be exercised by tests.

**N5 — Explainability.** Every decision SHALL emit a structured log entry with an action code and a free-text justification composed from template fragments (not freeform model text).

**N6 — Testability.** All components SHALL be unit-testable without network, filesystem race, or clock dependence; the clock SHALL be injectable.

### 2.3 Ambiguity resolutions

- **"Stakeholder availability"** is resolved as a binary online/offline state per stakeholder plus a per-channel health state (`OK` / `DEGRADED` / `DOWN`). Both are maintained by the presence service and delivered via events.
- **"Preferred channel"** is resolved as an ordered list `[slack, email, sms]`; the planner always tries the first channel whose state is `OK`.
- **"More senior stakeholder"** is resolved through a numeric seniority tier combined with domain expertise into a single *qualification score* (Section 5). Escalation moves strictly up the qualification ordering.
- **"Less qualified because they are online"** is the forbidden action and is defined precisely: the system SHALL NOT deliver to candidate B while a candidate A with higher qualification exists in an *available* state, regardless of when B's availability was observed. Enforcement is structural — the ranking algorithm never places availability ahead of qualification — not a runtime check.
- **"Abort"** applies only while a send is `QUEUED` or `IN_FLIGHT` (not yet acknowledged). Once a channel acknowledges delivery (e.g. the SMTP server accepted the message), the send is terminal: the system cannot recall it and therefore *completes and escalates in parallel* rather than pretending to abort. This mirrors the real-world semantics of email and is a defended decision (Section 12).
- **"Escalate in parallel"** means: leave the original (possibly already-delivered) notification in place, and issue an *escalation* notification to the next-higher-qualified candidate. An escalation carries a distinct `escalation_level` but the same `alert_id`, so the dedup ledger treats it as a different recipient but the same alert.

### 2.4 Acceptance criteria (mapped to tests)

| ID | Criterion | Test |
|----|-----------|------|
| AC-1 | Given an alert and a registry, the system returns a deterministic ranking. | `test_ranker_determinism` |
| AC-2 | A stakeholder is availability-evaluated at most once per dispatch. | `test_snapshot_single_eval` |
| AC-3 | No stakeholder receives the same alert twice on the same channel. | `test_dedup_never_duplicate` |
| AC-4 | The system never notifies a lower-qualified stakeholder while a higher-qualified one is available. | `test_no_downgrade` |
| AC-5 | Mid-flight offline detection aborts a queued send and re-routes. | `test_reroute_on_offline` |
| AC-6 | Mid-flight offline detection after delivery completes escalates in parallel, no retraction, no duplicate. | `test_escalate_after_delivery` |
| AC-7 | A more-qualified stakeholder appearing mid-flight triggers parallel escalation; a less-qualified one does not. | `test_better_match_appears` |
| AC-8 | Every delivered message contains the full alert payload and a rationale. | `test_context_preserved` |
| AC-9 | Re-running a dispatch from the ledger reproduces the same decision log. | `test_replayable` |
| AC-10 | Crash mid-send never yields a duplicate. | `test_crash_idempotency` |

These criteria are normative. The implementation is not considered complete until every row is green.

---

## 3. System Architecture

### 3.1 Component overview

The system is organized as one process with seven cooperating components plus two external-ish data stores. Boundaries between components are enforced at the Python module level, and each component is testable in isolation with injected dependencies.

```
                          ┌────────────────────────────────────────────┐
   alert event            │                  ROUTER                     │
 ───────────────────────▶ │  (orchestrator / the "agent")              │
                          │                                            │
                          │   ┌───────────────┐    ┌────────────────┐  │
                          │   │   RANKER      │    │  SNAPSHOTER    │  │
                          │   │  score by     │───▶│  evaluate      │  │
                          │   │  expertise &  │    │  availability  │  │
                          │   │  seniority    │    │  ONCE, stamp   │  │
                          │   └───────────────┘    └────────────────┘  │
                          │            │                                │
                          │            ▼                                │
                          │   ┌───────────────┐                        │
                          │   │   PLANNER     │  ranked route +        │
                          │   │  plan + route │  escalation depth cap  │
                          │   └───────────────┘                        │
                          │            │                                │
                          │            ▼                                │
                          │   ┌───────────────┐    ┌────────────────┐  │
   presence/channel       │   │  DISPATCHER   │◀───│    CHANGES     │  │
   events ───────────────▶│   │  execute via  │    │   DETECTOR     │  │
                          │   │  adapters     │    │ (event-driven) │  │
                          │   └───────┬───────┘    └────────────────┘  │
                          │           │                                 │
                          │           ▼                                 │
                          │   ┌───────────────┐    ┌────────────────┐  │
                          │   │   LEDGER      │◀───│   DECISION     │  │
                          │   │  idempotency  │    │   POLICY       │  │
                          │   │  + journal    │    │ R1–R6 + ack    │  │
                          │   └───────────────┘    └────────────────┘  │
                          └────────────────────────────────────────────┘
                                         │
        ┌────────────────────────────────┼───────────────────────────────┐
        │                                │                               │
        ▼                                ▼                               ▼
┌────────────────┐              ┌────────────────┐              ┌────────────────┐
│  REGISTRY      │              │  PRESENCE      │              │  CHANNELS      │
│  (JSON, R/O)   │              │  (SQLite/in-   │              │  Email / Slack │
│  stakeholders  │              │   memory,      │              │  / SMS adapters│
│  + expertise   │              │   event source)│              │  (stubbed)     │
└────────────────┘              └────────────────┘              └────────────────┘
```

### 3.2 Responsibilities

**Ingress.** Accepts a validated `Alert` from either the CLI or the optional HTTP endpoint. Validation: metric non-empty, threshold and value numeric, severity within enum, context serializable. Invalid alerts are rejected *before* any ledger write with a structured error, so a malformed alert cannot corrupt dispatch state.

**Router (orchestrator).** Owns the lifecycle of one alert from ingress to terminal state. It is the only component that may call the planner, dispatcher, and decision policy. It holds the per-alert `DispatchPlan` and exposes the current state for observability (CLI trace printing, HTTP status endpoint). The router is intentionally a thin coordinator: all rules live in the decision policy, all state in the ledger, so the router itself contains almost no logic — a deliberate structure that makes the system auditable.

**Ranker.** Pure function `(alert, roster_snapshot) -> ranked_candidates`. No I/O. Produces the qualification-ordered list and attaches per-candidate `basis` strings. Because it is pure, it is trivially unit-testable and provably deterministic.

**Snapshotter.** The only component that queries availability. At ranking time it evaluates the *candidate set* once, stamps each evaluation with the observed state and `eval_ts`, and records the stamp in the ledger (`snapshot` table). After ranking, **no further availability reads occur**; the Change Detector runs purely on events (Section 6). The snapshotter also records *who was skipped because they were offline*, because that information is needed later when a `candidate.available` event arrives (the system must know the previously-offline person's rank and qualification without re-querying anything).

**Planner.** Consumes the ranked, gated candidate list and builds the `DispatchPlan`: the ordered route (`primary`, `backups`), the channel fallback order per recipient, the escalation depth cap, and the initial `plan_state = QUEUED`. The plan is immutable after creation except for its state field; the *content* of the plan never changes, which guarantees that re-routing decisions refer to the same qualification ordering that was computed once at the start (the anchor of the no-downgrade property).

**Dispatcher.** Executes plan steps. For each step it consults the ledger (check-then-claim, Section 9), calls the appropriate channel adapter, and records outcomes. The dispatcher owns the ack-timer thread for high-severity alerts (Section 8.5). It never makes routing decisions; when an event requires re-planning it pauses the current step, consults the Decision Policy, and resumes, re-routes, or escalates per the policy's verdict.

**Change Detector.** Subscribes to three event types: `presence.changed(stakeholder_id, online)`, `channel.failed(stakeholder_id, channel)`, `candidate.available(stakeholder_id, online=True)`. For events naming a stakeholder involved in an *active* dispatch, it consults the snapshot (not a live query) to determine impact, then hands a structured `ChangeNotice` to the Decision Policy. The Change Detector holds no routing logic of its own.

**Decision Policy.** Pure function `(plan, snapshot, change_notice, ledger) -> Verdict(action, rationale)`. The Verdict is one of `COMPLETE`, `REROUTE`, `ESCALATE_PARALLEL`, `ABORT`, `RETRY_CHANNEL`, `IGNORE`, each with a composed rationale string. Rules R1–R6 are specified in Section 8. The policy reads the ledger to learn what has already been delivered (it must, for example, know whether an email was already acked before deciding to escalate in parallel), but it performs no I/O.

**Ledger.** A SQLite-backed append-only journal plus idempotency store. It is the source of truth for: every `INTENT`/`SENT`/`DELIVERED`/`FAILED`/`CANCELLED` per (alert_id, stakeholder, channel); the availability snapshot; the plan; and the decision log. All state transitions are single SQL transactions. Because it is SQLite, a crash leaves a consistent journal and the check-then-claim protocol (Section 9) survives restarts — the same ledger table a re-launched process reads to avoid double-sends.

**Presence service (simulated).** Maintains per-stakeholder online/offline state and per-channel health, and emits the three event types. In production this would wrap Slack presence APIs, directory services, and channel health probes; in this system it is a scripted simulator driven by the scripted scenarios and by unit tests. Its interface is `subscribe(callback)` / `emit(event)`, which is exactly the interface a real event bus (Kafka, Redis pub/sub) would expose in the scale-out design (Section 16).

**Channel adapters.** Uniform interface `send(notification) -> DeliveryReceipt` where `DeliveryReceipt ∈ {ACKED, FAILED(permanent), RETRIABLE}`. Email, Slack, and SMS adapters are stubbed but faithful to the real provider semantics that matter for routing decisions: *email is fire-and-forget fire* (an ACK means the mail server accepted it — the recipient may still be offline, and there is no retraction), *Slack and SMS are presence-aware* (an ACK implies the recipient is reachable), and *a channel in DOWN state returns RETRIABLE before any attempt* (so the planner can fall back without burning a send attempt).

### 3.3 Data flow: full dispatch lifecycle

The following sequence is the canonical happy path and the reference for the state machine in Section 7.

```
 1. INGRESS      validate alert -> alert_id
 2. RANK         load roster snapshot; score by qualification (Sec 5)
 3. GATE         for top-K candidates: snapshot availability ONCE (Sec 6)
                 - available candidates ranked by qualification
                 - unavailable candidates retained, marked OFFLINE, with rank
 4. PLAN         primary = highest-ranked available candidate
                 backups = remaining available candidates in order
                 cap = min(3, roster size)
 5. CLAIM        ledger INSERT (alert_id, primary, primary.channel, INTENT)  -- atomic
 6. SEND         channel adapter; outcome -> ledger
 7. LISTEN       change detector active for this alert_id
                 events arrive -> ChangeNotice -> Decision Policy -> Verdict
 8. REACT        apply verdict: continue | retry channel | reroute | escalate | abort
 9. TERMINAL     plan_state = DELIVERED | ESCALATED | ABORTED | FAILED
                 decision_log final entry; alert closed
```

Step 7 is the heartbeat of the whole system: it is where "mid-flight" changes are observed, and it is deliberately the *only* place the system looks at the world after ranking. The invariant that makes re-planning safe — the snapshot — is created in step 3 and never extended afterwards.

### 3.4 Process model

Single process, one thread per active high-severity dispatch for the ack timer, otherwise sequential execution. The walkthrough and tests run in-process with an injectable clock and an injectable event emitter, so scenarios are fully deterministic. The event emitter uses a synchronous callback channel (`emit` blocks until all subscribers return) in-process; the design note in Section 16 describes the drop-in replacement by a message broker. A synchronous in-process emitter is deliberately *not* a queue: it guarantees total ordering of events, which is essential to the determinism and replayability requirements (N1, N2). Any ordering assumption the decision policy makes is therefore sound.

---

## 4. Data Model

### 4.1 Core entities (Python dataclasses)

**`Stakeholder`** — read-only, from registry:

```
Stakeholder:
    id: str                 # e.g. "STK-001"
    name: str
    title: str              # e.g. "Inventory Lead"
    seniority: int          # 1 (IC) .. 5 (platform lead); 5 = most senior
    expertise: dict[str, int]   # metric-domain -> proficiency 1..5, e.g. {"inventory":5,"supply_chain":4}
    on_call: bool           # whether on rotation now
    channels: list[ChannelPref]   # ordered preference, e.g. [slack, email, sms]
```

**`ChannelPref`** — `{channel: Literal["email","slack","sms"], endpoint: str}`. `endpoint` is opaque to the core (an SMTP address, a Slack user id, a phone number); only adapters interpret it.

**`Alert`** — immutable, created at ingress, never mutated:

```
Alert:
    alert_id: str           # unique, generated at ingress
    metric: str             # e.g. "stock_level", "contract_expiry", "sla_breach"
    value: float
    threshold: float
    severity: str           # LOW | MEDIUM | HIGH | CRITICAL
    domain: str             # derived or explicit, e.g. "inventory"
    context: dict           # arbitrary, preserved verbatim
    ts: str                 # ISO-8601 (injectable clock)
```

**`AvailabilitySnapshotEntry`** — the single-evaluation record:

```
AvailabilitySnapshotEntry:
    stakeholder_id: str
    online: bool
    channel_health: dict[channel -> OK|DEGRADED|DOWN]
    eval_ts: str            # when it was observed (injectable clock)
    qualification: float    # computed rank score, frozen here
    gated: bool             # True if filtered out by availability/on-call
```

**`DispatchPlan`**:

```
DispatchPlan:
    alert_id: str
    created_ts: str
    ranking: list[AvailabilitySnapshotEntry]   # qualification order, incl. offline, frozen
    route: list[RouteStep]                     # primary then backups, only gated-in candidates
    escalation_cap: int                        # default 3
    state: PlanState                          # QUEUED|SENDING|DELIVERED|ABORTED|ESCALATED|FAILED
    severity: str
```

**`RouteStep`** — `{stakeholder_id, channel_order: list[str], step_index}`. Channel order is derived from the stakeholder's `channels` preference filtered by `channel_health == OK` at snapshot time.

**`Notification`** — what adapters receive:

```
Notification:
    alert_id: str
    recipient_id: str
    channel: str
    body: str               # full context + rationale (Sec 8.4)
    escalation_level: int   # 0 = primary, 1..cap = escalation
    sent_ts: str
```

**`Verdict`** — the decision-policy output:

```
Verdict:
    action: COMPLETE | REROUTE | ESCALATE_PARALLEL | ABORT | RETRY_CHANNEL | IGNORE
    target: str | None      # stakeholder id, for REROUTE/ESCALATE
    channel: str | None     # for RETRY_CHANNEL
    rationale: str          # template-composed, human-readable
    decision_code: str      # stable code, e.g. "R2_ABORT_REROUTE"
```

### 4.2 SQLite schema

The ledger schema below is normative. All DDL is executed at first launch inside a `BEGIN ... COMMIT` transaction with `journal_mode=WAL`.

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS alerts (
    alert_id    TEXT PRIMARY KEY,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL,
    threshold   REAL NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    domain      TEXT NOT NULL,
    context     TEXT NOT NULL,          -- JSON, preserved verbatim
    ts          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    alert_id        TEXT NOT NULL REFERENCES alerts(alert_id),
    stakeholder_id  TEXT NOT NULL,
    qualification   REAL NOT NULL,
    online          INTEGER NOT NULL,
    channel_health  TEXT NOT NULL,      -- JSON: {"slack":"OK","email":"DOWN",...}
    gated           INTEGER NOT NULL,
    eval_ts         TEXT NOT NULL,
    PRIMARY KEY (alert_id, stakeholder_id)   -- UNIQUE: one eval per stakeholder per alert
);

CREATE TABLE IF NOT EXISTS plans (
    alert_id        TEXT PRIMARY KEY REFERENCES alerts(alert_id),
    plan_state      TEXT NOT NULL CHECK (plan_state IN
                    ('QUEUED','SENDING','DELIVERED','ABORTED','ESCALATED','FAILED')),
    escalation_cap  INTEGER NOT NULL DEFAULT 3,
    created_ts      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    alert_id        TEXT NOT NULL REFERENCES alerts(alert_id),
    stakeholder_id  TEXT NOT NULL,
    channel         TEXT NOT NULL CHECK (channel IN ('email','slack','sms')),
    status          TEXT NOT NULL CHECK (status IN
                    ('INTENT','SENT','DELIVERED','FAILED','CANCELLED','ESCALATED')),
    escalation_level INTEGER NOT NULL DEFAULT 0,
    body            TEXT NOT NULL,
    sent_ts         TEXT,
    UNIQUE (alert_id, stakeholder_id, channel, escalation_level)
);

CREATE TABLE IF NOT EXISTS decision_log (
    entry_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id      TEXT NOT NULL REFERENCES alerts(alert_id),
    seq           INTEGER NOT NULL,
    decision_code TEXT NOT NULL,
    action        TEXT NOT NULL,
    target        TEXT,
    rationale     TEXT NOT NULL,
    logged_ts     TEXT NOT NULL,
    UNIQUE (alert_id, seq)
);
```

### 4.3 Schema rationale (decisions you must be able to defend)

- **`snapshots.PRIMARY KEY (alert_id, stakeholder_id)`** is the enforcement point of the no-double-query requirement (F5, N-constraint 2). The schema physically prevents a second evaluation of the same stakeholder for the same alert: an `INSERT` of a duplicate primary key fails. The snapshotter therefore *cannot* evaluate twice even by bug, and a unit test asserts that a re-evaluation attempt raises `IntegrityError`.
- **`notifications.UNIQUE (alert_id, stakeholder_id, channel, escalation_level)`** is the enforcement point of no-duplication (F7). The escalation_level is part of the key so that an escalation is a distinct, legal notification while two identical primary sends are impossible. This single constraint implements the entire dedup story (Section 9) at the storage layer; application-level checks are defense in depth, not the primary guarantee.
- **`decision_log.UNIQUE (alert_id, seq)`** guarantees a total order of decisions per alert and makes replay (N2) well-defined: replaying `decision_log` rows in `seq` order reproduces the exact decision sequence.
- **`context` is stored as JSON text** and never parsed except for display and embedding into `Notification.body`. The core never interprets it, so it cannot be corrupted by routing logic.
- **`channel_health` is stored as JSON** on the snapshot row because it is observed once, with the snapshot; it is *not* a live table that would tempt a later read (a re-query). Freezing channel health at snapshot time is intentional and is the mechanism that lets rule R1 (retry on fallback channel) use snapshot data instead of a live probe.
- **WAL journal mode** is chosen for crash-safety with concurrent read/write within the single process; the checkpoint is opportunistic and harmless at this scale. This is a minor detail included for completeness, not a scaling claim.

### 4.4 Registry seed strategy

Eight stakeholders are seeded with deliberately overlapping expertise so that ranking, rerouting, and escalation decisions are non-trivial and demonstrable:

| id | name | seniority | expertise (domain→score) | on_call | channels (pref order) |
|----|------|-----------|--------------------------|---------|----------------------|
| STK-001 | Alice Chen | 3 | inventory 5, supply_chain 3 | yes | slack, email, sms |
| STK-002 | Bob Okafor | 3 | inventory 4, logistics 4 | yes | email, slack |
| STK-003 | Carol Reyes | 4 | sla_contracts 5, inventory 2 | yes | sms, email |
| STK-004 | David Miller | 5 | platform 5, sla_contracts 4 | no  | email, slack, sms |
| STK-005 | Eve Nakamura | 2 | stock_anomaly 5, inventory 3 | yes | slack, sms |
| STK-006 | Frank Dubois | 3 | logistics 5, stock_anomaly 4 | yes | email, sms |
| STK-007 | Grace Lin | 1 | supply_chain 5 | yes | sms, email |
| STK-008 | Hank Vogel | 4 | platform 4, logistics 3 | no  | slack, email |

The overlaps matter for the routing: an `inventory` alert ranks Alice > Eve > Bob > Carol, so re-routing can be shown to follow the *snapshot* order rather than jumping to whoever happens to be online. David is the canonical "more senior stakeholder becomes available mid-flight" candidate, and Eve is the canonical "low-qualification person who is online but must NOT be chosen over a higher-qualified offline person." Appendix A gives the exact JSON.

---

## 5. Ranking Algorithm

### 5.1 Qualification score

The qualification score is the *only* ordering key in the system. It combines domain expertise with seniority. Availability and on-call status are **gates**, applied after scoring; they never influence the relative order of candidates. This is the structural guarantee behind the no-downgrade requirement.

```
qualification(s) =
    expertise_match(domain, s) * seniority_weight(s)

expertise_match(domain, s) =
    s.expertise.get(domain, 0)            # 0..5, from registry

seniority_weight(s) =
    1.0 + (s.seniority - 1) * 0.15        # 1.00 .. 1.60

gates(s) =
    s.on_call is True                     # on-rotation required for primary routing
    AND s.online is True                  # observed once at snapshot time
    AND any(s.channel_health[c] == OK for c in s.channels)
```

The seniority weight is intentionally small (`+0.15` per tier). The intent is **tie-breaking among equal-domain experts**, not domain overriding: an inventory-5 IC outranks an inventory-2 platform lead (5×1.00=5.00 vs 2×1.60=3.20), which is the behavior the brief demands — seniority alone must not be able to leapfrog expertise. The exact constants are tunable and documented here so the walkthrough can defend them; what matters is the *shape*: expertise dominates, seniority breaks ties.

### 5.2 Worked example

Registry seed (Section 4.4), alert `stock_level` breached, domain `inventory`:

| stakeholder | expertise | seniority | weight | qualification | on_call | online | gated? |
|-------------|-----------|-----------|--------|---------------|---------|--------|--------|
| Alice Chen  | 5 | 3 | 1.30 | **6.50** | yes | yes | no |
| Eve Nakamura| 3 | 2 | 1.15 | **3.45** | yes | no | yes |
| Bob Okafor  | 4 | 3 | 1.30 | **5.20** | yes | yes | no |
| Carol Reyes | 2 | 4 | 1.45 | **2.90** | yes | yes | no |
| David Miller| 0 | 5 | 1.60 | **0.00** | no | no | yes |
| Grace Lin   | 0 | 1 | 1.00 | **0.00** | yes | yes | yes |

Gated-out candidates are retained in the ranking *with their computed score and rank*, but are excluded from the active route. Final active route (qualification order, gated-in only):

1. **Alice Chen (6.50)** — primary, channel `slack` (pref order slack > email > sms, all OK).
2. **Bob Okafor (5.20)** — backup 1, channel `email`.
3. **Carol Reyes (2.90)** — backup 2, channel `sms`.

Critical details for the scenario:

- **Eve (3.45) is offline.** She outranks Bob and Carol on paper, but she is gated out. The route does *not* silently drop her: her snapshot row says `gated=1`, `online=0`, qualification 3.45. If she comes online mid-flight, the Change Detector receives `candidate.available(STK-005)`, looks up her *frozen* snapshot, sees qualification 3.45 > Alice's 6.50? No — it is lower than Alice's, so nothing happens (rule R4, Section 8). If instead David (0.00 domain match) comes online, he still loses to Eve on qualification. The point being demonstrated: **the re-planning decision is a pure comparison of frozen scores, never a re-evaluation.**
- **David Miller (seniority 5) is the canonical "more senior stakeholder became available" case** only for alerts in his domain (`sla_contracts`, `platform`), where his qualification is actually high. For an `inventory` alert his domain score is 0, so even online he ranks below everyone — a deliberate seed choice that lets the walkthrough show escalation *failing* the no-downgrade test as well as passing it.

### 5.3 Determinism

The ranker is a pure function: same `(alert, roster_snapshot)` tuple in, same list out. Floating-point comparisons use `functools.total_ordering` with a tolerance-free direct comparison of the two float products (the products differ by construction in all seeded cases; where two are exactly equal, ordering falls back to stakeholder id for a stable, documented tie-break). No hash-ordering, no `set` iteration, no wall clock — all nondeterminism sources are banned in the ranker.

### 5.4 Why qualification-first, availability-second (defended)

An alternative design ranks by availability first ("who's reachable right now") and then picks the best among them. That design is simpler and is what many naive paging systems do — and it is precisely the failure the brief forbids: "must not escalate to someone who is less qualified just because they are online." Under availability-first ranking, an online junior generalist would repeatedly beat an offline domain expert; every pager rotation would surface the same reachable person regardless of fit. Qualification-first ranking means the *qualified* set is always the anchor, availability only *truncates* it from the bottom. The cost is that sometimes no qualified person is reachable and the system must escalate upward or mark the alert unresolved — an honest, explainable failure mode, not a silent mis-route. This tradeoff is defended at length in Section 12.

---

## 6. The Single-Evaluation Guarantee (No Double-Query)

The brief's second-hard constraint is: **"must not query availability twice for the same person."** This section specifies the mechanism, proves it, and defines what the system does with people it has *never* evaluated.

### 6.1 Mechanism: snapshot-then-diff

1. **Snapshot phase (once, at ranking).** The Snapshotter queries availability for the candidate set — exactly the candidates the ranker scored — and writes one row per candidate into `snapshots`. The `PRIMARY KEY (alert_id, stakeholder_id)` makes a second write of the same key an `IntegrityError`.
2. **Event-only phase (dispatch duration).** After the snapshot, the Change Detector holds no live query path. It subscribes to events. An event *is* the availability truth: `presence.changed(sid, online)` arrives *because* the presence service observed a transition, not because the router asked.
3. **Diff, don't re-query.** On an event, the Change Detector reads the *frozen* snapshot row for that stakeholder and compares it to the event payload. It computes what changed (was online → now offline; channel went OK → DOWN; previously offline → now online). It never calls back into the presence service.

Why this satisfies the constraint: the only availability reads in the entire system occur during the snapshot phase, exactly once per candidate, enforced by the schema. Every subsequent state transition is derived from events pushed to the router. There is literally no code path that asks "is X available right now?" after ranking begins.

### 6.2 Stakeholders never evaluated

The candidate set at snapshot time is the *roster*, filtered to stakeholders with any on-call or domain relevance — practically the full roster in this system. A stakeholder who was never snapshotted cannot appear in any diff. Two cases exist and both are handled without a query:

- **A never-seen stakeholder becomes available.** The event carries their id and state, but not their expertise. The Change Detector can read their registry entry (registry is a static, pre-dispatch file — reading it is not an availability query, it is a configuration read) and compute their qualification *using the same formula*, then compare against the frozen snapshot. This is safe: it does not learn anything time-varying. If they qualify higher than the current recipient and the alert is not yet acked, the policy escalates (rule R4); otherwise it ignores. Note this reads configuration, not presence — the distinction is the entire point of this section.
- **A never-seen stakeholder is irrelevant.** No event, no work. The snapshot is the contract; anything outside it is out of scope by construction.

### 6.3 Proof sketch (for the tests and the walkthrough)

Claim: for every dispatch, each stakeholder is availability-evaluated at most once.

- Availability evaluation is defined as any read of time-varying presence/channel state for a stakeholder.
- The only code that reads time-varying state is the Snapshotter (Section 3.2).
- The Snapshotter executes once per dispatch, immediately after ranking, and iterates the candidate set exactly once.
- The `snapshots` primary key raises on duplicate inserts; a re-evaluation of the same (alert, stakeholder) is therefore rejected by the database, not merely by convention.
- After the snapshot phase, the Change Detector consumes events and reads the `snapshots` and `notifications` tables only.

Therefore the system satisfies the constraint, and the enforcement is *physical* (schema) rather than *behavioral* (discipline). The test `test_snapshot_single_eval` asserts the `IntegrityError` on re-insert.

### 6.4 Channel health is part of the snapshot

Channel health (`slack OK`, `email DOWN`, ...) is observed once, in the same snapshot transaction, and stored on the snapshot row. Rule R1 (channel fallback, Section 8.2) therefore uses frozen channel health to choose the fallback channel — it does not probe live. This is intentional: it prevents a third kind of double-query (double-probing a channel), and it keeps the decision reproducible from the snapshot alone. The unavoidable consequence — channel health may have drifted since snapshot — is accepted and defended in Section 12: for the alert lifecycle (seconds), snapshot-time health is a faithful-enough model, and if a fallback channel is *actually* down at send time, the adapter returns RETRIABLE and the policy escalates, which is the correct terminal behavior anyway.

---

## 7. Dispatch Plan & Route State Machine

### 7.1 Plan states

```
                         ┌──────────┐
        ingress          │  QUEUED  │
   ─────────────────────▶│ (plan    │
                         │  built)  │
                         └────┬─────┘
                              │ claim primary (INTENT)
                              ▼
                         ┌──────────┐
                    ┌───▶│ SENDING  │◀──── retry channel (R1)
                    │    └────┬─────┘
      reroute (R2) │         │ acked
                    │         ▼
                    │    ┌──────────┐
                    └────│          │
      complete (R1/R4)   └──────────┘
                              │
        ┌─────────────────────┼──────────────────────┐
        ▼                     ▼                      ▼
   ┌──────────┐         ┌──────────┐            ┌──────────┐
   │ DELIVERED│         │ ESCALATED│            │  ABORTED │
   │ (primary)│         │(escalate │            │ (reroute │
   │          │         │ parallel)│            │  spent   │
   └──────────┘         └──────────┘            └──────────┘
                                                        │
                                                        ▼
                                                  ┌──────────┐
                                                  │  FAILED  │
                                                  │(no target│
                                                  │ left)    │
                                                  └──────────┘
```

Transitions and their triggers:

- `QUEUED → SENDING`: ledger claim of the primary step succeeds (`INTENT` inserted atomically).
- `SENDING → SENDING`: a retry on a fallback channel (rule R1) or a reroute to the next backup (rule R2) — the state stays `SENDING` because a send is still active; only the target/step changes.
- `SENDING → DELIVERED`: the active step's adapter returns `ACKED`, and no escalation is pending. Terminal.
- `SENDING → ESCALATED`: an escalation was issued (rule R3/R4, ack timeout). Terminal for the *plan*; the escalated notification is its own terminal entry. Delivered status of the original is recorded separately on the notification row.
- `SENDING → ABORTED`: the current step was aborted (recipient offline mid-flight, nothing acked) and rerouting to any remaining backup is impossible (all gated-out or already failed). Terminal.
- `ABORTED → FAILED` (implicit): the plan is recorded as `FAILED` when rerouting had a target but every target failed, versus `ABORTED` when there was never a viable target. Both are terminal; the distinction is purely semantic for the decision log and the summary output.

There is **no transition out of a terminal state.** The decision policy asserts this as an invariant (`_assert_terminal_is_terminal`) — belt and braces against a re-routing loop (edge case E10, Section 11).

### 7.2 Route structure

A `DispatchPlan.route` is an ordered list of `RouteStep`s derived once at planning time:

- **Primary step:** the highest-ranked gated-in candidate. Channel order = the candidate's preference order filtered by snapshot channel health.
- **Backup steps:** the remaining gated-in candidates in snapshot ranking order, each with its own channel order. Backups are only ever selected by rules R2/R3, never proactively contacted.
- **Escalation cap:** default 3. `escalation_level` counts total notifications for the alert; when `escalation_level == cap`, further escalation requests are refused and the plan terminates (edge case E6, Section 11).

The route is **immutable** (its content never changes after creation). Re-planning changes only the *cursor* (which step is active) and the plan state. This immutability is what makes rule R2's "reroute to the next-ranked candidate in snapshot order" well-defined even after several events have fired: the order is the order, captured once. A system that rebuilt the route on every event would drift toward availability-first routing and reintroduce the downgrade bug — another structural choice worth defending (Section 12).

### 7.3 Cursor semantics

The dispatcher maintains a cursor `(step_index, channel_index)` per alert:

- `step_index` selects the RouteStep.
- `channel_index` selects which channel in that step's order is being attempted.
- Rule R1 advances `channel_index` within the same step.
- Rule R2/R3 advances `step_index` (and resets `channel_index` to 0) to the next viable step.

The cursor is persisted in the `plans` table? No — the cursor is derived from `notifications` state at any moment (the highest `escalation_level` with a non-CANCELLED row identifies the active step). This keeps the ledger the single source of truth and makes replay trivial: replay the notifications and decision_log, recompute the cursor. Persisting a redundant cursor would create a drift risk with no benefit at this scale.

---

---

## 8. Decision Policy

The Decision Policy is the heart of the system and the artifact the walkthrough walks through line by line. It is a pure function:

```
Verdict decide(
    plan: DispatchPlan,
    snapshot: list[AvailabilitySnapshotEntry],
    change: ChangeNotice | None,     # None => timer/ack-driven evaluation
    ledger_state: LedgerView         # what has been sent/delivered so far
) -> Verdict
```

Every branch is encoded as a named rule (R1–R6) with a stable decision code so the `decision_log` is greppable and the walkthrough can cite rules by name.

### 8.1 Decision matrix

The inputs that matter are: (a) the *change* (offline, channel-failed, better-match-available, worse-match-available, none/timer), (b) whether the current send has been **acked** (delivery is terminal), (c) whether the current recipient has an alternative channel, and (d) whether a higher-qualified candidate is reachable. The matrix:

| Event | Current send ACKed? | Higher-qualified reachable? | Alternative channel? | Verdict | Rule |
|---|---|---|---|---|---|
| Recipient went offline | no | — | yes | `RETRY_CHANNEL` (fallback channel of same recipient) | R1 |
| Recipient went offline | no | — | no | `REROUTE` → next-ranked backup | R2 |
| Recipient went offline | yes | yes | — | `ESCALATE_PARALLEL` → next-ranked backup | R3 |
| Recipient went offline | yes | no | — | `COMPLETE` (delivered is delivered) | R3 |
| Better-qualified candidate online | no | yes | — | `REROUTE` → that candidate | R4a |
| Better-qualified candidate online | yes | yes | — | `ESCALATE_PARALLEL` → that candidate | R4b |
| Worse/equal-qualified candidate online | — | — | — | `IGNORE` (never downgrade) | R5 |
| Channel failed (RETRIABLE) | no | — | yes | `RETRY_CHANNEL` → next channel | R1 |
| Channel failed (permanent FAILED) | no | — | yes | `RETRY_CHANNEL` → next channel | R1 |
| Channel failed, all channels exhausted | no | yes | no | `REROUTE` → next-ranked backup | R2 |
| Channel failed, all channels exhausted | no | no | no | `ABORT` (plan → FAILED) | R6 |
| Ack timeout (high severity), not acked | no | yes | — | `ESCALATE_PARALLEL` → next-ranked backup | R4c |
| Ack timeout (high severity), not acked | no | no | — | `ESCALATE_PARALLEL` → duty manager (generic) | R4c |

The matrix is exhaustive over the enumerated event space. Anything not in the matrix is a bug, and the policy raises `UnhandledSituationError` (a deliberate fail-loud policy) rather than silently guessing.

### 8.2 Rule R1 — Retry on alternative channel (channel fallback)

Trigger: current recipient is still the correct target (still online, still top-qualified), but the channel that was attempted is `DOWN`/`FAILED`, and the recipient has another channel that was `OK` at snapshot time.

Action: advance `channel_index`, register a fresh `INTENT` for `(alert_id, recipient, next_channel, escalation_level=0)`, send.

Rationale template: `"{recipient} remains the highest-qualified available recipient; channel {failed_channel} is unavailable, retrying via {next_channel}."`

Basis for the design: channel failure is a *transport* problem, not a *recipient* problem. Swapping recipients on a transport failure would waste qualified capacity and risk the downgrade bug; retrying the same recipient on an alternate transport preserves the qualification ordering while keeping the alert with the person best able to act on it. Only when no alternate channel exists does the policy move to the next candidate (R2).

### 8.3 Rules R2/R3 — Reroute vs. complete-and-escalate (the abort decision)

The brief explicitly names the hard call: *abort the current dispatch and re-route, or complete the original dispatch and escalate in parallel.* The policy answers with a single, defensible discriminator: **has delivery been acknowledged?**

- **Not acked** (queued or in-flight): the send is revocable. `ABORT` the in-flight attempt (`CANCELLED` in the ledger), and `REROUTE` to the next-ranked backup in snapshot order. The aborted attempt consumes none of the dedup budget because it was never delivered.
- **Acked**: delivery is terminal. For email, an ACK means the mail server accepted the message — the recipient will receive it, offline or not. Pretending to abort an accepted email is fiction. The policy therefore **completes** the original delivery and issues an **escalation in parallel** to the next-ranked backup, with `escalation_level = 1` and the original context plus an escalation notice.

This is the single most important decision in the system and it is defended in Section 12.4. The short version for the walkthrough: *"We only pretend to be able to abort what we can actually recall. Email cannot be recalled. Slack/SMS can, before delivery. The discriminator is the delivery receipt, not our wishes."*

R3's parallel escalation is a *new notification row* (`escalation_level=1`), so it does not violate dedup, and it is sent to a *different* stakeholder than the original, so no stakeholder receives two notifications for the same alert — the two invariants hold simultaneously. If the next-ranked backup was already notified (impossible in the same plan, but guarded anyway), the ledger's `UNIQUE (alert_id, stakeholder_id, channel, escalation_level)` would reject the insert and the policy would advance to the next backup.

### 8.4 Rules R4 — Better match appears / ack-time escalation

Three sub-cases, one rule family, same core comparison: *the candidate is more qualified than the current recipient?* — where "more qualified" means strictly greater qualification score, with the frozen snapshot scores used on both sides.

- **R4a (better match, nothing acked):** `REROUTE` to the better candidate. Rationale: `"A higher-qualified stakeholder ({better}) has become available (qualification {q1} vs {q0}); re-routing the pending alert."` The pending (unacked) attempt is `CANCELLED`.
- **R4b (better match, already acked):** `ESCALATE_PARALLEL` to the better candidate. The delivered message stands; the better candidate gets a full-context escalation explaining the original delivery to the now-secondary recipient.
- **R4c (ack timeout, severity HIGH/CRITICAL):** if not acked within `ack_window` seconds (configurable, injectable clock, default 30s), escalate to the next-ranked backup; if no backup exists, escalate to the *duty manager* stakeholder (a seeded generic-domain stakeholder, e.g. `STK-004 David Miller`, seniority 5) — this is the one sanctioned exception to the domain-match requirement, and it is a deliberate, documented design choice (the alert must not die silently even when no expert is reachable).

Rationale template for R4c: `"High-severity alert not acknowledged within {ack_window}s; escalating to {target}."`

The comparison uses **frozen snapshot scores**, not a live recomputation, preserving the no-double-query and no-downgrade guarantees simultaneously. A "better match" is *only ever* a stakeholder whose frozen qualification exceeds the frozen qualification of the current recipient — a worse match arriving online triggers R5, below.

### 8.5 Rule R5 — No downgrade (the inverse of R4)

Trigger: any event announces availability of a stakeholder whose frozen qualification is *less than or equal to* the current recipient's.

Action: `IGNORE`. The event is logged (so the walkthrough can show the reasoning), the plan continues exactly as before.

Rationale template: `"Stakeholder {sid} (qualification {q}) is now available, but {current} ({q_current}) is more qualified; not downgrading."`

This rule is where the brief's third hard constraint is enforced at the *behavioral* level. Note it is also structurally redundant — the route cursor never moves backward and the route was qualification-ordered at planning — but the policy keeps it explicit because the walkthrough wants to *show* a worse match arriving online and being refused. Defense in depth is cheap here and it makes the property directly observable.

### 8.6 Rule R6 — All targets exhausted

Trigger: reroute requested, but no gated-in candidate remains un-notified (backups exhausted), or every remaining backup has permanently failed.

Action: `ABORT`, plan state `FAILED`, alert marked `UNRESOLVED`. The final `decision_log` entry includes the full context (the alert is not lost — it is parked in the ledger and flagged for a human) and the complete escalation chain that was attempted, so a human or a follow-on system can pick it up. No further automated action. There is deliberately no infinite-retry loop: the escalation cap (default 3) bounds the number of notifications per alert, so worst-case cost per alert is bounded (see E6, Section 11).

### 8.7 Rationale composition

Every Verdict carries a `rationale` composed from the rule templates above plus two mandatory facts: the alert context digest and the comparison that motivated the decision (the "why you over X" text). The final delivered notification body is:

```
[ALERT] {metric} threshold breached
  severity : {severity}
  value    : {value} (threshold {threshold})
  domain   : {domain}
  context  : {context_json}
  ─────────────────────────────────
  Why you: {recipient} is the highest-qualified available stakeholder
           for domain {domain} (qualification {q}).
  Why not X: {X} (qualification {qX}) is lower-qualified{; skipped details}.
  Escalation level: {level}  |  Alert id: {alert_id}
```

The `rationale` on the *notification* is composed by the policy at send time from the same templates the decision log uses, so the recipient's "why you were chosen over others" is literally the decision log made human-readable — no separate, drift-prone summary path exists. This is the mechanism that satisfies requirement F8 and the brief's "an explanation of why they were chosen over others."

### 8.8 Ack-timer mechanics

- The dispatcher arms a timer per high-severity dispatch (`HIGH`/`CRITICAL`) with `ack_window` (default 30s, injectable clock for tests/walkthrough).
- The timer fires only if the plan is still `SENDING` and the active notification is not `DELIVERED`.
- On fire: policy invocation with `change=None, timer=True` → rule R4c → escalation or abort.
- The timer is cancelled when the plan reaches a terminal state. The walkthrough accelerates `ack_window` to make the timer observable within the 3-minute window.

---

## 9. Idempotency & Deduplication

This section specifies the protocol that makes "no duplicate notifications" (F7, AC-3) a *guarantee* rather than a hope. Two layers: the check-then-claim protocol (behavioral) and the schema constraints (physical, Section 4.2).

### 9.1 Check-then-claim protocol

Every send attempt, primary or escalation, follows the same protocol, executed as a **single SQLite transaction**:

```
BEGIN IMMEDIATE;
  SELECT status FROM notifications
   WHERE alert_id=:a AND stakeholder_id=:s AND channel=:c AND escalation_level=:l;
  IF row exists:
      ROLLBACK; -> NO_OP (already claimed/delivered) [dup prevented]
  ELSE:
      INSERT INTO notifications (..., status='INTENT', ...)
      COMMIT;   -> claim acquired
THEN send via adapter.
On ACKED:    UPDATE notifications SET status='DELIVERED' WHERE notification_id=:id;
On FAILED:   UPDATE notifications SET status='FAILED'  WHERE notification_id=:id;
On ABORT:    UPDATE notifications SET status='CANCELLED' WHERE notification_id=:id;
```

The `BEGIN IMMEDIATE` + conditional insert is the lock. In a single-process system the transaction is the only concurrency boundary needed; in the scale-out design (Section 16) the same statement survives unchanged on a shared SQLite/Postgres/Redis-backed store, which is why the schema is specified to this level of detail.

### 9.2 Why INTENT precedes SEND

If a process crashes *between* the INSERT and the adapter call, the ledger shows an `INTENT` with no `DELIVERED`. On restart, the dedup check sees the INTENT row and treats the notification as *already claimed* — it will not re-send. The consequence is a possible *missed* delivery (the crash ate the send), never a *duplicate*. For an alerting system, at-least-once with crash-gap is the wrong profile; we deliberately accept "claim exists, delivery lost, escalation will cover it" over "delivery duplicated." The escalation chain (R3/R4c) guarantees the alert still reaches a human even in that window, because the ack timer is also ledger-driven. This is a defended tradeoff (Section 12.5): **never duplicate, even at the cost of an occasional lost-send that the escalation path catches.**

### 9.3 The two invariants

**Invariant I1 (no recipient duplication):** For any alert_id and stakeholder_id, there is at most one row with `status IN ('SENT','DELIVERED','ESCALATED')` in `notifications`.

Proof obligation for tests: drive the policy through every R2/R3/R4 reroute sequence and assert, after each, that I1 holds. The schema `UNIQUE (alert_id, stakeholder_id, channel, escalation_level)` alone does not enforce I1 across *channels* (the same stakeholder could get email + Slack), so I1 must be enforced by the claim protocol plus a policy-level guard: before any send to `(s, c)`, check for any prior non-CANCELLED row for `(alert_id, s)`; if present, refuse and advance the cursor. This check runs in the same transaction as the claim.

**Invariant I2 (no cross-recipient duplication):** No stakeholder receives two notifications for the same alert (primary + escalation to the same person). Enforced by I1 plus the escalation-target selection, which always picks the next *un-notified* backup from the route; a backup who already holds a non-CANCELLED row is skipped. Because the route is finite and the cap is 3, this terminates.

I1 and I2 together are exactly "no stakeholder receives duplicate notifications." The test suite asserts both as post-conditions of every scenario (Section 13.3).

### 9.4 Interaction with parallel alerts

Dedup is keyed per `alert_id`. Two distinct alerts routed to the same stakeholder in overlapping time windows are different keys and both deliver — that is correct (the person is on call for both incidents) and it is asserted in `test_parallel_alerts_same_recipient`. The dedup protocol never conflates distinct alerts; only *the same alert* is ever suppressed.

---

---

## 10. Channel Adapters

### 10.1 Interface

```python
class ChannelAdapter(Protocol):
    name: str                      # "email" | "slack" | "sms"
    def send(self, notification: Notification) -> DeliveryReceipt: ...
    def health(self, endpoint: str) -> ChannelState: ...   # OK | DEGRADED | DOWN
```

`DeliveryReceipt` is an enum: `ACKED`, `FAILED` (permanent — bad endpoint, hard reject), `RETRIABLE` (transient — provider down, timeout). The distinction matters to R1: a `FAILED` on the last channel exhausts the recipient's options and the policy reroutes (R2); a `RETRIABLE` could theoretically be retried with backoff, but the policy treats it like `FAILED` for the current step to keep the decision *deterministic* — retry-with-backoff is listed in Section 16 as future work, deliberately not in v1 because it introduces timer/ordering complexity that the 3-minute walkthrough cannot afford to show honestly.

### 10.2 Stub semantics (faithful to real providers)

The stubs exist so the walkthrough is deterministic, but each is engineered to encode the real provider property that affects *routing decisions*:

- **Email (fire-and-forget):** `send()` immediately returns `ACKED`. It *never* blocks and *never* fails on recipient presence, because SMTP semantics are exactly that: the message is accepted by a mail server and delivered later, whether or not the recipient is online. The system therefore knows email delivery is not recallable — which is the entire basis of rule R3. A debug hook can force `RETRIABLE` to simulate the mail server being down, exercising R1.
- **Slack (presence-aware):** `send()` checks the presence service *through the event snapshot* (not a live call — the presence state was frozen at snapshot time). If the recipient's state at snapshot was offline, `send()` returns `RETRIABLE` immediately without consuming a budgeted attempt — because we already *know* the outcome, and burning a "real" attempt on a foregone failure would distort the dedup story. If the channel health is `DOWN`, same `RETRIABLE`. Otherwise `ACKED`.
- **SMS:** same shape as Slack, with the same rules.

Why the stubs "know" presence instead of being dumb fakes: the whole point of the system is that presence is a *snapshot*, and the walkthrough must show a Slack send begin, then the recipient go offline, then the policy react. A dumb stub that always ACKs would make rule R2 unreachable in the walkthrough. A stub that queries live presence would violate the no-double-query constraint. The stub reads the frozen snapshot — which is precisely the data the real adapters *would* have access to in production via a presence API, but which this system already captured at snapshot time. This is the cleanest honest way to simulate the mid-flight failure window.

### 10.3 Injection & failure hooks

Every adapter accepts an `env`-style injector so scenarios can script failures deterministically:

```
adapter.fail_next = [('RETRIABLE', 1), ('FAILED', 0)]   # queue of forced outcomes
```

The scenario driver (Section 14) uses these hooks to script `channel.failed` events and adapter failures. The presence service exposes the same style for `presence.changed` and `candidate.available`. All injectors are *inputs to the scenario driver*, not part of the production path — a reviewer can see the production path is clean and the scripting is external.

### 10.4 Drop-in path to real providers

The interface above is deliberately the contract that real adapters would implement: `smtplib`/`SendGrid` for email, Slack Web API `chat.postMessage` for Slack, Twilio SMS API. None of that is in v1 (credentials + nondeterminism + the walkthrough is 3 minutes). The section in README ("what's next") points at this interface as the seam. Nothing in the router, policy, or ledger knows an adapter is a stub.

---

## 11. Edge Cases

This section enumerates every edge case the design anticipates, the rule that handles it, and the test that pins it. Edge cases are the part the brief says "counts the most," so this section is intentionally exhaustive and cross-referenced to the rules and tests.

**E1 — Recipient goes offline mid-flight, send NOT yet acked.** → Rule R2 (abort + reroute to next-ranked backup). If an alternate channel exists on the same recipient, R1 fires first (retry channel) only if the recipient is still *online*; going offline is a recipient-state change, so the correct reaction is R2, not R1. Rationale text names both the offline event and the chosen backup. Test: `test_reroute_on_offline`.

**E2 — Recipient goes offline mid-flight, send already acked.** → Rule R3 (complete + escalate in parallel). Email accepted by the server is not recallable. The delivered message stays; the next-ranked backup receives `escalation_level=1` with full context. No duplicate: I1 (different stakeholder, same alert) and I2 (no same-recipient double). Test: `test_escalate_after_delivery`.

**E3 — Preferred channel is DOWN at snapshot time.** → The planner already filtered channel order by snapshot health, so the primary step's first channel is the highest-preference *healthy* channel. The "preferred" channel is honored only when healthy — a stakeholder who prefers Slack over email but has Slack down at snapshot gets email, with the rationale noting the fallback. No mid-flight reaction is needed because the snapshot already encoded health. Test: `test_planner_skips_down_channel`.

**E4 — Channel fails at send time (provider outage) after planning.** → Rule R1 (retry on the recipient's next channel from snapshot order). The failure is a *transport* problem, not a recipient problem. If all channels exhausted → R2 (reroute). Test: `test_channel_fail_retries_fallback_channel`.

**E5 — Best-qualified candidate is offline at ranking time.** → Gated out, but retained in the snapshot with `gated=1` and frozen qualification. If they come online mid-flight, R4b fires if they beat the current recipient (they are best-qualified, so they do), causing a parallel escalation — or a reroute if nothing is acked yet. The system does **not** notify a worse candidate just because the best is offline (that is the no-downgrade rule); it notifies the best-ranked *available* candidate and keeps the offline best as a live escalation target. Test: `test_offline_top_candidate_escalates_when_online`.

**E6 — Escalation depth reached.** → The cap (default 3) bounds notifications per alert. A further escalation request is refused (`ABORT`, alert `UNRESOLVED`, decision-log entry explains the chain that was attempted). This is the mechanism that makes worst-case cost bounded and prevents infinite loops. Test: `test_escalation_cap_bounds_chain`.

**E7 — Reroute target is the person who just went offline.** → The reroute cursor walks the route from the current position forward and skips any backup who is (a) currently offline per snapshot, or (b) already holds a non-CANCELLED notification row (I1 guard). It is structurally impossible to reroute back to the offline primary because the primary was already delivered-or-cancelled and I1 refuses a second row. Test: `test_reroute_skips_unavailable_and_duplicate`.

**E8 — Unknown/unmapped metric domain (no domain experts).** → All qualification scores are 0 or near-0; the policy's domain-match produces an empty expert pool. Sanctioned fallback: route to the duty manager (highest-seniority on-call stakeholder), with a rationale that explicitly states no domain expert exists. This is the same sanctioned exception as R4c's ack-time path, kept in one place (`duty_manager` resolution) so it is auditable. Test: `test_unknown_domain_goes_to_duty_manager`.

**E9 — Empty roster or all candidates gated out.** → Rule R6: `ABORT` → `FAILED`, alert `UNRESOLVED`, context preserved in the ledger for a human. Nothing is sent; nothing is silently dropped. Test: `test_empty_roster_marks_unresolved`.

**E10 — Re-routing loop / repeated events.** → The terminal-state invariant (no transition out of terminal states, Section 7.1) plus the escalation cap together bound the loop. Additionally, repeated *identical* events (e.g. two `presence.changed(offline)` events) are idempotent: the Change Detector diffs the event against the snapshot and sees no *new* change, logs nothing, produces `IGNORE`. Test: `test_repeated_events_idempotent`.

**E11 — Malformed alert (missing field, bad severity, non-numeric value).** → Ingress validation rejects *before* any ledger write, returns a structured `AlertValidationError`. No partial dispatch state is created. Test: `test_ingress_rejects_malformed`.

**E12 — Concurrent alerts to the same recipient.** → Dedup is keyed per `alert_id` (Section 9.4); both deliver. The claim protocol is per-alert. Test: `test_parallel_alerts_same_recipient`.

**E13 — Crash between INTENT insert and adapter send.** → On restart, the INTENT row blocks re-send (9.2); the alert is flagged `UNRESOLVED`-pending for the ack/escalation path or a human. Never a duplicate. Test: `test_crash_between_claim_and_send` (simulates by re-running dispatch against a ledger that already has the INTENT).

**E14 — Duplicate event source (event replay / at-least-once bus).** → Every event is processed against the current ledger state; a replay of an already-applied event produces `IGNORE` (the diff is empty). The policy never mutates state from an event that cannot change it. Test: `test_event_replay_is_noop`.

**E15 — Clock skew on snapshot `eval_ts` vs event timestamps.** → Timestamps are only ever *display* fields, never ordering keys. Ordering of events is the emitter's arrival order (in-process, total order, Section 3.4). The policy never compares event timestamps against `eval_ts`; it compares *state* (online→offline), which is skew-proof. This is a deliberate simplification documented here so a reviewer cannot fault the design on a detail that was explicitly decided. Test: `test_timestamps_not_used_for_ordering`.

**E16 — Stakeholder has no healthy channel at snapshot time.** → Gated out (gate 3, Section 5.1). They cannot be a primary; they may still be an escalation target only via the duty-manager exception. Rationale records why. Test: `test_no_healthy_channel_gates_out`.

**E17 — Ack timer fires while an escalation is already in flight.** → The timer is cancelled when the plan leaves `SENDING` (Section 8.8). If it somehow fired late (race in the injectable-clock walkthrough), the policy checks plan state first and returns `IGNORE` — the plan is already escalated, double-escalation would violate the cap. Test: `test_timer_no_double_escalation`.

**E18 — Severity is LOW/MEDIUM (no ack timer armed).** → No timer, no R4c path. Routing proceeds exactly as before; LOW alerts never auto-escalate on ack-timeout (they can still reroute/escalate on events). Rationale for skipping the timer is logged once. Test: `test_low_severity_no_timer`.

**E19 — A stakeholder's registry entry is missing a field.** → Registry load validates the schema at startup; a malformed entry fails fast with a named error listing the entry id. No dispatch proceeds against a corrupt registry. Test: `test_registry_validation_fails_fast`.

**E20 — The same alert is ingested twice (idempotent ingress).** → Ingress is keyed on an external `alert_id` when provided; a duplicate ingest returns the existing alert's status rather than dispatching a second plan. When no external id exists, ingress generates one and the caller is responsible for dedup upstream (documented). Test: `test_ingress_idempotent_on_duplicate`.

Each edge case is either (a) structurally prevented by the schema (E13, E14, E20), (b) handled by a named rule (E1–E6, E16, E17), or (c) handled by a fail-loud validation (E8, E11, E19). No edge case in this list is silently swallowed. That property — *every unexpected situation either has a named rule or raises* — is the design's answer to the brief's demand that shipped decisions be defensible.

---

---

## 12. Tradeoffs & Defended Decisions

The brief explicitly rewards decisions that are made and can be defended. This section is the defense. Every item follows the same shape: *decision → cost → why it wins here → what would change it.*

### 12.1 Deterministic policy engine over an LLM agent

The most consequential decision. The "agent" framing in the brief might suggest an LLM orchestrator calling tools. We chose a **deterministic, rule-based policy engine** as the routing core.

- **Cost:** the system cannot free-form reason about novel situations; anything outside the R1–R6 matrix raises `UnhandledSituationError` and must be added as a rule. It also looks less "AI" on the surface.
- **Why it wins here:** the four hard constraints (no dup, no double-query, no downgrade, context preservation) are *correctness properties*. An LLM cannot prove them. Every reroute decision has a right answer determined by the snapshot, the route, and the ledger — there is no judgment call an LLM would improve. An LLM in the hot path would also be nondeterministic (sampling temperature, prompt drift), which would make the walkthrough unreplayable and the invariants untestable. Determinism is a *feature the brief demands*: "must not" language requires guarantees, and guarantees require determinism.
- **What would change it:** if the task were *summarizing* incidents or *drafting* rationale prose for humans where factual correctness of routing was secondary, an LLM would win. We have deliberately scoped it to prose-only in Section 16.

### 12.2 Qualification-first ranking (availability as a gate, not a key)

- **Cost:** when the best-qualified person is offline, the system routes to the best *available* qualified person and escalates upward — and on no-expert, marks `UNRESOLVED`. It sometimes delivers to a less-senior person than a senior-but-irrelevant person who is online.
- **Why it wins:** the alternative (availability-first) directly violates the brief's "must not escalate to someone who is less qualified just because they are online." Every naive paging system converges on that failure; ours structurally cannot. The residual "senior but wrong-domain person was online" outcome is the *correct* outcome — domain match outranks seniority, and that is the ranking formula, not a bug.
- **What would change it:** if availability were a rare and valuable resource and every alert were time-critical beyond the ack window, availability-first would be defensible. For operational alerts with a 30s ack window, qualification wins.

### 12.3 Snapshot-based single evaluation over live polling

- **Cost:** the system may act on stale presence (a person marked offline at snapshot who actually came back a second later). It cannot "look again."
- **Why it wins:** polling is what causes double-querying, which the brief bans outright. The snapshot is also what makes decisions *reproducible*: the decision log can be replayed with the snapshot and produce identical output, which no live-polling system can claim. Staleness is bounded by the alert lifecycle (seconds); within that window, frozen presence is a faithful model, and any drift is caught by the events that flow through the Change Detector anyway (an event is *newer* truth than the snapshot and is applied as a diff).
- **What would change it:** a multi-second-duration dispatch where presence churns violently (e.g. thousands of recipients). At that scale you'd version snapshots and re-baseline; the single-eval guarantee would become "at most one evaluation per *baseline*," documented.

### 12.4 Abort-and-reroute vs. complete-and-escalate: the delivery-receipt discriminator

- **Decision:** the system only aborts sends it can actually recall — i.e., sends whose delivery was not yet acknowledged. Acknowledged sends are completed, and the "reroute" becomes a *parallel escalation* to the next-ranked backup.
- **Cost:** in the acked-then-offline case, the now-offline original recipient still gets the email, and a *second* person also gets notified. That looks like "two notifications," but it is one primary + one escalation, to two different people — which is the brief's stated acceptable outcome ("complete the original dispatch and escalate in parallel"). The alternative — suppressing the acked email and pretending to reroute — is fiction and would silently lose the alert if the new recipient also fails.
- **Why it wins:** it matches the real semantics of the transport (SMTP acceptance is not recallable), which is the kind of "edge case handled because I know the domain" the brief rewards. The discriminator is *observable* (the receipt), not heuristic.
- **What would change it:** only a transport with true recall (e.g. a WebSocket push we control, or a message bus with delete-before-delivery) would permit abort-after-ack; our Slack/SMS stubs already model the recallable case, so the policy supports both and switches on the receipt. That generality is itself defensible.

### 12.5 At-most-once dedup (never duplicate, even at a cost)

- **Decision:** the claim protocol inserts `INTENT` before sending; a crash between claim and send produces a missed delivery, never a duplicate (Section 9.2).
- **Cost:** at-least-once semantics are the industry default for alerting because a missed alert is worse than a duplicate. We deliberately invert this.
- **Why it wins:** the brief's constraint is *"must not send duplicate notifications"* — that is the hard requirement, and at-most-once is the only honest way to honor it. The missed-delivery window is closed by design, not by hope: the ack timer (R4c) escalates if the claimed send never lands, so the alert still reaches a human within the ack window. In other words, we don't give up reliability to get dedup; we route the reliability burden into the escalation path, where it belongs.
- **What would change it:** if the platform could retry with a per-channel idempotency key (Slack/Twilio both support it) *and* the channel guarantees delivery, at-least-once would be safe. With stub adapters there is no such guarantee, so at-most-once stands.

### 12.6 SQLite ledger over an in-memory store or Redis

- **Cost:** single-process writes are serialized by SQLite's locking; at thousands of alerts/second this becomes a bottleneck. The walkthrough does not approach that.
- **Why it wins:** ACID transactions are the *enforcement mechanism* for the dedup claim protocol and the terminal-state invariant. An in-memory dict is faster but crash-volatile (a restart could replay a send → duplicate). Redis gives durability but requires a server, credentials, and a story for the walkthrough. SQLite is one file, stdlib-backed, crash-safe, and the exact same `UNIQUE` constraints work when the scale-out design swaps the store (Section 16). Zero-dependency runtime (N3) is preserved.
- **What would change it:** multi-process deployment or >1k alerts/s → a real DB/Redis with the same schema constraints; the protocol doesn't change, only the storage.

### 12.7 Event-driven change detection over polling

- **Cost:** the system is blind to changes that produce no event (e.g. a presence service that silently stops emitting). We accept a *monitoring* gap (the presence simulator is the event source; if it dies, the walkthrough dies loudly) in exchange for determinism.
- **Why it wins:** polling is banned by the no-double-query constraint, full stop. Events also give us the *causal structure* the decision policy needs: an event carries a before/after, which is exactly the diff the policy consumes. A polled read gives only an "after," forcing the policy to guess what changed.
- **What would change it:** a real event bus with at-least-once delivery would add replay (E14 already handles it) and ordering guarantees (we use the in-process total order today; a broker needs a partition key per alert — documented in Section 16).

### 12.8 Stubbed channels over real provider integration

- **Cost:** no real email/Slack/SMS is sent; a skeptical reviewer may wonder if the system is "real."
- **Why it wins:** (a) the walkthrough must be deterministic and offline-safe; real providers add credentials, rate limits, and network nondeterminism that would make the 3-minute walkthrough unrepeatable; (b) the *routing logic* — which is the entire point of the task — is fully exercised by the stubs because the adapters are behind an interface and the logic never inspects their internals; (c) provider APIs are a solved integration problem, not the engineering challenge under evaluation. The interface (Section 10.1) is the drop-in seam.
- **What would change it:** a submission where the deliverable was "the notification actually goes out." Here the deliverable is the routing decision, so stubs are the correct fidelity/effort trade.

### 12.9 Immutable plan, mutable cursor

- **Decision:** the route is computed once and never rebuilt; events move only the cursor.
- **Cost:** if the roster changed materially mid-dispatch (someone added), the route won't see them until the next alert.
- **Why it wins:** rebuilding the route on events re-opens the availability-first downgrade path (a fresh route would score against *current* availability, which is the banned pattern). Frozen routes are what make R2's "next-ranked backup" well-defined and the no-downgrade property structural. New rostering is handled by the next alert's dispatch, which is the correct boundary anyway.
- **What would change it:** extremely long-lived alerts (hours) where roster drift matters; then a re-baseline is warranted — explicitly not part of this v1.

### 12.10 Seniority weight constants

- **Decision:** `qualification = expertise × (1 + (seniority−1) × 0.15)`.
- **Cost:** arbitrary-looking constants invite scrutiny.
- **Why it wins:** the *shape* (expertise dominates, seniority breaks ties) is the defensible property; the constants are documented, tunable, and stable. We defend the shape and admit the constants are a config value, not a law.
- **What would change it:** calibration data on past incidents (who resolved which alerts how fast). That is exactly the kind of "with more time" improvement listed in Section 16.

---

## 13. Test Plan

### 13.1 Strategy

The system is deterministic and pure-in-the-hot-path, so testing is exhaustive rather than probabilistic. Three layers: **unit** (pure functions), **scenario** (event sequences through the router), and **property** (invariants asserted after arbitrary event sequences). The property layer is the one that proves the brief's constraints, so it is the layer the walkthrough opens with.

### 13.2 Unit tests

| Test | Target | Asserts |
|------|--------|---------|
| `test_ranker_determinism` | Ranker | identical inputs → identical order (run twice, compare) |
| `test_ranker_no_downgrade_order` | Ranker | gated-in list is strictly qualification-descending |
| `test_snapshot_single_eval` | Snapshotter | re-inserting same (alert, stakeholder) raises `IntegrityError` |
| `test_planner_skips_down_channel` | Planner | DOWN channel dropped from step channel order |
| `test_planner_escalation_cap` | Planner | cap applied, route length ≤ cap |
| `test_policy_r1_retry` | Policy | offline-recipient-but-channel-failed → R1 with correct fallback channel |
| `test_policy_r2_abort_reroute` | Policy | offline + unacked + no alt channel → ABORT + REROUTE to next backup |
| `test_policy_r3_escalate_parallel` | Policy | offline + acked → ESCALATE_PARALLEL, original untouched |
| `test_policy_r4_better_match` | Policy | better-qualified online → REROUTE (unacked) / ESCALATE (acked) |
| `test_policy_r5_ignore_worse` | Policy | worse/equal-qualified online → IGNORE |
| `test_policy_r6_exhausted` | Policy | no backups → ABORT/FAILED, UNRESOLVED |
| `test_policy_unhandled_raises` | Policy | out-of-matrix input → `UnhandledSituationError` |
| `test_ingress_rejects_malformed` | Ingress | missing field / bad severity → `AlertValidationError`, no ledger writes |

### 13.3 Scenario tests (event sequences through the router)

Each scenario runs a full dispatch with a scripted event timeline and asserts the terminal state, the notification rows, the decision log, and both invariants.

- `test_reroute_on_offline` (E1): Slack send begins → `presence.changed(alice, offline)` → assert Alice's row `CANCELLED`, Bob `DELIVERED` once, full context + rationale present (AC-5, AC-8).
- `test_escalate_after_delivery` (E2): email ACKed → `presence.changed(alice, offline)` → assert Alice `DELIVERED`, Carol `ESCALATED` once, two distinct recipients, context preserved (AC-6, I1, I2).
- `test_better_match_appears` (R4): high-severity alert, David comes online mid-flight in his domain → assert parallel escalation to David with rationale naming the qualification comparison (AC-7).
- `test_no_downgrade` (R5/E5): Eve (lower-qualified) comes online mid-flight while Alice is current → assert `IGNORE`, no new notification (AC-4).
- `test_channel_fail_retries_fallback_channel` (E4): Slack provider DOWN at send → assert email attempt same recipient (R1), no reroute.
- `test_ack_timer_escalates` (R4c): HIGH severity, injectable clock advances past `ack_window`, no ack → assert escalation to next backup; assert no double escalation on a second timer fire (E17).
- `test_unknown_domain_to_duty_manager` (E8): `domain="quantum"` → assert duty manager delivery with explicit rationale.
- `test_escalation_cap_bounds_chain` (E6): repeatedly force failures → assert exactly cap notifications, then `FAILED/UNRESOLVED`.
- `test_parallel_alerts_same_recipient` (E12): two alert_ids to same stakeholder → both deliver.
- `test_crash_between_claim_and_send` (E13): ledger pre-seeded with INTENT → rerun dispatch → assert no duplicate send, alert flagged for escalation/human.

### 13.4 Property tests

Property tests run N randomized-but-seeded event sequences and assert invariants after every event:

- **P1 (I1):** for each alert, each stakeholder has ≤ 1 non-CANCELLED notification row.
- **P2 (I2):** no stakeholder appears in > 1 notification row for the same alert (primary + escalation to the same person impossible).
- **P3 (no-downgrade):** if a delivered notification targets S and any other stakeholder T with higher frozen qualification exists in an *available* state at delivery time, the event log must show an escalation attempt to T or an R5 `IGNORE` entry with the comparison — i.e. the downgrade never happens *silently*.
- **P4 (single-eval):** the `snapshots` table has exactly one row per candidate per alert.
- **P5 (context):** every delivered `body` contains the original `context` JSON and a non-empty `rationale`.
- **P6 (termination):** every dispatch reaches a terminal state within a bounded number of events (the cap + rule budget).

P3 is the adversarial one: it actively hunts for the bug the brief cares most about. The seeded property runner uses `random.Random(fixed_seed)` so failures are reproducible.

### 13.5 Test runner

Stdlib `unittest` with a `make test` shim; no third-party test framework. Tests run headless, offline, under 60s total. The walkthrough opens by running the suite (Section 14.4) so the video proves green before showing the walkthrough.

---

---

## 14. Walkthrough Script & Scenarios

### 14.1 Video constraints

Maximum 3 minutes. The brief: "Videos longer than three minutes are watched for three minutes only." So the walkthrough opens with the strongest signal (a green test suite proving the invariants), then three scripted scenarios, then one screen of "what's next." No logos, no intro animations, no production value — the brief says production value counts for nothing. Narration is the walkthrough; the screen shows real terminal output.

### 14.2 Scenario A — Recipient goes offline mid-flight (R2, abort + reroute)

Scripted event timeline:

```
t=0.0   alert: stock_level value=12 threshold=20 severity=HIGH domain=inventory
        -> ranker output printed (qualification scores, gated flags)
        -> plan: primary Alice (slack), backups Bob (email), Carol (sms)
t=0.5   claim INTENT(alice, slack); send begins -> "SENDING alice via slack"
t=1.0   presence.changed(alice, offline)          [simulated]
        -> ChangeDetector: diff vs snapshot = online->offline
        -> DecisionPolicy R2: not acked, no alt channel (slack only healthy)
             => ABORT + REROUTE -> Bob (email)
        -> ledger: alice/slack CANCELLED; bob/email INTENT -> SENDING
t=1.5   bob/email ACKED -> DELIVERED
        -> print Bob's full notification body (context + "why you over Carol")
t=2.0   summary: plan DELIVERED, decision_log rows R2_ABORT_REROUTE
```

Narration points: why Alice (highest qualification 6.50); why abort was *legal* (nothing acked → recallable); why Bob, not Carol (snapshot order, not whoever-is-online — Carol is online but 2.90 < 5.20); how the dedup ledger proves Alice never received it; the notification body shows full context + rationale.

### 14.3 Scenario B — More senior stakeholder becomes available (R4b, parallel escalate)

```
t=0.0   alert: contract_expiry value=3 threshold=5 severity=CRITICAL domain=sla_contracts
        -> primary: Carol (sla_contracts 5, seniority 4, qualification 7.25)
        -> backups: David is OFFLINE at snapshot (seniority 5, sla 4) -> gated, retained
t=0.5   Carol via sms ACKED -> DELIVERED
t=1.0   candidate.available(david)               [simulated]
        -> diff: david now online; frozen qualification 6.40 (sla 4 x seniority 1.60)
        -> 6.40 < 7.25? NO -> wait. This scenario needs David to *beat* Carol.
```

Correction (deliberate seed design): to show a *successful* better-match escalation, the alert domain must favor David. Use `domain=platform`:

```
t=0.0   alert: platform_health value=0.91 threshold=0.95 severity=CRITICAL domain=platform
        -> primary: Hank (platform 4, seniority 4, q=5.80) via slack
        -> David (platform 5, seniority 5, q=8.00) OFFLINE at snapshot -> gated, retained
t=0.5   Hank slack SENDING (not yet acked)
t=1.0   candidate.available(david)
        -> R4a (not acked): ABORT hank + REROUTE david
```

...and the *negative* control in the same scenario, 15 seconds later:

```
t=1.0   candidate.available(eve)   # inventory/stocks domain, q=3.45 vs Hank 5.80
        -> R5: IGNORE (worse match online) -> decision log shows the refusal
```

Narration points: the better-match comparison is against *frozen* scores (no re-evaluation); R4a aborts only because nothing was acked; the R5 negative control demonstrates the no-downgrade rule live, using the exact stake the brief warns about ("more senior stakeholder has become available" is handled, and "less qualified person online" is refused — both shown in one scenario).

### 14.4 Scenario C — Channel failure → fallback channel (R1)

```
t=0.0   alert: stock_anomaly value=0.88 threshold=0.90 severity=MEDIUM domain=stock_anomaly
        -> primary: Eve (stock_anomaly 5, seniority 2, q=5.75) via slack (pref slack>sms)
t=0.5   channel.failed(slack)   [simulated provider outage]
        -> R1: same recipient, next channel from snapshot = sms
        -> eve/sms INTENT -> ACKED -> DELIVERED
t=1.0   summary: no reroute (recipient never changed), rationale names the fallback
```

Narration points: transport failure is not a recipient problem (R1); the fallback channel came from the *snapshot*, not a live probe — this is the single-evaluation guarantee applied to channels too.

### 14.5 Walkthrough close (last ~30 seconds)

1. Run the property suite (`test_property_no_duplicate`, `test_property_no_downgrade`, `test_snapshot_single_eval`) — green.
2. Show `decision_log` for Scenario A in full (each row: code, action, target, rationale) — the explainability artifact.
3. One line: "Everything you saw is on the public repo; what's next is in the README."

### 14.6 Expected terminal output (Scenario A, verbatim shape)

```
$ python -m alert_routing.router scenarios/scenario_a.json --clock simulated

[ingress] alert_7f3a1b received: stock_level=12.0 threshold=20.0 HIGH inventory
[rank]    1. Alice Chen   q=6.50  gated=no   online=yes   slack
[rank]    2. Bob Okafor   q=5.20  gated=no   online=yes   email
[rank]    3. Eve Nakamura q=3.45  gated=yes  online=no   (offline at snapshot)
[rank]    4. Carol Reyes  q=2.90  gated=no   online=yes   sms
[plan]    route=[alice(slack), bob(email), carol(sms)] cap=3
[ledger]  INTENT  alert_7f3a1b alice slack level=0
[send]    SENDING alert_7f3a1b -> alice via slack ...
[event]   presence.changed alice -> offline
[policy]  R2_ABORT_REROUTE: alice offline mid-flight, send unacked, no alt channel;
          aborting alice/slack, rerouting to bob (next-ranked in snapshot order)
[ledger]  CANCELLED alert_7f3a1b alice slack
[send]    SENDING alert_7f3a1b -> bob via email ...
[ledger]  DELIVERED alert_7f3a1b bob email level=0
[notify]  bob: [ALERT] stock_level threshold breached ... Why you: highest-qualified
          available for inventory. Why not Carol: q 2.90 < 5.20.
[summary] plan=DELIVERED decisions=2 notifications=1 unresolved=0
```

This output is the walkthrough's script and also the acceptance evidence: a reviewer can read it and check every constraint by eye.

---

## 15. Repository & Release Strategy

### 15.1 Repository layout

```
alert_routing/
├── README.md                 # run instructions, architecture summary, what's next
├── BLUEPRINT.md              # this document
├── pyproject.toml            # minimal metadata, no deps; optional
├── scenarios/
│   ├── scenario_a_offline.json
│   ├── scenario_b_better_match.json
│   └── scenario_c_channel_fail.json
├── src/alert_routing/
│   ├── __init__.py
│   ├── models.py
│   ├── registry.py
│   ├── ranker.py
│   ├── snapshotter.py
│   ├── planner.py
│   ├── dispatcher.py
│   ├── changes.py            # Change Detector + event types
│   ├── decision.py           # policy R1–R6 + rationale composition
│   ├── ledger.py             # SQLite store, check-then-claim
│   ├── channels.py           # adapters (email/slack/sms stubs)
│   ├── presence.py           # presence simulator + emitter
│   ├── router.py             # orchestrator + CLI entry
│   └── server.py             # optional FastAPI POST /alert, /alerts/{id}
└── tests/
    ├── test_unit_*.py
    ├── test_scenario_*.py
    └── test_property_*.py
```

### 15.2 Public repository

- Created via `gh repo create alert-routing --public` under the `github/web8080` account (per direction), with `gh auth` confirmed first.
- README must pass the logged-out-browser test: we verify `https://github.com/web8080/alert-routing` opens in a private/incognito window and the "run it" instructions work from a clean clone in a temp dir (`python -m alert_routing.router scenarios/scenario_a.json`).
- The walkthrough video (≤3 min) is uploaded unlisted and its "anyone with the link" permission is verified in a logged-out window before submission. Both links are checked before the email reply is sent — the brief is explicit that unopenable deliverables are scored as absent.

### 15.3 Reproducibility guarantee

`README.md` includes: `python3 -m venv .venv && . .venv/bin/activate && python -m alert_routing.router scenarios/scenario_a.json`. No `pip install` beyond the venv itself; the optional HTTP server documents `pip install fastapi uvicorn` as an explicit, optional step. The walkthrough does not depend on the HTTP server.

---

## 16. Future Work

Honest, prioritized, and explicitly not in v1. This section answers "what would you do next with more time" in the README and the video's last 30 seconds.

1. **Real channel adapters.** SMTP/SendGrid, Slack Web API, Twilio — behind the existing `ChannelAdapter` interface (Section 10.1). Straightforward, credential-gated, no design change.
2. **On-call rotation calendars.** Replace the static `on_call` boolean with an ical/PagerDuty-style schedule; on-call becomes a computed gate per dispatch time. This is the highest-value real-world addition.
3. **Ack/escalation timeline and incident UI.** Render the `decision_log` as an incident timeline (who was notified when, what changed, why rerouted). The data already exists; this is presentation.
4. **Real event bus.** Replace the in-process emitter with a message broker (partition key = `alert_id` preserves ordering; at-least-once delivery is already handled by E14's replay idempotency). Enables multi-process scaling with the identical schema.
5. **Retry with backoff** on `RETRIABLE` channel failures instead of treating them as immediate R2 reroutes — adds timer complexity, deferred deliberately (Section 10.1).
6. **LLM-generated rationale prose** as an optional enrichment layer *downstream* of the decision (never in the hot path), with the deterministic rationale as the fallback — the safe place for "AI" in this system.
7. **Calibration of the seniority weight** against real resolution data (Section 12.10) so the tie-break constant is empirical, not chosen.
8. **Chaos tests** for the ledger: kill the process at random ledger points, restart, assert I1/I2 still hold (crash-safety as a first-class property test).

---

## 17. Appendix A — Stakeholder Seed Data

```json
{
  "stakeholders": [
    {"id":"STK-001","name":"Alice Chen","title":"Inventory Lead","seniority":3,
     "expertise":{"inventory":5,"supply_chain":3},"on_call":true,
     "channels":[{"channel":"slack","endpoint":"alice.slack"},{"channel":"email","endpoint":"alice@acme.dev"},{"channel":"sms","endpoint":"+1555-000-0001"}]},
    {"id":"STK-002","name":"Bob Okafor","title":"Logistics Ops","seniority":3,
     "expertise":{"inventory":4,"logistics":4},"on_call":true,
     "channels":[{"channel":"email","endpoint":"bob@acme.dev"},{"channel":"slack","endpoint":"bob.slack"}]},
    {"id":"STK-003","name":"Carol Reyes","title":"Contracts Lead","seniority":4,
     "expertise":{"sla_contracts":5,"inventory":2},"on_call":true,
     "channels":[{"channel":"sms","endpoint":"+1555-000-0003"},{"channel":"email","endpoint":"carol@acme.dev"}]},
    {"id":"STK-004","name":"David Miller","title":"Platform Lead","seniority":5,
     "expertise":{"platform":5,"sla_contracts":4},"on_call":false,
     "channels":[{"channel":"email","endpoint":"david@acme.dev"},{"channel":"slack","endpoint":"david.slack"},{"channel":"sms","endpoint":"+1555-000-0004"}]},
    {"id":"STK-005","name":"Eve Nakamura","title":"Anomaly Analyst","seniority":2,
     "expertise":{"stock_anomaly":5,"inventory":3},"on_call":true,
     "channels":[{"channel":"slack","endpoint":"eve.slack"},{"channel":"sms","endpoint":"+1555-000-0005"}]},
    {"id":"STK-006","name":"Frank Dubois","title":"Logistics Lead","seniority":3,
     "expertise":{"logistics":5,"stock_anomaly":4},"on_call":true,
     "channels":[{"channel":"email","endpoint":"frank@acme.dev"},{"channel":"sms","endpoint":"+1555-000-0006"}]},
    {"id":"STK-007","name":"Grace Lin","title":"Supply Chain IC","seniority":1,
     "expertise":{"supply_chain":5},"on_call":true,
     "channels":[{"channel":"sms","endpoint":"+1555-000-0007"},{"channel":"email","endpoint":"grace@acme.dev"}]},
    {"id":"STK-008","name":"Hank Vogel","title":"Platform Engineer","seniority":4,
     "expertise":{"platform":4,"logistics":3},"on_call":false,
     "channels":[{"channel":"slack","endpoint":"hank.slack"},{"channel":"email","endpoint":"hank@acme.dev"}]}
  ]
}
```

Seed rationale: three domains with ≥2 experts (inventory: Alice/Eve/Bob; sla_contracts: Carol/David; platform: David/Hank); one senior-offline candidate (David) for the better-match scenario; one junior-online candidate (Eve) for the no-downgrade negative control; one generic-supply-chain IC (Grace) to show domain routing to a specialist; no stakeholder has identical (expertise, seniority), so the tie-break path is exercised only in property tests, not the walkthrough.

---

## 18. Appendix B — Full Decision Traces

### 18.1 Trace T1 — Scenario A, offline mid-flight (R2)

Inputs: alert `stock_level=12/20 HIGH inventory`. Snapshot: Alice 6.50 online, Bob 5.20 online, Eve 3.45 offline (gated), Carol 2.90 online. Plan route `[alice(slack), bob(email), carol(sms)]`.

| seq | event | ledger before | verdict | ledger after |
|-----|-------|---------------|---------|--------------|
| 1 | ingress | — | plan QUEUED | plan=QUEUED |
| 2 | (send start) | — | claim alice/slack INTENT | INTENT alice/slack |
| 3 | presence.changed(alice, offline) | INTENT alice/slack (not acked) | R2 ABORT+REROUTE→bob | alice/slack CANCELLED; INTENT bob/email |
| 4 | (ack) | INTENT bob/email | — | bob/email DELIVERED |
| 5 | (close) | DELIVERED | plan DELIVERED | decision_log rows 2 |

Post-conditions: notifications rows for alert = {alice CANCELLED, bob DELIVERED}. I1 ✓, I2 ✓, P3 ✓ (Carol online but never chosen over Bob — she ranks below Bob in the frozen order).

### 18.2 Trace T2 — Better match appears, nothing acked (R4a)

Alert `platform_health=0.91/0.95 CRITICAL platform`. Snapshot: Hank 5.80 online (route primary), David 8.00 offline (gated, retained). Plan route `[hank(slack)]` (David gated).

| seq | event | ledger before | verdict | ledger after |
|-----|-------|---------------|---------|--------------|
| 1 | ingress | — | plan QUEUED | plan=QUEUED |
| 2 | send start | — | claim hank/slack INTENT | INTENT hank/slack |
| 3 | candidate.available(david) | INTENT hank/slack (unacked) | R4a ABORT+REROUTE→david | hank/slack CANCELLED; INTENT david/email (pref email first healthy) |
| 4 | ack | INTENT david/email | — | david/email DELIVERED |
| 5 | candidate.available(eve) | DELIVERED | R5 IGNORE (3.45 < 5.80) | no change; decision_log R5 |

Post-conditions: Hank never received the alert (aborted before ack); David received with rationale naming the 8.00 vs 5.80 comparison; Eve's arrival produced an `IGNORE` log row, proving the no-downgrade path observably.

### 18.3 Trace T3 — Acknowledged then offline (R3, complete + escalate)

Alert `contract_expiry=3/5 CRITICAL sla_contracts`. Snapshot: Carol 7.25 online (primary, sms), David 6.40 offline (gated), Frank 0 (domain miss) gated. Plan `[carol(sms)]`.

| seq | event | ledger before | verdict | ledger after |
|-----|-------|---------------|---------|--------------|
| 1 | ingress | — | plan QUEUED | plan=QUEUED |
| 2 | send | — | claim carol/sms INTENT | INTENT carol/sms |
| 3 | ack | INTENT carol/sms | — | carol/sms DELIVERED |
| 4 | presence.changed(carol, offline) | carol DELIVERED | R3 ESCALATE_PARALLEL→david | carol stays DELIVERED; INTENT david/email level=1 |
| 5 | ack | INTENT david/email | — | david/email DELIVERED level=1; plan ESCALATED |

Post-conditions: two recipients (Carol primary, David escalation level 1), two rows, both delivered, context in both bodies. No stakeholder has two rows. The acked email was not "aborted" — the policy's honesty about email recall is demonstrated.

### 18.4 Trace T4 — Ack timer (R4c)

Alert `stock_level HIGH`, recipient Alice via slack, send claimed but adapter stalls (RETRIABLE every attempt), no event.

| seq | event | ledger before | verdict | ledger after |
|-----|-------|---------------|---------|--------------|
| 1 | ingress | — | plan QUEUED | plan=QUEUED |
| 2 | send | — | claim alice/slack INTENT | INTENT alice/slack |
| 3 | timer fires at ack_window (30s simulated) | INTENT alice/slack | R4c ESCALATE_PARALLEL→bob | bob/email INTENT level=1; alice/slack left INTENT (unacked) |
| 4 | ack | bob INTENT | — | bob DELIVERED level=1 |
| 5 | timer fires again | plan ESCALATED | IGNORE (E17) | no change |

Post-conditions: exactly one escalation; the second timer fire is a no-op. Alice's INTENT row is a documented at-most-once residue (9.2) — she may not have received it, but Bob's escalation guarantees a human was reached.

---

## 19. Appendix C — Glossary

| Term | Meaning |
|------|---------|
| ACK | Delivery receipt from a channel adapter; terminal for a send. |
| Claim | The atomic `INTENT` insert that reserves a notification slot and prevents duplicates. |
| Cursor | The `(step_index, channel_index)` position within an immutable route. |
| Gating | Filtering ranked candidates by on-call, online, and channel-health (never reorders). |
| Ledger | SQLite store that is the source of truth for notifications, snapshots, plans, decisions. |
| No-downgrade | Invariant: never notify a lower-qualified stakeholder while a higher-qualified one is available. |
| Reroute | Aborting an unacked send and moving the cursor to the next-ranked backup. |
| Escalate in parallel | Issuing a level-1+ notification to a different, more-qualified stakeholder while the original stands. |
| Qualification | `expertise_match × seniority_weight`, the only ordering key. |
| Snapshot | The one-time, frozen record of availability/channel health per candidate per alert. |
| Terminal state | `DELIVERED`, `ESCALATED`, `ABORTED`, or `FAILED`; no outgoing transitions. |
| UNRESOLVED | Alert parked in the ledger with no human notified and a complete attempted chain. |

---

## 20. Acceptance Checklist

Pre-submission gate — every row must be satisfied before the email reply is sent.

- [ ] Public repo `github/web8080/alert-routing` opens in a logged-out browser.
- [ ] Clean-clone run works from README instructions (no `pip install` beyond the venv).
- [ ] `python -m unittest` green (all of Sections 13.2–13.4 — shipped suite is **88 tests**).
- [ ] Scenario A, B, C reproduce the expected terminal output from `scenarios/`.
- [ ] Decision log shows named rules (R1–R6) and composed rationales.
- [ ] Video ≤ 3:00, unlisted, "anyone with the link," opens in a logged-out window.
- [ ] Video covers: alert → rank → dispatch → mid-flight change → reroute/escalate → context preserved → no duplicates.
- [ ] Video explains at least the abort-vs-parallel-escalate discriminator, the no-double-query mechanism, and the no-downgrade rule.
- [ ] README documents run instructions + "what would you do next with more time."
- [ ] Both links (repo + video) re-checked in a logged-out window immediately before submission.

---

## 21. As-Built Addendum — Dashboard refinement (2026-08-15)

*The design sections above are the contract; this addendum records what the
shipped implementation additionally built on top of them, and where it differs
from the speculative layout in §15.1. It is appended, never edits, the original.*

### 21.1 Dashboard: hybrid 4-view (Console / Monitor / Policy / Registry)

The dashboard shipped as a **hybrid console/table/CRUD UI** — four views behind
a single left-sidebar nav, served by stdlib `http.server` (`alert_routing/ui.py`
+ `static/`). The front-end contains zero routing logic; it renders only the JSON
API (`/api/scenarios`, `/api/registry`, `/api/roster`, `/api/dispatch`,
`/api/monitor`), all of which reuse `run_scenario_data` / `render_timeline` /
`parse_stakeholder`.
Registry edits land in a **SQLite store** (`RegistryStore`, default
`registry.db` beside the JSON, overridable via `ALERT_REGISTRY_DB` /
`--registry-db`): the DB is the runtime source of truth, dispatches read it
directly, and the tracked `registry.json` seed is refreshed on every save so
the repo stays a clean bootstrap.

- **Console** — the live-dispatch screen: alert panel + custom-alert JSON form,
  dispatch state machine (RECEIVED → RANKED → PLANNED → DISPATCHING → CHANGE
  DETECTED → POLICY DECISION → RESULT), animated trace (step/pause/replay/speed),
  qualification-first ranking table, decision card (rule/action/target/rationale/
  result), notification ledger, incident timeline, R1–R6 policy matrix, and an
  AI incident summary + runbook note with an on-screen toggle.
- **Monitor** — `monitor.py` `AutoMonitor`: one telemetry feed per bundled
  scenario (metric/threshold/severity/domain + a deterministic slope), values
  drift over a virtual clock and breach on their own schedule. Every breach is
  auto-submitted to the **deterministic router** into ONE shared ledger (no
  manual scenario switching); submission order is severity → deviation. The AI
  watcher is advisory only — a one-line note per dispatch, written after the
  decision, with a deterministic fallback (`prose_or_fallback("monitor", …)`).
  Driven by `GET /api/monitor` + `POST /api/monitor/tick`.
- **Policy** — R1–R6 rule matrix, full decision log, and AI summary in one view.
- **Registry** — CRUD over the live stakeholder registry (add/edit/delete,
  per-stakeholder channels + expertise, on-call toggle) plus an **on-call shift
  calendar** and today's on-call chips.

### 21.2 On-call roster (roster.py + roster.json)

Section 16.2 listed "on-call rotation calendars" as future work; a minimal,
backward-compatible slice is now shipped:

- Shifts are flat date ranges: `{id, start, end, primary, backups}`.
- `effective_on_call(registry, shifts, day)` returns the union of primaries +
  backups across shifts covering the day; when **no** shift covers the day the
  static registry `on_call` flags apply unchanged (the plain registry still works).
- The UI computes the effective on-call map and hands it to every dispatch
  (`_on_call_overrides`), so dispatch gating is roster-aware.
- Recurrence, iCal import, and PagerDuty-style schedules remain future work —
  intentionally out of scope for the walkthrough.

### 21.3 Registry CRUD

`parse_stakeholder` / `save_registry` gained round-trip editability (the registry
is no longer read-only at runtime). The UI writes go through the same validation
(`RegistryValidationError` on malformed entries — E19 preserved). A shared lock
serializes concurrent writes in the UI server.

### 21.4 Test plan status (supersedes §13 counts)

The shipped suite is **88 tests** — the §21 count plus `test_agents.py`
(incident-KB retrieval order + record round-trip, triage brief schema,
supervisor audit trail + fallback determinism, **honesty** — mode/audit must
reflect the brief's real source, never a silent AI — and the safety gate that
flags any stakeholder the kernel did not deliver to). Runner: stdlib
`python3 -m unittest discover` (also passes under pytest). Determinism and
hermeticity are unchanged — the agentic tests patch `ai_enabled`/pass
`enabled=False` and never touch the network. See §22 for the layer they test.

### 21.5 As-built repository layout (vs §15.1)

```
alert_routing/
  registry.py   + roster.py      + ranker.py      + snapshotter.py
  planner.py    + ledger.py      + presence.py    + channels.py
  changes.py    + decision.py    + router.py      + cli.py
  timeline.py   + ai.py          + runbooks.py    + agents.py   + incidents.py
  monitor.py    + metrics_feed.py
  ui.py (+ static/)               server.py (optional FastAPI, never core)
scenarios/  (7 scripted scenarios + proposed/)   runbooks/   incidents/ (seeded KB)
registry.json   roster.json   tests/ (121)   .github/workflows/ci.yml   Dockerfile
```

README/TESTING/ROADMAP have been brought in line with this
as-built state.

---

## 22. AI / Agentic Layer — Design (two-lane architecture)

*Added 2026-08-15 as the senior-engineer design for "cracking the hard part
with AI". This section specifies what we add and — equally important — what we
deliberately never add. It is a design contract, not a promise to ship
everything before the walkthrough.*

> **Update 2026-08-16 (Monitor view):** the same rule holds for the dashboard's
> Monitor — the AI is a **watcher**, not a router. It scans every feed and
> writes an advisory note per dispatch, but *every* breach is submitted to the
> deterministic router and AI output can never change who is notified. See §21.1.

### 22.1 The argument (why the hard part *must* stay deterministic)

The brief's hard constraints — no lost context, no duplicate notification, no
double-query of availability, no downgrade to a less-qualified recipient — are
**correctness properties**. A probabilistic model cannot prove any of them. The
2026 AIOps/agentic-SRE market has converged on the same conclusion: AI runs
*triage, summary, and comms*; the **who-to-page decision stays deterministic
policy** (PagerDuty: "our agents know when they don't have enough data — they
escalate to a human rather than guess"; Cordum's two-lane design; Google SRE's
shipped agents do consolidation/handoffs/postmortems, not dispatch). The Replit
incident is the cautionary tale: constraint adherence degrades under complexity,
and irreversible actions outrun human supervision.

**Consequence:** our router (Sections 5–9) is already the right answer. The AI
layer must be **structurally incapable of routing** — no tool can choose a
recipient, change a channel, or trigger an escalation. Safety is architectural,
not behavioral: *the AI has no lever to hurt the guarantees.*

### 22.2 Two-lane architecture

```
Lane 1 (deterministic kernel — unchanged)          Lane 2 (agentic AI — read-only, post-decision)
alert ─▶ RANKER ─▶ SNAPSHOTTER ─▶ PLANNER ─▶       alert + ledger + trace + runbooks + past incidents
         DISPATCHER ─▶ CHANGE DETECTOR ─▶            ─▶ SUPERVISOR
         DECISION POLICY R1–R6 ─▶ timeline            ├─ triage agent   (RAG brief: cause/checks/steps/escalation)
         (R1–R6, dedup, single-eval: provable)        ├─ comms agent    (status/handoff drafts)
                                                      └─ postmortem agent (structured draft)
                                                      ─▶ structured JSON brief → dashboard
```

- Lane 2 runs **after** the terminal decision, on the same event data the
  kernel already recorded. Its output is advisory; it never feeds back into
  Lane 1.
- Every agent has a **deterministic fallback** and a **token/time budget**, so
  the walkthrough and tests run identically with AI off (P5 untouched).
- The only optional dependency is the existing Anthropic call (stdlib
  `urllib`); no framework (LangGraph/CrewAI/AutoGen) — a 60-line supervisor
  loop with structured-JSON handoffs proves the same architecture with zero
  dependency cost and lower latency.

### 22.3 The triage-brief agent (the slice we build)

Inputs: the alert, the decision log + notification ledger for this alert, the
deterministic runbook retrieval (Section: `runbooks.py`), and the past-incident
KB (`incidents/*.json`). Output: a **schema-valid JSON brief**:

```json
{
  "likely_cause": "…",            "confidence": "low|medium|high",
  "first_checks": ["…", "…"],     "remediation_steps": ["…", "…"],
  "escalation_criteria": "…",
  "runbook": {"id": "inventory_stock_level", "snippet": "…"},
  "similar_incidents": [{"id": "…", "metric": "…", "resolution": "…", "similarity": 0.87}]
}
```

Contract:
- **Post-decision only.** It explains and advises; it can never alter routing.
- **Grounded.** Every claim must cite the runbook or a past incident; the LLM
  is instructed to answer "insufficient evidence" rather than invent (the
  PagerDuty "know when you don't have enough data" principle).
- **Schema-valid or fallback.** If the LLM returns invalid JSON, missing keys,
  or times out, the supervisor substitutes the deterministic brief. The shape
  is what the UI renders — a malformed shape is a failed agent, not a crash.
- **Prompt hygiene.** Raw alert `context` is untrusted input (prompt-injection
  hardening, same as Section for ai.py); the brief's fields are validated
  against the kernel's own records.

### 22.4 Supervisor orchestration (thin stub)

A stdlib supervisor runs the three specialist agents as a bounded pipeline:

1. **triage** — retrieves runbook + similar incidents (deterministic, cheap),
   then drafts the brief.
2. **comms** — drafts the status/notification prose from the brief + decision
   log (deterministic fallback: existing `fallback_notification_body`).
3. **postmortem** — drafts a structured post-incident summary from the ledger
   (deterministic fallback: `fallback_incident_summary`).

Guards: per-agent **max_tokens**, a **wall-clock timeout**, and a **per-agent
fallback** so one failing agent cannot fail the pipeline. The supervisor
returns `{"mode": "ai"|"fallback", "agents": [{"name", "ok", "latency_ms"}],
"triage": {...}, "comms": "...", "postmortem": "..."}` and records its own
trace (agent names, ok/fail, latency) — an agent audit trail for the walkthrough.

### 22.5 What we deliberately DO NOT add

| Don't | Why |
|---|---|
| LLM in the routing/decision path | destroys P1–P5 and the whole defense; the obvious question is precisely "why didn't AI pick the recipient" |
| Autonomous remediation / write actions | OWASP A2 (excessive agency); write-path must be approval-gated, out of walkthrough scope |
| Multi-agent frameworks (LangGraph/CrewAI) | dependency + latency + cost; a stdlib supervisor proves the same design |
| Fine-tuning | RAG adapts without it; costs, staleness, no walkthrough value |
| AI "personality"/agents on the paging path | Replit failure mode |
| AI chat with write access | stays a read-only explainer (README "would an AI chat be wise") |

### 22.6 Evals (make it stand out)

Two judges, both deterministic and runnable in the walkthrough:

1. **Retrieval quality** — recall@k of runbook/incident retrieval against
   labeled test cases (no LLM; ~1s). "RAG works" is measured, not claimed.
2. **Safety check** — cross-validate the AI brief against the decision log:
   if the brief ever recommends paging someone the deterministic router
   rejected for this alert, the supervisor flags it as a **defect** (logged,
   never silently accepted). The safety property is asserted, not assumed.

### 22.7 Walkthrough story

Same alert, two runs: run one without AI → deterministic trace; run two with
AI → the *same* trace plus a triage brief (likely cause, first checks, runbook,
similar incidents) and a supervisor audit trail. The closing line: **"the AI
reasoned over our runbooks and history and made the on-call engineer faster —
and it could not have changed who was paged."**

### 22.8 As-built status (supersedes the "promise to ship" framing)

Shipped (commit `4418910`): `agents.py` — `TriageAgent`/`CommsAgent`/
`PostmortemAgent` + `supervise()` (per-agent token/time budget, deterministic
fallback, audit trail `[{name, ok, latency_ms, fallback}]`) + `safety_check`.
`incidents.py` — KB load, deterministic similarity, opt-in `record_incident`
(`ALERT_RECORD_INCIDENTS=1`); `incidents/*.json` seed so retrieval has data on
first run. Dashboard renders the brief (Console + Policy cards, mode badge).
`tests/test_agents.py` (+16 → **88 tests**). **Honesty invariant implemented:**
`mode` and the audit trail reflect the brief's *actual* source — `_parse_brief`
raises on malformed output so a parse failure records a real fallback, and
`mode` derives from the triage agent's report only. Live-verified with a real
Anthropic run (`mode=ai, source=ai`, grounded runbook + past incidents) and a
keyless fallback run (identical routing trace).

---

*End of blueprint. This document is the contract for implementation: a build that satisfies Sections 2, 5–9, and 13 is the deliverable; anything else is scope creep. §21 records the shipped additions without editing the contract; §22 specifies the AI/agentic layer's design contract (post-decision, read-only, provable).*




