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
};

const $ = (id) => document.getElementById(id);

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
  setControls(true);
  resetView();
  const data = await api("/api/dispatch", { method: "POST", body: JSON.stringify(body) });
  if (data.error) {
    banner(data.error, "bad");
    setControls(false);
    return;
  }
  state.payload = data;
  showAlertMeta(selectedScenario()?.alert);
  $("plan-state").textContent = "plan: " + data.plan_state;
  $("plan-state").classList.add("ready");
  renderNotifications(data.notifications);
  renderRanking(data.ranking, data.notifications);
  renderDecisions(data.decisions);
  $("timeline").textContent = data.timeline_text;
  lightRules(data.policy_codes);
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
  $("rank-body").innerHTML = '<tr><td colspan="5" class="placeholder">ranking appears after dispatch</td></tr>';
  $("selected-line").textContent = "selected: —";
  $("timeline").textContent = "… running dispatch …";
  $("plan-state").classList.remove("ready");
  $("plan-state").textContent = "plan: …";
  $("verdict-banner").textContent = "";
  document.querySelectorAll(".rule").forEach((r) => r.classList.remove("on"));
  document.querySelectorAll(".sm li").forEach((li) => li.classList.remove("done", "active"));
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
  document.querySelectorAll(".sm li").forEach((li) => {
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
  const rows = $("rank-body").querySelectorAll("tr[data-sid]");
  rows.forEach((r) => r.classList.toggle("current", r.dataset.sid === sid));
  if (sid) {
    const row = Array.from(rows).find((r) => r.dataset.sid === sid);
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
}

function finalVerdict() {
  const actions = (state.payload.decisions || []).map((d) => d.action);
  const b = $("verdict-banner");
  b.innerHTML = "";
  const span = document.createElement("span");
  if (actions.includes("ABORT")) {
    span.className = "bad";
    span.textContent = "verdict: ABORT — alert unresolved, context preserved in ledger";
  } else if (actions.includes("REROUTE") || actions.includes("ESCALATE_PARALLEL")) {
    span.className = "warn";
    span.textContent = "verdict: " + actions[actions.length - 1] + " → " + state.payload.plan_state;
  } else if (state.payload.policy_codes.length) {
    span.className = "good";
    span.textContent = "verdict: " + state.payload.policy_codes[state.payload.policy_codes.length - 1] +
      " → " + state.payload.plan_state;
  } else {
    span.className = "good";
    span.textContent = "verdict: " + state.payload.plan_state;
  }
  b.appendChild(span);
}

function markFinalRecipients() {
  const rows = $("rank-body").querySelectorAll("tr[data-sid]");
  rows.forEach((r) => r.classList.remove("current"));
  for (const n of state.payload.notifications) {
    const row = Array.from(rows).find((r) => r.dataset.sid === n.stakeholder_id);
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
  const list = $("decision-list");
  list.innerHTML = "";
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
    list.appendChild(div);
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
  document.querySelectorAll(".rule").forEach((r) => r.classList.remove("on"));
  const fired = new Set((codes || []).map(ruleKey).filter(Boolean));
  fired.forEach((key) => {
    const el = document.querySelector('.rule[data-code="' + key + '"]');
    if (el) { el.classList.add("on"); if (key === "R5") el.classList.add("r5"); }
  });
}

function banner(msg, cls) {
  const b = $("verdict-banner");
  b.innerHTML = '<span class="' + cls + '">' + escapeHtml(msg) + "</span>";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- registry modal ---------------- */

async function openRegistry() {
  const modal = $("registry-modal");
  modal.classList.remove("hidden");
  const body = $("registry-body");
  body.innerHTML = '<div class="placeholder">loading…</div>';
  const data = await api("/api/registry");
  body.innerHTML = "";
  for (const s of data.stakeholders || []) {
    const div = document.createElement("div");
    div.className = "stak";
    const chips = s.channels.map((c) =>
      '<span class="chip p' + c.priority + '">#' + c.priority + " " + escapeHtml(c.name) + "</span>"
    ).join("");
    const exp = Object.entries(s.expertise || {}).map(([d, v]) => d + " " + v + "/5").join(" · ");
    div.innerHTML =
      '<div class="shead">' +
      '<span><span class="sname">' + escapeHtml(s.name) + "</span>" +
      '<span class="sid">' + escapeHtml(s.id) + " · " + escapeHtml(s.title) + "</span></span>" +
      "<span class='dim'>sen " + s.seniority + (s.on_call ? " · ON-CALL" : " · off-call") + "</span>" +
      "</div>" +
      '<div class="expertise">expertise: ' + escapeHtml(exp || "—") + "</div>" +
      '<div class="sch">' + chips + "</div>";
    body.appendChild(div);
  }
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
  showAlertMeta(selectedScenario()?.alert);
  renderNotifications(state.payload.notifications);
  renderRanking(state.payload.ranking, state.payload.notifications);
  renderDecisions(state.payload.decisions);
  $("timeline").textContent = state.payload.timeline_text;
  lightRules(state.payload.policy_codes);
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
$("registry-btn").addEventListener("click", openRegistry);
$("registry-close").addEventListener("click", () => $("registry-modal").classList.add("hidden"));
$("registry-modal").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.classList.add("hidden");
});

const PAGES = ["ingress", "trace", "rank", "decision", "notifications", "incident", "policybar"];
function switchPage(name) {
  if (!PAGES.includes(name)) return;
  for (const id of PAGES) {
    const page = document.getElementById(id);
    if (page) page.classList.toggle("active", id === name);
  }
  for (const link of document.querySelectorAll("#side-nav a")) {
    link.classList.toggle("active", link.dataset.page === name);
  }
  const head = document.querySelector(".page.active h2");
  if (head) document.title = "Alert Routing — " + head.textContent.replace(/\s+/g, " ").trim();
}
document.querySelectorAll("#side-nav a").forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    switchPage(link.dataset.page);
  });
});
switchPage("ingress");

loadScenarios();
