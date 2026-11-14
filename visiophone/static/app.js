"use strict";

const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";

// Links to the go2rtc interface (port 1984 on the same host)
for (const a of document.querySelectorAll("[data-go2rtc]")) {
  a.href = `${location.protocol}//${location.hostname}:1984/${a.dataset.go2rtc}`;
}

// Snapshots refreshed periodically, only while the page is visible
for (const img of document.querySelectorAll("img[data-refresh]")) {
  const base = img.src.split("?")[0];
  setInterval(() => {
    if (document.hidden) return;
    const next = new Image();
    next.onload = () => { img.src = next.src; img.classList.remove("broken"); };
    next.onerror = () => img.classList.add("broken");
    next.src = `${base}?t=${Date.now()}`;
  }, Number(img.dataset.refresh));
}

// Confirmation before dangerous submits
document.addEventListener("click", (e) => {
  const button = e.target.closest("[data-confirm]");
  if (button && !confirm(button.dataset.confirm)) e.preventDefault();
});

async function post(url, data = {}) {
  const body = new URLSearchParams(data);
  const resp = await fetch(url, { method: "POST", body, headers: { "X-CSRF-Token": CSRF } });
  let json = {};
  try { json = await resp.json(); } catch (_) { /* not JSON */ }
  return { ok: resp.ok && json.ok !== false, json };
}

function toast(message, ok = true) {
  const div = document.createElement("div");
  div.className = `flash flash-${ok ? "ok" : "error"} toast`;
  div.textContent = message;
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 4000);
}

// Buttons that call an API without leaving the page
document.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-post]");
  if (!button) return;
  e.preventDefault();
  button.disabled = true;
  const data = button.dataset.action ? { action: button.dataset.action } : {};
  const { ok, json } = await post(button.dataset.post, data);
  button.disabled = false;
  toast(json.message || (ok ? "Done." : "Failed."), ok);
});

// Show the fields matching the selected camera source
function showForSource(formId) {
  const form = document.getElementById(formId);
  const update = () => {
    const source = form.querySelector('input[name="source"]:checked')?.value;
    for (const block of form.querySelectorAll("[data-show-for]")) {
      const visible = block.dataset.showFor === source;
      block.hidden = !visible;
      for (const input of block.querySelectorAll("input, select")) input.disabled = !visible;
    }
  };
  form.addEventListener("change", update);
  update();
}

function relativeTime(seconds) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - seconds));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min ago`;
  return `${Math.floor(s / 86400)} days ago`;
}

function renderTimes() {
  for (const el of document.querySelectorAll("[data-time]")) {
    const value = el.dataset.time;
    if (!value) continue;
    const epoch = isNaN(value) ? Date.parse(value) / 1000 : Number(value);
    const date = new Date(epoch * 1000).toLocaleString();
    el.textContent = el.dataset.format === "since" ? `${date} (${relativeTime(epoch).replace(" ago", "")})`
                                                   : `${date} (${relativeTime(epoch)})`;
  }
}
renderTimes();

function pill(text, kind) {
  return `<span class="pill ${kind}">${text}</span>`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function startStatusRefresh() {
  const set = (key, html) => {
    const el = document.querySelector(`[data-key="${key}"]`);
    if (el) el.innerHTML = html;
  };
  setInterval(async () => {
    if (document.hidden) return;
    const resp = await fetch("/api/status");
    if (!resp.ok) return;
    const s = await resp.json();
    const m = s.metrics;
    set("cpu_temperature", `${m.cpu_temperature} °C`);
    set("cpu_load", `${m.cpu_load} %`);
    set("memory_used", `${m.memory_used} %`);
    set("disk_used", `${m.disk_used} %`);
    set("go2rtc", m.go2rtc ? pill("running", "ok") : pill("stopped", "bad"));
    set("clients", s.stream_clients);
    set("live", s.live_viewers);
    set("mqtt", !s.mqtt.enabled ? pill("disabled", "") : s.mqtt.connected ? pill("connected", "ok")
      : `${pill("disconnected", "bad")} <small>${escapeHtml(s.mqtt.error || "")}</small>`);
    const last = document.querySelector('[data-key="last-ring"]');
    if (last && s.button.last) last.dataset.time = s.button.last;
    renderTimes();
  }, 5000);
}

function startButtonTest() {
  const dot = document.getElementById("button-dot");
  const text = document.getElementById("button-text");
  let count = null;
  setInterval(async () => {
    if (document.hidden) return;
    const resp = await fetch("/api/status");
    if (!resp.ok) return;
    const b = (await resp.json()).button;
    dot.classList.toggle("on", b.pressed);
    if (b.error) {
      text.textContent = `Button error: ${b.error}`;
    } else if (count !== null && b.count > count) {
      text.textContent = `Ring received (${new Date().toLocaleTimeString()})`;
    }
    count = b.count;
  }, 400);
}

function waitForReboot() {
  // Wait for the Pi to go down, then poll until the interface answers again
  let wentDown = false;
  setInterval(async () => {
    try {
      const resp = await fetch("/login", { cache: "no-store" });
      if (resp.ok && wentDown) location.href = "/";
    } catch (_) {
      wentDown = true;
    }
  }, 3000);
}
