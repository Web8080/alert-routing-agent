/* Alert Routing Agent dashboard — vanilla JS, zero dependencies. */

"use strict";

const state = {
  scenarios: [],
  payload: null,
  playing: false,
  cursor: 0,
  timer: null,
  speed: 8,
  currentSid: null,
  registryData: [],
  roster: null,
};

const $ = (id) => document.getElementById(id);
const all = (sel) => Array.from(document.querySelectorAll(sel));
function els(ids) { return ids.map((id) => $(id)).filter(Boolean); }

function ruleKey(code) {
  if (!code) return null;
  const m = code.match(/^(R[1-6])/);
  return m ? m[1] : null;
}

const KIND_LABEL = { ingress: "INGRESS", rank: "RANK", plan: "PLAN", send: "SEND",
                     event: "EVENT", policy: "POLICY", ledger: "LEDGER",
                     notify: "NOTIFY", summary: "SUMMARY" };

const KIND_STAGE = { ingress: "received", rank: "ranked", plan: "planned",
                     send: "dispatching", event: "event", policy: "decision",
                     ledger: "result", notify: "result", summary: "result" };

/* ---------------- API ---------------- */

async function api(url, opts = {}) {
  const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
  return res.json();
}

async function loadScenarios() {
  const data = await api("/api/scenarios");
  state.scenarios = data.scenarios || [];
  const sel = $("scenario");
  sel.innerHTML = "";
  for (const s of state.scenarios) {
    const opt = document.createElement("option");
    opt.value = s.name;
    opt.textContent = s.name + " — " + s.description;
    sel.appendChild(opt);
  }
  sel.disabled = state.scenarios.length === 0;
  $("dispatch").disabled = state.scenarios.length === 0;
  if (state.scenarios.length) {
    sel.selectedIndex = 0;
    showAlertMeta(state.scenarios[0].alert);
  }
}

function selectedScenario() {
  const sel = $("scenario");
  return state.scenarios.find((s) => s.name === sel.value);
}

async function runDispatch(body) {
  if ($("ai-toggle").checked) body.summary = true;
  setControls(true);
  resetView();
  const data = await api("/api/dispatch", { method: "POST", body: JSON.stringify(body) });
  if (data.error) {
    banner(data.error, "bad");
    setControls(false);
    return;
  }
  state.payload = data;
  showAlertMeta(data.alert || selectedScenario()?.alert);
  $("a-id").textContent = data.alert_id || "—";
  $("plan-state").textContent = "plan: " + data.plan_state;
  $("plan-state").classList.add("ready");
  $("plan-state-mini").textContent = "plan: " + data.plan_state;
  renderNotifications(data.notifications);
  renderRanking(data.ranking, data.notifications);
  renderDecisions(data.decisions);
  all("#timeline").forEach((el) => { el.textContent = data.timeline_text; });
  lightRules(data.policy_codes);
  renderAI(data);
  playTrace(data.trace);
}

/* ---------------- rendering ---------------- */

function showAlertMeta(a) {
  if (!a) return;
  $("a-id").textContent = "—";
  $("a-metric").textContent = a.metric;
  $("a-value").textContent = a.value + " (threshold " + a.threshold + ")";
  const sev = $("a-severity");
  sev.textContent = a.severity;
  sev.className = a.severity === "CRITICAL" ? "CRITICAL" : "";
  $("a-domain").textContent = a.domain;
  $("a-context").textContent = JSON.stringify(a.context || {});
}

function resetView() {
  state.payload = null;
  state.cursor = 0;
  state.currentSid = null;
  stopPlay();
  $("trace-log").innerHTML = "";
  $("events").innerHTML = "";
  $("notifications").innerHTML = "";
  $("decision-card").innerHTML = '<div class="placeholder">—</div>';
  $("decision-list").innerHTML = "";
  $("decision-list-policy").innerHTML = "";
  $("rank-body").innerHTML = '<tr><td colspan="5" class="placeholder">ranking appears after dispatch</td></tr>';
  $("selected-line").textContent = "selected: —";
  all("#timeline").forEach((el) => { el.textContent = "… running dispatch …"; });
  $("plan-state").classList.remove("ready");
  $("plan-state").textContent = "plan: …";
  $("plan-state-mini").textContent = "plan: —";
  $("trace-card").classList.remove("running");
  $("trace-status").textContent = "idle";
  for (const id of ["verdict-banner", "verdict-banner-policy"]) {
    const el = $(id); el.textContent = ""; el.classList.add("dim");
  }
  els(["ai-summary", "ai-summary-policy"]).forEach((el) => {
    el.textContent = "…";
    el.classList.remove("ai-live");
  });
  els(["ai-runbook", "ai-runbook-policy"]).forEach((el) => { el.textContent = ""; });
  all(".rule").forEach((r) => r.classList.remove("on"));
  all(".sm li").forEach((li) => li.classList.remove("done", "active"));
}

function traceLineEl(line) {
  const el = document.createElement("div");
  el.className = "trace-line k-" + line.kind;
  const k = KIND_LABEL[line.kind] || line.kind.toUpperCase();
  el.innerHTML = '<span class="ts">' + line.ts + "</span>" +
    '<span class="k">' + k + "</span>" + escapeHtml(line.text);
  return el;
}

function updateStateMachine(kind) {
  const stage = KIND_STAGE[kind];
  if (!stage) return;
  all(".sm li").forEach((li) => {
    li.classList.remove("active");
    if (li.dataset.stage === stage) li.classList.add("active");
    else if (li.dataset.stage && stageOrder(li.dataset.stage) < stageOrder(stage)) {
      li.classList.add("done");
    }
  });
}
function stageOrder(s) {
  return ["received", "ranked", "planned", "dispatching", "event", "decision", "result"]
    .indexOf(s);
}

function markCurrentRecipient(sid) {
  state.currentSid = sid;
  const rows = all("#rank-body tr[data-sid]");
  rows.forEach((r) => r.classList.toggle("current", r.dataset.sid === sid));
  if (sid) {
    const row = rows.find((r) => r.dataset.sid === sid);
    if (row) $("selected-line").textContent = "selected: " + row.dataset.name + " (" + sid + ")";
  }
}

function renderTraceStep() {
  const trace = state.payload.trace;
  if (!trace || state.cursor >= trace.length) {
    stopPlay();
    $("play").textContent = "▶ play";
    updateStateMachine("summary");
    finalVerdict();
    markFinalRecipients();
    return;
  }
  const line = trace[state.cursor];
  const log = $("trace-log");
  const wasAtBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;

  if (line.kind === "event") {
    const ev = document.createElement("div");
    ev.className = "event";
    ev.innerHTML = '<span class="ts">' + line.ts + "</span>" + escapeHtml(line.text);
    $("events").prepend(ev);
  }
  if (line.kind === "send") {
    const m = line.text.match(/-> (\S+) via/);
    if (m) markCurrentRecipient(m[1]);
  }
  const el = traceLineEl(line);
  log.appendChild(el);
  log.scrollTop = wasAtBottom ? log.scrollHeight : log.scrollTop;

  const prev = log.children[log.children.length - 2];
  if (prev && prev.classList) prev.classList.remove("hot");
  el.classList.add("hot", "flash");

  updateStateMachine(line.kind);
  state.cursor += 1;
}

function playTrace(trace) {
  state.cursor = 0;
  $("trace-log").innerHTML = "";
  state.playing = true;
  $("play").textContent = "⏸ pause";
  $("trace-card").classList.add("running");
  $("trace-status").textContent = "running";
  tick();
}

function tick() {
  if (!state.playing) return;
  renderTraceStep();
  const delay = Math.max(20, Math.round(1200 / state.speed));
  state.timer = setTimeout(tick, delay);
}

function stopPlay() {
  state.playing = false;
  if (state.timer) { clearTimeout(state.timer); state.timer = null; }
  const last = $("trace-log").lastElementChild;
  if (last) last.classList.remove("hot");
  const card = $("trace-card");
  if (card) {
    card.classList.remove("running");
    if (!state.payload) $("trace-status").textContent = "idle";
    else if (state.cursor < (state.payload.trace || []).length) $("trace-status").textContent = "paused";
    else $("trace-status").textContent = "done";
  }
}

function finalVerdict() {
  const actions = (state.payload.decisions || []).map((d) => d.action);
  let cls = "good";
  let text;
  if (actions.includes("ABORT")) {
    cls = "bad";
    text = "verdict: ABORT — alert unresolved, context preserved in ledger";
  } else if (actions.includes("REROUTE") || actions.includes("ESCALATE_PARALLEL")) {
    cls = "warn";
    text = "verdict: " + actions[actions.length - 1] + " → " + state.payload.plan_state;
  } else if (state.payload.policy_codes.length) {
    text = "verdict: " + state.payload.policy_codes[state.payload.policy_codes.length - 1] +
      " → " + state.payload.plan_state;
  } else {
    text = "verdict: " + state.payload.plan_state;
  }
  for (const id of ["verdict-banner", "verdict-banner-policy"]) {
    const el = $(id);
    el.classList.remove("dim");
    el.innerHTML = '<span class="' + cls + '">' + escapeHtml(text) + "</span>";
  }
}

function markFinalRecipients() {
  const rows = all("#rank-body tr[data-sid]");
  rows.forEach((r) => r.classList.remove("current"));
  for (const n of state.payload.notifications) {
    const row = rows.find((r) => r.dataset.sid === n.stakeholder_id);
    if (row) row.classList.add("current");
  }
}

function renderRanking(ranking, notifications) {
  const body = $("rank-body");
  body.innerHTML = "";
  if (!ranking.length) {
    body.innerHTML = '<tr><td colspan="5" class="placeholder">no stakeholders</td></tr>';
    return;
  }
  const statusBySid = {};
  for (const n of notifications) statusBySid[n.stakeholder_id] = n.status;

  ranking.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.dataset.sid = r.sid;
    tr.dataset.name = r.name;
    if (r.gated) tr.classList.add("gated-row");
    const avail = r.gated
      ? '<span class="dot offline"></span><span class="gated-tag">GATED</span>'
      : r.online
        ? '<span class="dot online"></span>online'
        : '<span class="dot offline"></span>offline';
    const role = r.title + (r.on_call ? "" : " · off-call");
    tr.innerHTML =
      "<td>" + (i + 1) + "</td>" +
      "<td>" + escapeHtml(r.name) + (statusBySid[r.sid] ? " <span class='gated-tag'>" + statusBySid[r.sid] + "</span>" : "") + "</td>" +
      "<td>" + r.qualification.toFixed(2) + "</td>" +
      "<td>" + avail + "</td>" +
      "<td>" + escapeHtml(role) + "</td>";
    body.appendChild(tr);
  });
}

function renderDecisions(decisions) {
  const card = $("decision-card");
  for (const id of ["decision-list", "decision-list-policy"]) {
    const el = $(id); el.innerHTML = "";
  }
  if (!decisions.length) {
    card.innerHTML = '<div class="placeholder">no policy decision was needed — straight delivery</div>';
    return;
  }
  const last = decisions[decisions.length - 1];
  const target = last.target ? " → " + last.target : "";
  card.innerHTML =
    '<div class="dcode">' + escapeHtml(last.code) + '</div>' +
    '<span class="daction">' + escapeHtml(last.action) + target + "</span>" +
    '<div class="drationale">' + escapeHtml(last.rationale) + "</div>" +
    '<div class="dresult">plan_state: ' + escapeHtml(state.payload.plan_state) +
    " · no duplicate · context preserved</div>";

  decisions.forEach((d) => {
    const div = document.createElement("div");
    div.className = "dentry";
    div.innerHTML = '<span class="d-code">' + escapeHtml(d.code) + "</span> " +
      escapeHtml(d.action) + (d.target ? " → " + escapeHtml(d.target) : "");
    for (const id of ["decision-list", "decision-list-policy"]) {
      $(id).appendChild(div.cloneNode(true));
    }
  });
}

function renderNotifications(rows) {
  const wrap = $("notifications");
  wrap.innerHTML = "";
  if (!rows.length) { wrap.innerHTML = '<div class="placeholder">—</div>'; return; }
  for (const n of rows) {
    const div = document.createElement("div");
    div.className = "notif";
    div.innerHTML =
      '<span class="st">' + escapeHtml(n.stakeholder_name) + "</span>" +
      '<span class="ch">' + escapeHtml(n.channel) + "</span>" +
      '<span class="stt ' + escapeHtml(n.status) + '">' + escapeHtml(n.status) + "</span>" +
      '<span class="lvl dim">l' + n.escalation_level + "</span>";
    wrap.appendChild(div);
  }
}

function lightRules(codes) {
  all(".rule").forEach((r) => r.classList.remove("on"));
  const fired = new Set((codes || []).map(ruleKey).filter(Boolean));
  fired.forEach((key) => {
    all('.rule[data-code="' + key + '"]').forEach((el) => {
      el.classList.add("on");
      if (key === "R5") el.classList.add("r5");
    });
  });
}

function banner(msg, cls) {
  for (const id of ["verdict-banner", "verdict-banner-policy"]) {
    const el = $(id);
    el.classList.remove("dim");
    el.innerHTML = '<span class="' + cls + '">' + escapeHtml(msg) + "</span>";
  }
}

function renderAI(data) {
  const enabled = !!data.ai_enabled;
  const badge = enabled ? "· AI live" : "· AI off — deterministic fallback";
  els(["ai-badge", "ai-badge-policy"]).forEach((el) => { el.textContent = badge; });
  const summary = data.ai_summary || "—";
  els(["ai-summary", "ai-summary-policy"]).forEach((el) => {
    el.textContent = summary;
    el.classList.toggle("ai-live", enabled);
  });
  const runbook = data.ai_runbook || "";
  els(["ai-runbook", "ai-runbook-policy"]).forEach((el) => { el.textContent = runbook; });

  const triage = data.ai_triage;
  els(["triage-badge", "triage-badge-policy"]).forEach((el) => {
    if (!triage) { el.textContent = ""; return; }
    const mode = triage.mode === "ai" ? "live" : "fallback";
    const fb = (triage.agents || []).filter((a) => a.fallback).length;
    const lat = triage.elapsed_ms != null ? " · " + Math.round(triage.elapsed_ms) + "ms" : "";
    el.textContent = "· " + mode + (fb ? " · " + fb + " fallback" : "") + lat;
  });
  const triageHTML = triage ? renderTriageHTML(triage)
    : '<div class="placeholder">brief renders after a dispatch</div>';
  els(["ai-triage", "ai-triage-policy"]).forEach((el) => { el.innerHTML = triageHTML; });
}

function renderTriageHTML(t) {
  const b = t.triage || {};
  const safety = t.safety;
  let html = "";
  if (t.mode === "ai") {
    html += '<div class="triage-note">supervised triage · advisory only · deterministic routing unchanged</div>';
  }
  const cause = b.likely_cause || "—";
  const conf = b.confidence != null ? '<span class="conf dim mono">' + Math.round(b.confidence * 100) + "%</span>" : "";
  html += "<div><b>Cause:</b> " + escapeHtml(cause) + " " + conf + "</div>";
  html += renderListHTML("First checks", b.first_checks);
  html += renderListHTML("Remediation", b.remediation_steps);
  if (b.escalation_criteria) {
    html += "<div><b>Escalate if:</b> " + escapeHtml(b.escalation_criteria) + "</div>";
  }
  if (b.runbook && b.runbook.id) {
    html += '<div class="runbook-note">' + escapeHtml(b.runbook.id + "\n" + (b.runbook.snippet || "")) + "</div>";
  }
  if (Array.isArray(b.similar_incidents) && b.similar_incidents.length) {
    html += "<div class=\"triage-similar\"><b>Past incidents:</b>";
    b.similar_incidents.forEach((si) => {
      html += '<div class="triage-sim-item dim">' + escapeHtml(si.id + " · " +
        Math.round((si.similarity || 0) * 100) + "% · " + (si.resolution || "")) + "</div>";
    });
    html += "</div>";
  }
  if (safety && !safety.ok) {
    html += '<div class="triage-warn">safety gate: AI brief flagged — replaced with deterministic brief</div>';
  }
  return html;
}

function renderListHTML(label, items) {
  if (!Array.isArray(items) || !items.length) return "";
  let html = "<div><b>" + label + ":</b><ul class=\"triage-list\">";
  items.forEach((i) => { html += "<li>" + escapeHtml(i) + "</li>"; });
  html += "</ul></div>";
  return html;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- registry page ---------------- */

const CHANNEL_TYPES = ["email", "slack", "sms"];

function addChannelRow(name, priority, endpoint) {
  const div = document.createElement("div");
  div.className = "ch-row";
  div.innerHTML =
    '<select class="ch-name">' + CHANNEL_TYPES.map((c) =>
      '<option' + (c === name ? " selected" : "") + ">" + c + "</option>").join("") + "</select>" +
    '<input class="ch-priority" type="number" min="1" value="' + (priority || 1) + '">' +
    '<input class="ch-endpoint" placeholder="endpoint" value="' + escapeHtml(endpoint || "") + '">' +
    '<button type="button" class="ch-del ghost">✕</button>';
  div.querySelector(".ch-del").addEventListener("click", () => div.remove());
  $("ed-channels").appendChild(div);
}

function openEditor(st) {
  $("stak-editor").classList.remove("hidden");
  $("editor-title").textContent = st ? "EDIT " + st.name : "ADD STAKEHOLDER";
  $("ed-id").value = st ? st.id : "";
  $("ed-name").value = st ? st.name : "";
  $("ed-title").value = st ? st.title || "" : "";
  $("ed-seniority").value = st ? st.seniority : 3;
  $("ed-oncall").checked = st ? st.on_call : false;
  $("ed-expertise").value = st
    ? Object.entries(st.expertise || {}).map(([d, v]) => d + "=" + v).join(", ")
    : "";
  $("ed-channels").innerHTML = "";
  const chans = st && st.channels.length ? st.channels : [{ name: "email", priority: 1, endpoint: "" }];
  chans.forEach((c) => addChannelRow(c.name, c.priority, c.endpoint));
  $("edit-status").textContent = "";
  $("stak-editor").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeEditor() {
  $("stak-editor").classList.add("hidden");
}

function collectStakeholder() {
  const expertise = {};
  $("ed-expertise").value.split(",").map((s) => s.trim()).filter(Boolean).forEach((pair) => {
    const [d, v] = pair.split("=").map((s) => s.trim());
    if (d) expertise[d] = Number(v);
  });
  const channels = [];
  for (const row of all("#ed-channels .ch-row")) {
    channels.push({
      name: row.querySelector(".ch-name").value,
      priority: Number(row.querySelector(".ch-priority").value) || 1,
      endpoint: row.querySelector(".ch-endpoint").value.trim(),
    });
  }
  return {
    id: $("ed-id").value || undefined,
    name: $("ed-name").value.trim(),
    title: $("ed-title").value.trim(),
    seniority: Number($("ed-seniority").value),
    expertise,
    on_call: $("ed-oncall").checked,
    channels,
  };
}

async function saveStakeholder(e) {
  e.preventDefault();
  const item = collectStakeholder();
  $("edit-status").textContent = "saving…";
  const data = await api("/api/registry", { method: "POST", body: JSON.stringify(item) });
  if (data.error) { $("edit-status").textContent = data.error; return; }
  closeEditor();
  regFlash(data.stakeholder + " saved");
  await loadRegistry();
}

async function deleteStakeholder(sid) {
  if (!confirm("Remove " + sid + " from the registry? Existing ledger history is kept.")) return;
  const data = await api("/api/registry/" + sid, { method: "DELETE" });
  if (data.error) { regFlash(data.error, true); return; }
  regFlash(sid + " removed");
  await loadRegistry();
}

async function setOnCall(sid, on) {
  const data = await api("/api/registry/" + sid + "/on-call", {
    method: "POST",
    body: JSON.stringify({ on_call: on }),
  });
  if (data.error) { regFlash(data.error, true); return; }
  await loadRegistry();
}

function paintRegistry(body, rows) {
  body.innerHTML = "";
  for (const s of rows) {
    const div = document.createElement("div");
    div.className = "stak";
    const chips = s.channels.map((c) =>
      '<span class="chip p' + c.priority + '">#' + c.priority + " " + escapeHtml(c.name) +
      (c.webhook_missing ? '<span class="whint" title="no webhook in .env — stub delivery">*</span>' : "") +
      "</span>"
    ).join("");
    const exp = Object.entries(s.expertise || {}).map(([d, v]) => d + " " + v + "/5").join(" · ");
    div.innerHTML =
      '<div class="shead">' +
      '<span><span class="sname">' + escapeHtml(s.name) + "</span>" +
      '<span class="sid">' + escapeHtml(s.id) + " · " + escapeHtml(s.title || "—") + "</span></span>" +
      "<span class='dim'>sen " + s.seniority + (s.on_call ? " · ON-CALL" : " · off-call") + "</span>" +
      "</div>" +
      '<div class="expertise">expertise: ' + escapeHtml(exp || "—") + "</div>" +
      '<div class="sch">' + chips + "</div>" +
      '<div class="card-actions">' +
      '<button type="button" class="oc-toggle' + (s.on_call ? " on" : "") + '" data-oc data-sid="' + s.id + '">' + (s.on_call ? "on-call ✓" : "set on-call") + "</button>" +
      '<button type="button" class="ghost" data-edit data-sid="' + s.id + '">edit</button>' +
      '<button type="button" class="ghost danger" data-del data-sid="' + s.id + '">✕</button>' +
      "</div>";
    body.appendChild(div);
  }
  body.querySelectorAll("[data-edit]").forEach((b) =>
    b.addEventListener("click", () => {
      const st = state.registryData.find((x) => x.id === b.dataset.sid);
      if (st) openEditor(st);
    }));
  body.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", () => deleteStakeholder(b.dataset.sid)));
  body.querySelectorAll("[data-oc]").forEach((b) =>
    b.addEventListener("click", () => {
      const st = state.registryData.find((x) => x.id === b.dataset.sid);
      if (st) setOnCall(st.id, !st.on_call);
    }));
}

function paintRosterChips(rost) {
  $("roster-day").textContent = "· " + rost.today;
  $("roster-mode").textContent = rost.shift_mode
    ? "roster active — today's shifts drive dispatch"
    : "no shift covers today — falling back to registry on-call flags";
  const wrap = $("oncall-chips");
  wrap.innerHTML = "";
  if (!(rost.on_call_names || []).length) {
    wrap.innerHTML = '<div class="placeholder">nobody on call today</div>';
    return;
  }
  for (const name of rost.on_call_names) {
    const span = document.createElement("span");
    span.className = "chip p1";
    span.textContent = name;
    wrap.appendChild(span);
  }
}

function paintShiftList(shifts) {
  const wrap = $("shift-list");
  wrap.innerHTML = "";
  if (!shifts.length) { wrap.innerHTML = '<div class="placeholder">no shifts yet</div>'; return; }
  const names = (sid) => {
    const st = state.registryData.find((x) => x.id === sid);
    return st ? st.name : sid;
  };
  for (const s of shifts) {
    const div = document.createElement("div");
    div.className = "shift-entry";
    div.innerHTML =
      '<span class="shift-dates">' + escapeHtml(s.start) + " → " + escapeHtml(s.end) + "</span>" +
      '<span class="shift-people">' + escapeHtml(names(s.primary)) +
      (s.backups.length ? " + " + escapeHtml(s.backups.map(names).join(", ")) : "") + "</span>" +
      '<span class="shift-actions">' +
      '<button type="button" class="ghost" data-sh-edit data-id="' + escapeHtml(s.id) + '">edit</button>' +
      '<button type="button" class="ghost danger" data-sh-del data-id="' + escapeHtml(s.id) + '">✕</button>' +
      "</span>";
    wrap.appendChild(div);
  }
  wrap.querySelectorAll("[data-sh-edit]").forEach((b) =>
    b.addEventListener("click", () => {
      const shift = (state.roster.shifts || []).find((x) => x.id === b.dataset.id);
      if (shift) openShiftEditor(shift);
    }));
  wrap.querySelectorAll("[data-sh-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      const d = await api("/api/roster/" + b.dataset.id, { method: "DELETE" });
      if (d.error) { regFlash(d.error, true); return; }
      regFlash("shift removed");
      await loadRegistry();
    }));
}

function fillShiftSelects() {
  const primary = $("shift-primary");
  const backups = $("shift-backups");
  primary.innerHTML = "";
  backups.innerHTML = "";
  for (const s of state.registryData) {
    primary.appendChild(new Option(s.name + " (" + s.id + ")", s.id));
    backups.appendChild(new Option(s.name + " (" + s.id + ")", s.id));
  }
}

function openShiftEditor(shift) {
  fillShiftSelects();
  $("roster-panel").classList.remove("hidden");
  $("shift-id").value = shift ? shift.id : "";
  $("shift-start").value = shift ? shift.start : (state.roster ? state.roster.today : "");
  $("shift-end").value = shift ? shift.end : (state.roster ? state.roster.today : "");
  $("shift-primary").value = shift
    ? shift.primary
    : (state.registryData.find((x) => x.on_call) || {}).id || "";
  const selected = new Set(shift ? shift.backups : []);
  for (const opt of $("shift-backups").options) opt.selected = selected.has(opt.value);
  $("roster-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function saveShift(e) {
  e.preventDefault();
  const body = {
    start: $("shift-start").value,
    end: $("shift-end").value,
    primary: $("shift-primary").value,
    backups: Array.from($("shift-backups").selectedOptions).map((o) => o.value),
  };
  if ($("shift-id").value) body.id = $("shift-id").value;
  const data = await api("/api/roster", { method: "POST", body: JSON.stringify(body) });
  if (data.error) { regFlash(data.error, true); return; }
  regFlash("shift saved");
  await loadRegistry();
}

function regFlash(msg, isError) {
  const el = $("reg-status");
  el.textContent = msg;
  el.classList.toggle("error", !!isError);
  clearTimeout(regFlash._t);
  regFlash._t = setTimeout(() => { el.textContent = ""; }, 4000);
}

async function loadRegistry() {
  const [r, rost] = await Promise.all([api("/api/registry"), api("/api/roster")]);
  state.registryData = r.stakeholders || [];
  state.roster = rost;
  paintRegistry($("registry-body"), state.registryData);
  paintRosterChips(rost);
  paintShiftList(rost.shifts || []);
}

/* ---------------- controls ---------------- */

function setControls(disable) {
  $("dispatch").disabled = disable;
  $("dispatch-custom").disabled = disable;
  $("replay").disabled = !state.payload || disable;
  $("step").disabled = disable;
  $("play").disabled = disable;
}

function stepOnce() {
  if (!state.payload) return;
  stopPlay();
  renderTraceStep();
}

function togglePlay() {
  if (!state.payload) return;
  if (state.playing) {
    stopPlay();
    $("play").textContent = "▶ play";
  } else if (state.cursor < state.payload.trace.length) {
    state.playing = true;
    $("play").textContent = "⏸ pause";
    tick();
  }
}

function replay() {
  if (!state.payload) return;
  resetView();
  showAlertMeta(state.payload.alert || selectedScenario()?.alert);
  renderNotifications(state.payload.notifications);
  renderRanking(state.payload.ranking, state.payload.notifications);
  renderDecisions(state.payload.decisions);
  all("#timeline").forEach((el) => { el.textContent = state.payload.timeline_text; });
  lightRules(state.payload.policy_codes);
  renderAI(state.payload);
  playTrace(state.payload.trace);
}

/* ---------------- wiring ---------------- */

$("scenario").addEventListener("change", () => showAlertMeta(selectedScenario()?.alert));
$("dispatch").addEventListener("click", () => {
  const s = selectedScenario();
  if (s) runDispatch({ scenario: s.name });
});
$("dispatch-custom").addEventListener("click", () => {
  try {
    const alert = JSON.parse($("custom-alert").value);
    runDispatch({ alert });
  } catch (e) {
    banner("invalid custom alert JSON: " + e.message, "bad");
  }
});
$("play").addEventListener("click", togglePlay);
$("step").addEventListener("click", stepOnce);
$("replay").addEventListener("click", replay);
$("speed").addEventListener("input", (e) => { state.speed = Number(e.target.value); });

$("stak-add").addEventListener("click", () => openEditor(null));
$("stak-cancel").addEventListener("click", closeEditor);
$("stak-form").addEventListener("submit", saveStakeholder);
$("ch-add").addEventListener("click", () => addChannelRow("email", 1, ""));
$("roster-open").addEventListener("click", () => openShiftEditor(null));
$("shift-cancel").addEventListener("click", () => $("roster-panel").classList.add("hidden"));
$("shift-form").addEventListener("submit", saveShift);

const PAGES = ["console", "policy", "registry"];
function switchPage(name) {
  if (!PAGES.includes(name)) return;
  for (const id of PAGES) {
    const page = document.getElementById(id);
    if (page) page.classList.toggle("active", id === name);
  }
  for (const link of document.querySelectorAll("#side-nav a")) {
    link.classList.toggle("active", link.dataset.page === name);
  }
  if (name === "registry") loadRegistry();
  const head = document.querySelector(".page.active h2");
  if (head) document.title = "Alert Routing — " + head.textContent.replace(/\s+/g, " ").trim();
}
document.querySelectorAll("#side-nav a").forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    switchPage(link.dataset.page);
  });
});
switchPage("console");

loadScenarios();
