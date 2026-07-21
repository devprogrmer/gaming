"use strict";
// Dependency-free dashboard client. All state lives here; the server is a JSON
// API. No framework, no build step, works offline from bundled assets.

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const kid of kids) node.append(kid);
  return node;
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  return { ok: res.ok, status: res.status, data };
}

function badge(label) {
  const cls = label && label !== "not checked" ? label : "none";
  return el("span", { class: `badge ${cls}` }, label || "-");
}

// ---- auth / boot ---------------------------------------------------------
async function boot() {
  const me = await api("/api/me");
  if (me.ok && me.data && me.data.username) {
    showApp(me.data.username);
  } else {
    showLogin();
  }
}

function showLogin() {
  $("#login-view").classList.remove("hidden");
  $("#app-view").classList.add("hidden");
}

function showApp(username) {
  $("#login-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $("#whoami").textContent = username;
  navigate(location.hash.replace("#", "") || "home");
  loadSummary();
}

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("#login-error").textContent = "";
  const r = await api("/api/login", {
    method: "POST",
    body: { username: $("#login-user").value, password: $("#login-pass").value },
  });
  if (r.ok) boot();
  else $("#login-error").textContent = (r.data && r.data.error) || "login failed";
});

$("#logout-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  showLogin();
});

// ---- navigation ----------------------------------------------------------
function navigate(view) {
  for (const link of document.querySelectorAll(".nav-link"))
    link.classList.toggle("active", link.dataset.view === view);
  for (const sec of document.querySelectorAll(".view")) sec.classList.add("hidden");
  const target = $(`#view-${view}`);
  if (target) target.classList.remove("hidden");
  if (view === "history") loadHistory();
  if (view === "settings") loadSettings();
  if (view === "home") loadSummary();
}
for (const link of document.querySelectorAll(".nav-link"))
  link.addEventListener("click", () => navigate(link.dataset.view));

// ---- generic sortable table ---------------------------------------------
function renderTable(tableEl, columns, rows) {
  tableEl.innerHTML = "";
  const thead = el("thead");
  const htr = el("tr");
  columns.forEach((col, i) => {
    const th = el("th", {}, col.label);
    th.addEventListener("click", () => sortBy(tableEl, columns, rows, i));
    htr.append(th);
  });
  thead.append(htr);
  const tbody = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    for (const col of columns) {
      const val = row[col.key];
      const td = el("td");
      if (col.badge) td.append(badge(val));
      else td.textContent = val === null || val === undefined ? "-" : String(val);
      tr.append(td);
    }
    tbody.append(tr);
  }
  tableEl.append(thead, tbody);
}
function sortBy(tableEl, columns, rows, i) {
  const key = columns[i].key;
  const dir = tableEl._sortDir === key ? -1 : 1;
  tableEl._sortDir = dir === 1 ? key : null;
  rows.sort((a, b) => {
    const x = a[key], y = b[key];
    if (x === y) return 0;
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return (x > y ? 1 : -1) * dir;
  });
  renderTable(tableEl, columns, rows);
}

// ---- search --------------------------------------------------------------
$("#search-btn").addEventListener("click", async () => {
  $("#search-status").textContent = "Searching…";
  const r = await api("/api/search", {
    method: "POST",
    body: {
      query: $("#q-query").value, provider: $("#q-provider").value,
      country: $("#q-country").value, asn: $("#q-asn").value,
    },
  });
  if (!r.ok) { $("#search-status").textContent = "Search failed."; return; }
  pollJob(r.data.job_id, (job) => {
    if (job.status === "done") {
      const recs = (job.result && job.result.records) || [];
      $("#search-status").textContent = `${recs.length} match(es).`;
      renderTable($("#search-table"),
        [
          { key: "prefix", label: "CIDR" }, { key: "asn", label: "ASN" },
          { key: "organization", label: "ORG" }, { key: "country", label: "CC" },
          { key: "provider", label: "PROVIDER" },
        ], recs);
    } else if (job.status === "error") {
      $("#search-status").textContent = "Search error: " + job.error;
    }
  });
});

function pollJob(jobId, onUpdate, tries = 0) {
  api(`/api/jobs?id=${encodeURIComponent(jobId)}`).then((r) => {
    if (!r.ok) { onUpdate({ status: "error", error: "job lost" }); return; }
    const job = r.data;
    if (job.status === "done" || job.status === "error") { onUpdate(job); return; }
    if (tries > 1200) { onUpdate({ status: "error", error: "timeout" }); return; }
    setTimeout(() => pollJob(jobId, onUpdate, tries + 1), 500);
  });
}

// ---- live scan -----------------------------------------------------------
let lastScanId = null;
$("#scan-btn").addEventListener("click", async () => {
  $("#scan-status").textContent = "Scanning…";
  $("#dl-whitelist").disabled = true;
  const r = await api("/api/scan", {
    method: "POST", body: { category: $("#scan-category").value },
  });
  if (!r.ok) { $("#scan-status").textContent = "Scan failed."; return; }
  pollJob(r.data.job_id, (job) => {
    if (job.status !== "done") {
      $("#scan-status").textContent = "Scan error: " + (job.error || "");
      return;
    }
    const res = job.result || {};
    lastScanId = res.scan_id;
    $("#scan-status").textContent = `Scan #${res.scan_id} complete.`;
    renderScanCounts(res.counts || {});
    $("#dl-whitelist").disabled = false;
    renderScanRows(res.results || []);
  });
});
$("#whitelist-only").addEventListener("change", () => {
  if (lastScanRows) renderScanRows(lastScanRows);
});
let lastScanRows = null;
function renderScanRows(rows) {
  lastScanRows = rows;
  const only = $("#whitelist-only").checked;
  const shown = only ? rows.filter((r) => r.combined === "INTERNATIONAL") : rows;
  renderTable($("#scan-table"),
    [
      { key: "host", label: "HOST" }, { key: "health", label: "HEALTH", badge: true },
      { key: "avg_ms", label: "AVG(ms)" },
      { key: "abroad_label", label: "ABROAD" },
      { key: "combined", label: "WHITELIST", badge: true },
      { key: "ports_label", label: "PORTS" },
    ],
    shown.map((r) => ({
      ...r,
      abroad_label: r.abroad_status === "unavailable"
        ? "unavailable"
        : (r.abroad_reachable === null || r.abroad_reachable === undefined
          ? "not checked"
          : (r.abroad_reachable ? "OK" : "FAIL") + ` (${r.abroad_nodes_ok||0}/${r.abroad_nodes_total||0})`),
      ports_label: (r.open_ports && r.open_ports.length) ? r.open_ports.join(",") : "-",
    })));
}
function renderScanCounts(counts) {
  const wrap = $("#scan-counts");
  wrap.innerHTML = "";
  for (const key of ["INTERNATIONAL", "IRAN_ONLY", "ABROAD_ONLY", "UNREACHABLE"])
    wrap.append(badge(`${key}: ${counts[key] || 0}`));
}
$("#dl-whitelist").addEventListener("click", () => {
  if (lastScanId != null) window.location = `/api/export?kind=whitelist&scan=${lastScanId}`;
});

// ---- history + trend chart ----------------------------------------------
async function loadHistory() {
  const r = await api("/api/history?limit=50");
  if (!r.ok) return;
  const scans = r.data.scans || [];
  const table = $("#history-table");
  renderTable(table,
    [
      { key: "id", label: "ID" }, { key: "started_at", label: "WHEN" },
      { key: "scope", label: "SCOPE" }, { key: "total", label: "TOTAL" },
      { key: "good", label: "GOOD" }, { key: "medium", label: "MED" }, { key: "bad", label: "BAD" },
    ], scans);
  // Row click -> detail.
  for (const tr of table.querySelectorAll("tbody tr")) {
    tr.style.cursor = "pointer";
    tr.addEventListener("click", () => loadScanDetail(tr.firstChild.textContent));
  }
  drawTrend(scans.slice().reverse());
}

async function loadScanDetail(scanId) {
  const r = await api(`/api/scan-results?id=${encodeURIComponent(scanId)}`);
  if (!r.ok) return;
  $("#history-detail-title").classList.remove("hidden");
  $("#history-detail-controls").classList.remove("hidden");
  $("#dl-csv").onclick = () => window.location = `/api/export?kind=csv&scan=${scanId}`;
  $("#dl-json").onclick = () => window.location = `/api/export?kind=json&scan=${scanId}`;
  renderTable($("#history-detail-table"),
    [
      { key: "host", label: "HOST" }, { key: "health", label: "HEALTH", badge: true },
      { key: "avg_ms", label: "AVG(ms)" }, { key: "combined", label: "WHITELIST", badge: true },
    ], r.data.results || []);
}

function drawTrend(scans) {
  const cv = $("#trend-chart");
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!scans.length) return;
  // Approximate INTERNATIONAL by GOOD count here (history summary columns).
  const series = scans.map((s) => s.good);
  const maxV = Math.max(1, ...scans.map((s) => s.total));
  const pad = 24, w = cv.width - pad * 2, h = cv.height - pad * 2;
  ctx.strokeStyle = "#2a323d";
  ctx.beginPath(); ctx.moveTo(pad, pad); ctx.lineTo(pad, pad + h); ctx.lineTo(pad + w, pad + h); ctx.stroke();
  const step = scans.length > 1 ? w / (scans.length - 1) : 0;
  const plot = (vals, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    vals.forEach((v, i) => {
      const x = pad + step * i, y = pad + h - (v / maxV) * h;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  plot(series, "#3fb950");
  plot(scans.map((s) => s.total), "#3fb6f0");
}

// ---- settings + credentials ---------------------------------------------
async function loadSettings() {
  const r = await api("/api/settings");
  if (!r.ok) return;
  const form = $("#settings-form");
  form.innerHTML = "";
  for (const [key, val] of Object.entries(r.data.settings)) {
    const input = el("input", { id: `set-${key}`, value: String(val) });
    input.dataset.key = key;
    form.append(el("label", {}, key, input));
  }
}
$("#save-settings").addEventListener("click", async () => {
  const body = {};
  for (const input of document.querySelectorAll("#settings-form input")) {
    const raw = input.value.trim();
    if (raw === "true" || raw === "false") body[input.dataset.key] = raw === "true";
    else if (raw !== "" && !isNaN(Number(raw))) body[input.dataset.key] = Number(raw);
    else body[input.dataset.key] = raw;
  }
  const r = await api("/api/settings", { method: "POST", body });
  $("#settings-status").textContent = r.ok ? "Saved." : "Save failed.";
});

$("#save-creds").addEventListener("click", async () => {
  const r = await api("/api/change-credentials", {
    method: "POST",
    body: {
      current_password: $("#cur-pass").value,
      new_username: $("#new-user").value,
      new_password: $("#new-pass").value,
    },
  });
  if (r.ok) {
    $("#cred-status").textContent = "Updated — please sign in again.";
    setTimeout(showLogin, 1200);
  } else {
    $("#cred-status").textContent = (r.data && r.data.error) || "update failed";
  }
});

// ---- provider summary widget --------------------------------------------
async function loadSummary() {
  const r = await api("/api/summary");
  if (!r.ok) return;
  const panel = $("#summary-panel");
  panel.innerHTML = "";
  const providers = (r.data.providers || []).filter((p) => p.hosts > 0);
  if (!providers.length) {
    panel.append(el("p", { class: "muted" }, "No scans yet — run a Live Scan to populate this."));
    return;
  }
  for (const p of providers) {
    const pct = Math.round(p.fraction * 100);
    const card = el("div", { class: "card" },
      el("div", { class: "card-name" }, p.name),
      el("div", { class: "card-sub" }, `${p.country || "?"} · ${p.category}`),
      el("div", { class: "meter" }, el("span", { style: `width:${pct}%` })),
      el("div", { class: "frac" }, `${p.international}/${p.hosts} international (${pct}%)`),
    );
    panel.append(card);
  }
}

window.addEventListener("hashchange", () => navigate(location.hash.replace("#", "") || "home"));
boot();
