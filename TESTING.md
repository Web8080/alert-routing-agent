# TESTING.md — how to verify the Alert Routing Agent works as intended

**Author:** Victor Ibhafidon — **date:** 2026-08-14

This is the step-by-step verification guide. Everything is reproducible,
deterministic, and dependency-free (Python 3.10+). Two things are running
right now while you read this:

- Web UI on `http://127.0.0.1:8000`  (`make ui`)
- FastAPI API on `http://127.0.0.1:8100`  (docs at `/docs`) — optional

---

## 0. Prerequisites

```bash
cd /Users/innovations/Alert_routing
python3 --version          # any 3.10+; tested on 3.14
```

The core needs **no install**. Only the optional FastAPI server needs two
packages (already installed in the throwaway venv `/tmp/ar_srv`):
`pip install fastapi uvicorn`.

---

## 1. The full test suite (88 tests)

```bash
python3 -m unittest discover
```

**Expect:** `Ran 88 tests ... OK`. The suite covers:

| File | What it proves |
|---|---|
| `test_ranker.py` | qualification ordering, determinism |
| `test_snapshot.py` | **P3** — availability is evaluated exactly once per stakeholder (a second eval is a physical `IntegrityError`) |
| `test_dedup.py` | **P2** — no duplicate to the same stakeholder; no primary+escalation to the same person |
| `test_decision.py` | **P4/P5** — R1–R6 + `MIN_REROUTE_DELTA` gate; ack-timeout; duty-manager; cap |
| `test_scenarios.py` | end-to-end runs of all 3 scenarios; invariant assertions after every event |
| `test_timeline.py` | incident timeline + message-as-sent rendering |
| `test_roster.py` | on-call shifts: validation, covering days, effective on-call (roster vs static flags) |
| `test_registry_edit.py` | registry CRUD: parse/save round-trip, upsert, on-call toggle, delete |
| `test_runbooks.py` | deterministic runbook scorer + snippet feeding the incident summary |
| `test_live_delivery.py` | live SMTP/Slack adapters (env-gated) — honest RETRIABLE fallback, never a faked ACK |
| `test_agents.py` | **§22** — incident-KB retrieval order + record round-trip; triage brief schema; supervisor audit trail + fallback determinism; **honesty**: mode/audit must reflect the brief's real source, never a silent AI; safety gate flags any stakeholder the kernel did not deliver to |

---

## 2. The three demo scenarios (CLI)

```bash
python3 -m alert_routing.cli scenarios/scenario_1_offline.json
```

**Expected highlights:**

| Scenario | Trigger | Decision you must see | Terminal plan state |
|---|---|---|---|
| `scenario_1_offline.json` | Sarah (primary) goes **offline mid-flight**, send un-acked | `R2_ABORT_REROUTE ... rerouting to ... STK-002 (David)` | `DELIVERED` |
| `scenario_2_channel_fail.json` | **Slack fails** mid-flight | `R1_RETRY ... retrying same recipient via email` (Sarah stays recipient) | `DELIVERED` |
| `scenario_3_no_downgrade.json` | **Elena** (seniority 5) comes online — but q=3.20 vs Sarah q=6.50 | `R5_NO_DOWNGRADE ... not downgrading` | `DELIVERED` |

Check the RANK lines too: Maya (q=8.00) and Priya (q=7.25) rank ABOVE Sarah but
are GATED (offline at snapshot) — the "qualified-but-unavailable" talking point.
Each run ends with an `INCIDENT TIMELINE` showing who was notified, why, and the
exact message as sent.

`make run1`, `make run2`, `make run3`, `make run-all` are shortcuts.

---

## 3. Prove each guarantee (the five claims)

### P1 — The alert is never lost

The alert + full context + decision log are persisted in the ledger, and the
timeline can be re-rendered from disk even after the process "crashes":

```bash
python3 -m alert_routing.cli scenarios/scenario_1_offline.json
# (no --ledger needed) the CLI writes to a DURABLE temp file by default:
#   [ledger] durable temp ledger: /var/folders/.../alert_ledger_abc123.db
```

**What to look for:** the printed ledger path survives the process, and re-opening
it re-renders the *original* incident — same decisions, same final recipient —
it does not start from scratch:

```bash
python3 -m alert_routing.cli scenarios/scenario_1_offline.json \
  --ledger /var/folders/.../alert_ledger_abc123.db \
  --alert-id alert-scenario_1_offline --timeline
```

The CLI/UI default is a **durable temp file** (not `:memory:`), so a process
crash can never lose the alert — the file survives and the timeline is
re-renderable. Pass `--ledger /path/ledger.db` for cross-run persistence, or
`--ledger :memory:` only when you explicitly want a throwaway run.

### P2 — No duplicate notification to the same stakeholder

Run a scenario and check the ledger: each (stakeholder, channel, level) appears
**at most once**, and a CANCELLED attempt is never re-sent:

```bash
python3 -m alert_routing.cli scenarios/scenario_1_offline.json
# in the timeline: Sarah = CANCELLED once, David = DELIVERED once.
#   NOTIFICATIONS (dedup ledger)
#     [--] Sarah Chen     via slack level=0 status=CANCELLED
#     [OK] David Miller   via email level=0 status=DELIVERED
```

Also: re-ingesting the *same* alert is idempotent — dispatch early-returns when
the `alert_id` already exists, so nothing is duplicated:

```bash
curl -s -X POST http://127.0.0.1:8100/alert -H 'content-type: application/json' \
  -d '{"metric":"stock_level","value":12,"threshold":20,"severity":"HIGH","domain":"inventory"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['alert_id'])"
# run the SAME curl twice → same alert_id, and /alerts/{id} still shows 1 notification
```

### P3 — Availability is never queried twice for the same person

Read the proof in `test_snapshot.py`. The schema makes a second read a *physical
error*: `snapshots` has `PRIMARY KEY (alert_id, stakeholder_id)`, so a
re-evaluation raises `IntegrityError`. After the snapshot, the change detector
only **diffs events** against the frozen snapshot — there is no poller. The
event *is* the query. To see it enforced:

```bash
python3 -m unittest tests.test_snapshot -v
```

### P4 — Never select a less-qualified person merely because they're online

Qualification is computed **without** availability (`expertise × (1+(seniority−1)×0.15)`);
availability is only a gate, never a rank key. Scenario 3 is the live proof:
Elena (seniority 5) is the *most senior*, but her inventory score (3.20) is far
below Sarah's (6.50) — so R5 refuses her with a logged rationale, and Sarah
stays primary. The UI's **STAKEHOLDER RANKING** panel shows this directly
(qual column, GATED tag, no reordering by availability).

### P5 — Same state ⇒ same routing decision (determinism)

The policy engine is a pure function and the only clock is an injectable
`SimClock`. Prove it across Python's per-process hash randomization:

```bash
for seed in 1 42 999; do
  PYTHONHASHSEED=$seed python3 -m alert_routing.cli scenarios/scenario_1_offline.json > /tmp/det_$seed.out
done
diff /tmp/det_1.out /tmp/det_42.out && diff /tmp/det_1.out /tmp/det_999.out \
  && echo "DETERMINISTIC across hash seeds"
```

Same output for every seed. The FastAPI alert-id is also hash-seed-independent
(it's a SHA-1 of the payload, not `hash()`).

**Clock-injection enforcement:** the one path that reads the *current* time —
R4c ack-timeout (`evaluate_ack_timeout`) — refuses to run under a wall-clock
`SystemClock`. If a clock was not injected, it raises a `RuntimeError` instead
of silently making the decision time-dependent:

```bash
python3 - <<'PY'
from alert_routing.router import Router, SystemClock, SimClock
from alert_routing.models import Config
Router('registry.json', ':memory:', Config(), SystemClock()).evaluate_ack_timeout()
# RuntimeError: evaluate_ack_timeout requires an injected scripted clock (SimClock) ...
Router('registry.json', ':memory:', Config(), SimClock()).evaluate_ack_timeout()  # OK
PY
```

All shipped entry points (CLI, UI, API, tests) inject `SimClock`, so the R4c
path is always deterministic in practice — and now it cannot silently degrade.

---

## 4. The web UI

```bash
make ui            # = python3 -m alert_routing.ui --port 8000
open http://127.0.0.1:8000/
```

**What to test, in order:**

**Console view:**
1. Page loads: dark dashboard, left sidebar with three views —
   **Console / Policy / Registry**.
2. Pick `scenario_1_offline`, click **▶ DISPATCH**. Watch:
   - **DISPATCH STATE** light up `RECEIVED → RANKED → PLANNED → DISPATCHING → CHANGE DETECTED → POLICY DECISION → RESULT`.
   - **STAKEHOLDER RANKING** highlight Sarah (live) then David; Maya/Priya stay GATED.
   - **POLICY MATRIX** light the **R2** chip.
   - **DECISION** card: `R2_ABORT_REROUTE` + full rationale.
   - **NOTIFICATION LEDGER**: Sarah `CANCELLED`, David `DELIVERED`.
   - **INCIDENT TIMELINE**: message-as-sent for David with the "why you".
3. Pause/step/replay + speed slider — all client-side, still deterministic.
4. Scenario 2 → **R1** chip + same recipient via email. Scenario 3 → **R5** chip.
5. **Custom alert** tab: paste the JSON, `send custom` — an unknown domain routes
   to the duty manager; a known domain (inventory) ranks Sarah/Maya normally.
 6. **AI summary toggle** on/off — the incident summary (Anthropic, deterministic
    fallback when unconfigured) and runbook note render under the console.
 6b. **AI triage brief** (§22) — after any dispatch the Console/Policy cards show
    the supervised brief: cause + confidence, first checks, remediation steps,
    escalation criteria, the retrieved runbook, past incidents with similarity,
    and a mode badge (`· live` vs `· fallback`) plus agent audit line. With a
    live key the badge says **live**; without one it says **fallback** — the
    routing trace is identical in both (two-lane). Toggle the AI switch to prove
    P5 holds with AI on or off.

**Policy view:** rule matrix R1–R6 + the full decision log + AI summary from the
last dispatch.

**Registry view (CRUD + on-call):**
7. **On-call today** chips reflect `roster.json` shifts (primary + backups).
8. **+ add stakeholder** → save → appears in the list; reload the page and it's
   still there (persisted to `registry.json`).
9. Edit a stakeholder's channels/expertise or toggle **on-call** → save.
10. **on-call shifts** → add a shift covering today → the chips update; dispatch
    again and the roster-aware on-call flags are used (a stakeholder who is
    shift-primary is on call even if their static flag is off).

---

## 5. The optional FastAPI server

```bash
pip install fastapi uvicorn            # once
python -m uvicorn alert_routing.server:app --port 8100
```

```bash
# Ingest → returns a deterministic alert_id + delivered recipient
curl -s -X POST http://127.0.0.1:8100/alert -H 'content-type: application/json' \
  -d '{"metric":"stock_level","value":12,"threshold":20,"severity":"HIGH","domain":"inventory","context":{"warehouse":"WH-4"}}'

# Round-trip: query the state with the returned id
curl -s http://127.0.0.1:8100/alerts/<alert_id>

# Incident timeline for that alert
curl -s http://127.0.0.1:8100/alerts/<alert_id>/timeline

# Interactive API docs
open http://127.0.0.1:8100/docs
```

**Key properties to verify:** the same payload always yields the same
`alert_id`; ingest→query works (one shared durable ledger); the core still
works with the server module *not installed* (`build_app()` returns `None`,
module import never fails).

---

## 6. Expected "definition of done" checklist

- [ ] `python3 -m unittest discover` → `OK` (88)
- [ ] All 3 CLI scenarios end `plan=DELIVERED` with the correct R-rule fired
- [ ] `PYTHONHASHSEED` loop → identical outputs (P5)
- [ ] File-ledger crash test re-renders the original timeline (P1)
- [ ] UI Console: all panels + policy chips update during a live dispatch
- [ ] UI Policy: rule matrix + decision log + AI summary render
- [ ] UI Console/Policy: **AI triage brief** renders cause/checks/runbook/past incidents with a mode badge (live vs fallback)
- [ ] Registry: add/edit/delete stakeholder persists; on-call shifts update today's chips
- [ ] API: POST returns deterministic id; GET round-trip returns state
