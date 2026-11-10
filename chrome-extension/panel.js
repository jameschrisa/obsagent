"use strict";

const BASE = "http://127.0.0.1:8767";
const POLL_MS = 3000;
const $ = (id) => document.getElementById(id);

const state = {
  token: "",
  status: null,
  busy: false,          // a job or request is running
  dryOkFor: null,       // plan name whose dry run finished cleanly
  polling: null,
  timer: null,
  inflight: false,
  sessions: {},         // agent -> claude session id
  liveBase: null,       // {seconds, at} for the local timer
};

class HelperDown extends Error {}
class Unauthorized extends Error {}
class ApiFail extends Error {}

// ---- storage ----

async function loadStore() {
  const got = await chrome.storage.local.get(["token", "sessions", "agent"]);
  state.token = got.token || "";
  state.sessions = got.sessions || {};
  if (got.agent) $("agent").value = got.agent;
}
const saveSessions = () => chrome.storage.local.set({ sessions: state.sessions });

// ---- api ----

async function api(path, method = "GET", body = null) {
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: { Authorization: "Bearer " + state.token, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new HelperDown("The helper did not answer.");
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (res.status === 401) throw new Unauthorized("The helper did not accept the token.");
  if (!res.ok) throw new ApiFail(data.error || "Request failed (" + res.status + ").");
  return data;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- views ----

function showView(name) {
  for (const v of ["pair", "down", "main"]) $("view-" + v).hidden = v !== name;
}

function say(id, text, kind) {
  const el = $(id);
  el.textContent = text || "";
  el.className = "msg" + (kind ? " " + kind : "");
}

function fmtTime(s) {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? h + ":" + two(m) + ":" + two(r) : m + ":" + two(r);
}

function keyText(k) {
  if (!k || !k.last4) return "No key set";
  const name = k.name ? k.name + (k.label ? " (" + k.label + ")" : "") : "unnamed key";
  return name + " ..." + k.last4;
}

function renderStatus() {
  const s = state.status;
  if (!s) return;
  $("f-obs").textContent = s.obs_running ? "Running" : "Not running";
  $("f-ws").textContent = s.websocket ? "Connected" : "Not reachable";
  $("f-scene").textContent = s.scene || "-";
  $("f-profile").textContent = s.profile || "-";
  $("f-key").textContent = keyText(s.key);
  const p = s.producer || {};
  $("f-producer").textContent = p.running
    ? "Running" + (p.session ? ", session " + p.session : "")
    : "Stopped";
  $("launch-btn").hidden = s.websocket;
  $("launch-btn").textContent = s.obs_running ? "Check OBS connection" : "Launch OBS";

  const badge = $("live-badge");
  badge.className = "badge " + (s.streaming ? "on" : "off");
  badge.textContent = s.streaming ? "LIVE" : "Offline";
  state.liveBase = s.streaming && s.stream_seconds != null ? { seconds: s.stream_seconds, at: Date.now() } : null;
  tickTimer();

  renderPlans(s.plans || []);
  renderScenes(s);
  applyDisabled();
}

function tickTimer() {
  const b = state.liveBase;
  $("live-timer").textContent = b ? fmtTime(b.seconds + (Date.now() - b.at) / 1000) : "";
}

function renderPlans(plans) {
  const sel = $("plan");
  const have = Array.from(sel.options).map((o) => o.value).join("|");
  if (have === plans.join("|")) return;
  const keep = sel.value;
  sel.textContent = "";
  for (const p of plans) {
    const o = document.createElement("option");
    o.value = p; o.textContent = p;
    sel.appendChild(o);
  }
  if (plans.includes(keep)) sel.value = keep;
  else if (plans.includes("talking-head")) sel.value = "talking-head";
}

function renderScenes(s) {
  const box = $("scenes");
  const sig = JSON.stringify([s.scenes, s.scene]);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.textContent = "";
  for (const name of s.scenes || []) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "act scene";
    b.textContent = name;
    b.setAttribute("aria-pressed", name === s.scene ? "true" : "false");
    b.addEventListener("click", () => switchScene(name));
    box.appendChild(b);
  }
  if (!(s.scenes || []).length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = s.websocket ? "OBS has no scenes yet." : "Scenes show up when OBS is connected.";
    box.appendChild(p);
  }
}

function applyDisabled() {
  const s = state.status || {};
  const busy = state.busy;
  const ws = !!s.websocket;
  const prod = (s.producer && s.producer.running) || false;
  const plan = $("plan").value;
  const set = (id, off) => { $(id).disabled = busy || off; };
  set("launch-btn", false);
  set("dry-btn", !plan);
  set("apply-btn", !plan || state.dryOkFor !== plan);
  set("pre-btn", !plan);
  set("prod-start", !plan || prod);
  set("prod-stop", !prod);
  set("golive-btn", !ws || !!s.streaming || !(s.key && s.key.last4));
  set("stop-btn", !ws || !s.streaming);
  set("ask-btn", false);
  set("new-chat", false);
  for (const b of document.querySelectorAll("button.scene")) b.disabled = busy || !ws;
}

// ---- polling ----

async function refresh() {
  if (state.inflight) return;
  state.inflight = true;
  try {
    state.status = await api("/status");
    if ($("view-main").hidden) showView("main");
    renderStatus();
  } catch (e) {
    handleFatal(e);
  } finally {
    state.inflight = false;
  }
}

function handleFatal(e) {
  if (e instanceof HelperDown) { stopPolling(); showView("down"); }
  else if (e instanceof Unauthorized) {
    stopPolling();
    showView("pair");
    say("pair-msg", "The helper did not accept that token. Run obsagent serve token again and paste the new one.", "err");
  } else {
    throw e;
  }
}

function startPolling() {
  stopPolling();
  refresh();
  state.polling = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  state.timer = setInterval(tickTimer, 1000);
}
function stopPolling() {
  clearInterval(state.polling);
  clearInterval(state.timer);
  state.polling = state.timer = null;
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.token && state.polling) refresh();
});

// ---- jobs ----

async function waitJob(jobId, onTick) {
  for (let i = 0; i < 900; i++) {
    const j = await api("/jobs/" + encodeURIComponent(jobId));
    if (onTick) onTick(j);
    if (j.status !== "running") return j;
    await sleep(1000);
  }
  throw new ApiFail("The job is taking too long. Check Terminal.");
}

async function withBusy(fn) {
  state.busy = true;
  applyDisabled();
  try {
    return await fn();
  } catch (e) {
    if (e instanceof HelperDown || e instanceof Unauthorized) handleFatal(e);
    else throw e;
  } finally {
    state.busy = false;
    applyDisabled();
  }
}

function showOut(text) {
  const el = $("show-out");
  el.hidden = !text;
  el.textContent = text || "";
  el.scrollTop = el.scrollHeight;
}

function summarizePreflight(lines) {
  const count = { pass: 0, warn: 0, fail: 0 };
  for (const l of lines) {
    if (l.startsWith("[pass]")) count.pass++;
    else if (l.startsWith("[warn]")) count.warn++;
    else if (l.startsWith("[FAIL]")) count.fail++;
  }
  const box = $("pre-summary");
  box.textContent = "";
  for (const k of ["pass", "warn", "fail"]) {
    const c = document.createElement("span");
    c.className = "chip " + (count[k] ? k : "");
    c.textContent = count[k] + " " + k;
    box.appendChild(c);
  }
  box.hidden = false;
  return count;
}

async function runShowJob(path, body, label) {
  $("pre-summary").hidden = true;
  showOut(label + "...");
  return withBusy(async () => {
    try {
      const { job_id } = await api(path, "POST", body);
      const j = await waitJob(job_id, (v) => showOut(v.output_tail.join("\n")));
      showOut(j.output_tail.join("\n") || (j.status === "succeeded" ? "Done." : "Failed."));
      return j;
    } catch (e) {
      if (e instanceof ApiFail) { showOut(e.message); return null; }
      throw e;
    }
  });
}

// ---- actions ----

async function onDry() {
  const plan = $("plan").value;
  state.dryOkFor = null;
  const j = await runShowJob("/plan/apply", { plan, dry_run: true }, "Dry run of " + plan);
  if (j && j.status === "succeeded") state.dryOkFor = plan;
  applyDisabled();
}

async function onApply() {
  const plan = $("plan").value;
  const ok = await confirmDialog({
    title: "Apply " + plan + " to OBS?",
    lines: ["This changes scenes, sources and settings in the OBS that is open now.",
            "Close any open OBS dialog first."],
    okLabel: "Apply plan",
  });
  if (!ok) return;
  const j = await runShowJob("/plan/apply", { plan, dry_run: false }, "Applying " + plan);
  if (j && j.status === "succeeded") state.dryOkFor = null;
  await refresh();
}

async function onPreflight() {
  const plan = $("plan").value;
  const j = await runShowJob("/preflight", { plan }, "Preflight for " + plan);
  if (j) summarizePreflight(j.output_tail);
}

async function switchScene(name) {
  say("scene-msg", "");
  await withBusy(async () => {
    try {
      const r = await api("/scene", "POST", { name });
      say("scene-msg", r.verified ? "Switched to " + name + "." : "OBS did not confirm the switch.", r.verified ? "good" : "err");
    } catch (e) {
      if (e instanceof ApiFail) say("scene-msg", e.message, "err"); else throw e;
    }
  });
  await refresh();
}

async function onLaunch() {
  say("launch-msg", "Opening OBS and waiting for the WebSocket (up to 30 seconds)...");
  await withBusy(async () => {
    try {
      const { job_id } = await api("/obs/launch", "POST", {});
      const j = await waitJob(job_id);
      say("launch-msg", j.output_tail.slice(-1)[0] || "", j.status === "succeeded" ? "good" : "err");
    } catch (e) {
      if (e instanceof ApiFail) say("launch-msg", e.message, "err"); else throw e;
    }
  });
  await refresh();
}

async function onProdStart() {
  const plan = $("plan").value;
  const max = parseInt($("max-min").value, 10);
  if (!(max >= 1 && max <= 600)) { say("prod-msg", "Enter a whole number of minutes from 1 to 600.", "err"); return; }
  await withBusy(async () => {
    try {
      await api("/producer/start", "POST", { plan, max_minutes: max });
      say("prod-msg", "Producer started. It stops after " + max + " minutes or 15 minutes of silence.", "good");
    } catch (e) {
      if (e instanceof ApiFail) say("prod-msg", e.message, "err"); else throw e;
    }
  });
  await refresh();
}

async function onProdStop() {
  await withBusy(async () => {
    try {
      await api("/producer/stop", "POST", {});
      say("prod-msg", "Producer stopped.", "good");
    } catch (e) {
      if (e instanceof ApiFail) say("prod-msg", e.message, "err"); else throw e;
    }
  });
  await refresh();
}

async function onGoLive() {
  await refresh();
  const k = state.status && state.status.key;
  if (!k || !k.last4) { say("live-msg", "OBS has no stream key set.", "err"); return; }
  const shown = k.last4;
  const ok = await confirmDialog({
    title: "Go live now?",
    lines: ["Stream key in OBS:"],
    keybox: keyText(k),
    after: ["Before you start, check YouTube Studio. The stream you set up there must be on the channel that owns this key."],
    okLabel: "Go Live",
    danger: true,
  });
  if (!ok) return;
  await withBusy(async () => {
    try {
      await api("/stream/start", "POST", { confirm_last4: shown });
      say("live-msg", "Start sent. Watch the LIVE badge above.", "good");
    } catch (e) {
      if (e instanceof ApiFail) say("live-msg", e.message, "err"); else throw e;
    }
  });
  await refresh();
}

async function onStopStream() {
  const ok = await confirmDialog({
    title: "Stop the stream?",
    lines: ["Your audience loses the picture right away."],
    okLabel: "Stop Stream",
    danger: true,
  });
  if (!ok) return;
  await withBusy(async () => {
    try {
      await api("/stream/stop", "POST", {});
      say("live-msg", "Stop sent.", "good");
    } catch (e) {
      if (e instanceof ApiFail) say("live-msg", e.message, "err"); else throw e;
    }
  });
  await refresh();
}

async function onAsk() {
  const agent = $("agent").value;
  const message = $("ask-text").value.trim();
  const out = $("ask-out");
  out.hidden = false;
  if (!message) { out.textContent = "Type a request first."; return; }
  out.textContent = "Working. This can take a minute...";
  await withBusy(async () => {
    try {
      const { job_id } = await api("/chat", "POST", { agent, message, session_id: state.sessions[agent] || undefined });
      const j = await waitJob(job_id);
      const r = j.result || {};
      if (r.session_id) { state.sessions[agent] = r.session_id; saveSessions(); }
      if (j.status === "succeeded") { out.textContent = r.text || "(empty answer)"; $("ask-text").value = ""; }
      else out.textContent = r.text || j.output_tail.slice(-6).join("\n") || "The agent could not answer.";
    } catch (e) {
      if (e instanceof ApiFail) out.textContent = e.message; else throw e;
    }
  });
}

function onNewChat() {
  delete state.sessions[$("agent").value];
  saveSessions();
  $("ask-out").hidden = true;
  $("ask-out").textContent = "";
  $("ask-text").value = "";
}

// ---- in-page confirm dialog (no alert, confirm or prompt) ----

function confirmDialog({ title, lines = [], keybox = "", after = [], okLabel, danger = false }) {
  return new Promise((resolve) => {
    const dlg = $("confirm");
    $("c-title").textContent = title;
    const body = $("c-body");
    body.textContent = "";
    const add = (text) => { const p = document.createElement("p"); p.textContent = text; body.appendChild(p); };
    lines.forEach(add);
    if (keybox) { const d = document.createElement("div"); d.className = "keybox"; d.textContent = keybox; body.appendChild(d); }
    after.forEach(add);
    const ok = $("c-ok");
    ok.textContent = okLabel;
    ok.className = danger ? "danger" : "primary";
    let result = false;
    const onOk = () => { result = true; dlg.close(); };
    const onCancel = () => { result = false; dlg.close(); };
    const onClose = () => {
      ok.removeEventListener("click", onOk);
      $("c-cancel").removeEventListener("click", onCancel);
      dlg.removeEventListener("close", onClose);
      resolve(result);
    };
    ok.addEventListener("click", onOk);
    $("c-cancel").addEventListener("click", onCancel);
    dlg.addEventListener("close", onClose);
    dlg.showModal();
    $("c-cancel").focus();
  });
}

// ---- pairing ----

async function onPair(ev) {
  ev.preventDefault();
  const t = $("token").value.trim();
  if (!t) return;
  state.token = t;
  say("pair-msg", "Checking...");
  try {
    await api("/status");
  } catch (e) {
    state.token = "";
    if (e instanceof HelperDown) { showView("down"); return; }
    say("pair-msg", e instanceof Unauthorized ? "That token was not accepted. Copy it again from Terminal." : e.message, "err");
    return;
  }
  await chrome.storage.local.set({ token: t });
  $("token").value = "";
  say("pair-msg", "");
  startPolling();
}

async function onUnpair() {
  stopPolling();
  state.token = "";
  await chrome.storage.local.remove(["token"]);
  showView("pair");
}

// ---- boot ----

function wire() {
  $("pair-form").addEventListener("submit", onPair);
  $("retry-btn").addEventListener("click", () => { if (state.token) startPolling(); else showView("pair"); });
  $("launch-btn").addEventListener("click", onLaunch);
  $("dry-btn").addEventListener("click", onDry);
  $("apply-btn").addEventListener("click", onApply);
  $("pre-btn").addEventListener("click", onPreflight);
  $("plan").addEventListener("change", () => { state.dryOkFor = null; $("pre-summary").hidden = true; showOut(""); applyDisabled(); });
  $("prod-start").addEventListener("click", onProdStart);
  $("prod-stop").addEventListener("click", onProdStop);
  $("golive-btn").addEventListener("click", onGoLive);
  $("stop-btn").addEventListener("click", onStopStream);
  $("ask-btn").addEventListener("click", onAsk);
  $("new-chat").addEventListener("click", onNewChat);
  $("agent").addEventListener("change", () => chrome.storage.local.set({ agent: $("agent").value }));
  $("unpair").addEventListener("click", onUnpair);
}

(async function boot() {
  wire();
  await loadStore();
  if (!state.token) { showView("pair"); return; }
  startPolling();
})();
