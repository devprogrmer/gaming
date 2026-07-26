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
  setConnected(true);
  return { ok: res.ok, status: res.status, data };
}

function badge(label) {
  const cls = label && label !== "not checked" ? label : "none";
  return el("span", { class: `badge ${cls}` }, label || "-");
}

// ---- shared UI states ----------------------------------------------------
// Every list/table in the app routes its empty, busy, and error states through
// these three helpers, so a blank result never looks like a broken page and an
// error is never a raw string dumped into the DOM.
function emptyState(title, hint, icon = "( )") {
  return el("div", { class: "empty-state" },
    el("div", { class: "empty-icon" }, icon),
    el("div", { class: "empty-title" }, title),
    el("div", { class: "empty-hint" }, hint || ""),
  );
}

// Render a placeholder in place of a table. The node is inserted into the
// table's wrapper, never into the <table> itself -- a <div> inside <table> is
// invalid markup that browsers hoist out, which loses the styling.
function showEmpty(tableSel, title, hint, icon) {
  const table = $(tableSel);
  if (!table) return;
  table.innerHTML = "";
  const wrap = table.closest(".table-wrap") || table.parentNode;
  // Drop any placeholder from a previous render before adding this one.
  for (const old of wrap.querySelectorAll(":scope > .empty-state")) old.remove();
  wrap.append(emptyState(title, hint, icon));
}

// Clear a previous placeholder before rendering real rows into a table.
function clearEmpty(tableSel) {
  const table = $(tableSel);
  if (!table) return;
  const wrap = table.closest(".table-wrap") || table.parentNode;
  for (const old of wrap.querySelectorAll(":scope > .empty-state")) old.remove();
}

function banner(kind, title, detail) {
  const icons = { error: "!", warn: "!", info: "i" };
  return el("div", { class: `banner ${kind}` },
    el("span", { class: "banner-icon" }, icons[kind] || "i"),
    el("div", { class: "banner-body" },
      el("div", { class: "banner-title" }, title),
      ...(detail ? [el("div", { class: "banner-detail" }, detail)] : []),
    ),
  );
}

// A status line that can show a spinner while work is in flight.
function setStatus(sel, text, { busy = false } = {}) {
  const node = $(sel);
  if (!node) return;
  node.innerHTML = "";
  if (busy) node.append(el("span", { class: "spinner" }));
  node.append(document.createTextNode(text));
}

function setConnected(ok, label) {
  const dot = $("#conn-dot"), txt = $("#conn-text");
  if (!dot || !txt) return;
  dot.className = "conn-dot" + (ok ? (label === "busy" ? " busy" : "") : " offline");
  txt.textContent = !ok ? "connection lost" : (label === "busy" ? "working…" : "connected");
}

// Determinate when the job reports a fraction, indeterminate until it does.
function setProgress(sel, fraction) {
  const bar = $(sel);
  if (!bar) return;
  if (fraction === null || fraction === undefined) {
    bar.classList.remove("hidden");
    bar.classList.add("indeterminate");
    bar.firstElementChild.style.width = "";
    return;
  }
  bar.classList.remove("hidden", "indeterminate");
  bar.firstElementChild.style.width = `${Math.round(fraction * 100)}%`;
}
function hideProgress(sel) {
  const bar = $(sel);
  if (bar) bar.classList.add("hidden");
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
const VIEW_TITLES = {
  home: "Overview", search: "Search", scan: "Live Scan",
  history: "History", settings: "Settings",
};

function navigate(view) {
  for (const link of document.querySelectorAll(".nav-link"))
    link.classList.toggle("active", link.dataset.view === view);
  for (const sec of document.querySelectorAll(".view")) sec.classList.add("hidden");
  const target = $(`#view-${view}`);
  if (target) target.classList.remove("hidden");
  const title = $("#page-title");
  if (title) title.textContent = VIEW_TITLES[view] || "Overview";
  if (view === "history") loadHistory();
  if (view === "settings") loadSettings();
  if (view === "home") loadSummary();
}
for (const link of document.querySelectorAll(".nav-link"))
  link.addEventListener("click", () => navigate(link.dataset.view));

// ---- generic sortable table ---------------------------------------------
// Columns may declare `num: true` to be right-aligned with tabular figures,
// `badge: true` to render as a status pill, or `action` for a control.
function renderTable(tableEl, columns, rows) {
  tableEl.innerHTML = "";
  const thead = el("thead");
  const htr = el("tr");
  const sort = tableEl._sort || {};
  columns.forEach((col, i) => {
    const th = el("th", {}, col.label);
    if (col.num) th.className = "num";
    if (sort.key === col.key) {
      th.classList.add(sort.dir === 1 ? "sorted-asc" : "sorted-desc");
    }
    if (col.label) th.append(el("span", { class: "sort-caret" }, "▲"));
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
      if (col.num) td.className = "num";
      if (col.action) td.append(col.action(row));
      else if (col.badge) td.append(badge(val));
      else td.textContent = val === null || val === undefined ? "-" : String(val);
      tr.append(td);
    }
    tbody.append(tr);
  }
  tableEl.append(thead, tbody);
}

function sortBy(tableEl, columns, rows, i) {
  const key = columns[i].key;
  const prev = tableEl._sort || {};
  const dir = prev.key === key && prev.dir === 1 ? -1 : 1;
  tableEl._sort = { key, dir };
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
const SEARCH_COLUMNS = [
  { key: "prefix", label: "CIDR" }, { key: "asn", label: "ASN", num: true },
  { key: "organization", label: "ORG" }, { key: "country", label: "CC" },
  { key: "provider", label: "PROVIDER" },
];

$("#search-btn").addEventListener("click", async () => {
  setStatus("#search-status", "Searching…", { busy: true });
  setConnected(true, "busy");
  $("#search-table").innerHTML = "";
  const r = await api("/api/search", {
    method: "POST",
    body: {
      query: $("#q-query").value, provider: $("#q-provider").value,
      country: $("#q-country").value, asn: $("#q-asn").value,
    },
  });
  if (!r.ok) {
    setStatus("#search-status", "");
    setConnected(true);
    $("#search-status").append(
      banner("error", "Search could not be started",
        (r.data && r.data.error) || `HTTP ${r.status}`));
    return;
  }
  pollJob(r.data.job_id, (job) => {
    if (job.status === "done") {
      const recs = (job.result && job.result.records) || [];
      setConnected(true);
      setStatus("#search-status", recs.length
        ? `${recs.length} match${recs.length === 1 ? "" : "es"}.`
        : "");
      if (!recs.length) {
        $("#search-status").innerHTML = "";
        showEmpty("#search-table", "No matching ranges",
          "Try a broader query — a leading octet like “85”, or clear the provider/country filters.");
        return;
      }
      renderTable($("#search-table"), SEARCH_COLUMNS, recs);
      clearEmpty("#search-table");
    } else if (job.status === "error") {
      setConnected(true);
      setStatus("#search-status", "");
      $("#search-status").append(
        banner("error", "Search failed", job.error || ""));
    }
  });
});

function pollJob(jobId, onUpdate, tries = 0) {
  api(`/api/jobs?id=${encodeURIComponent(jobId)}`).then((r) => {
    if (!r.ok) { onUpdate({ status: "error", error: "job lost" }); return; }
    const job = r.data;
    if (job.status === "done" || job.status === "error" || job.status === "cancelled") {
      onUpdate(job);
      return;
    }
    onUpdate(job); // interim tick, so progress can render as it arrives
    if (tries > 1200) { onUpdate({ status: "error", error: "timeout" }); return; }
    setTimeout(() => pollJob(jobId, onUpdate, tries + 1), 500);
  }).catch(() => {
    setConnected(false);
    onUpdate({ status: "error", error: "connection lost" });
  });
}

// ---- live scan -----------------------------------------------------------
let lastScanId = null;
let lastScanMode = "combined";

$("#scan-btn").addEventListener("click", async () => {
  const mode = (document.querySelector('input[name="scan-mode"]:checked') || {}).value || "combined";
  lastScanMode = mode;
  setStatus("#scan-status", mode === "sequential"
    ? "Scanning one CIDR at a time…" : "Scanning…", { busy: true });
  setConnected(true, "busy");
  setProgress("#scan-progress", null);
  $("#dl-whitelist").disabled = true;
  $("#scan-counts").innerHTML = "";
  $("#scan-table").innerHTML = "";
  $("#scan-table-wrap").classList.toggle("hidden", mode === "sequential");
  $("#scan-sequential-wrap").classList.toggle("hidden", mode !== "sequential");
  $("#scan-sequential-wrap").innerHTML = "";

  const r = await api("/api/scan", {
    method: "POST", body: { category: $("#scan-category").value, mode },
  });
  if (!r.ok) {
    hideProgress("#scan-progress");
    setConnected(true);
    setStatus("#scan-status", "");
    $("#scan-status").append(
      banner("error", "Scan could not be started",
        (r.data && r.data.error) || `HTTP ${r.status}`));
    return;
  }

  pollJob(r.data.job_id, (job) => {
    if (job.status === "error") {
      hideProgress("#scan-progress");
      setConnected(true);
      setStatus("#scan-status", "");
      $("#scan-status").append(banner("error", "Scan failed", job.error || ""));
      return;
    }
    if (job.status === "cancelled") {
      hideProgress("#scan-progress");
      setConnected(true);
      setStatus("#scan-status", "");
      $("#scan-status").append(banner("warn", "Scan stopped",
        "The panel was shut down before this scan finished. Partial results were saved."));
      return;
    }

    const res = job.result || {};
    if (typeof job.progress === "number" && job.progress > 0) {
      setProgress("#scan-progress", job.progress);
    }

    if (res.mode === "sequential") {
      const done = job.status === "done";
      setStatus("#scan-status",
        done ? `Scan complete — ${res.cidrs_done}/${res.cidrs_total} CIDRs.`
             : `Scanning… ${res.cidrs_done}/${res.cidrs_total} CIDRs`,
        { busy: !done });
      renderSequentialScan(res);
      if (done) {
        hideProgress("#scan-progress");
        setConnected(true);
        lastScanId = res.scan_id;
        renderScanCounts(res.counts || {});
        $("#dl-whitelist").disabled = res.scan_id == null;
      }
    } else if (job.status === "done") {
      hideProgress("#scan-progress");
      setConnected(true);
      lastScanId = res.scan_id;
      setStatus("#scan-status", `Scan #${res.scan_id} complete.`);
      renderScanCounts(res.counts || {});
      $("#dl-whitelist").disabled = false;
      renderScanRows(res.results || []);
      if (res.location_unverified && res.location_unverified.length) {
        $("#scan-status").append(banner("warn",
          "Some ranges were excluded as not verified in Iran",
          res.location_unverified.join(", ")));
      }
    }
  });
});

$("#whitelist-only").addEventListener("change", () => {
  if (lastScanMode === "sequential" && lastSequentialResult) renderSequentialScan(lastSequentialResult);
  else if (lastScanRows) renderScanRows(lastScanRows);
});

// Shared column set for any per-host results table (combined or per-CIDR).
function hostColumns() {
  return [
    { key: "host", label: "HOST" },
    { key: "health", label: "HEALTH", badge: true },
    { key: "avg_ms", label: "AVG(ms)", num: true },
    { key: "abroad_label", label: "ABROAD" },
    { key: "combined", label: "WHITELIST", badge: true },
    { key: "ports_label", label: "PORTS" },
    { key: "_test", label: "", action: (row) => {
        const btn = el("button", { class: "row-btn" }, "Test path to…");
        btn.addEventListener("click", (ev) => { ev.stopPropagation(); startProximityPing(row.host); });
        return btn;
      } },
  ];
}

function decorateRows(rows) {
  return rows.map((r) => ({
    ...r,
    abroad_label: r.abroad_status === "unavailable"
      ? "unavailable"
      : (r.abroad_reachable === null || r.abroad_reachable === undefined
        ? "not checked"
        : (r.abroad_reachable ? "OK" : "FAIL") + ` (${r.abroad_nodes_ok||0}/${r.abroad_nodes_total||0})`),
    ports_label: (r.open_ports && r.open_ports.length) ? r.open_ports.join(",") : "-",
  }));
}

let lastScanRows = null;
function renderScanRows(rows) {
  lastScanRows = rows;
  const only = $("#whitelist-only").checked;
  const shown = only ? rows.filter((r) => r.combined === "INTERNATIONAL") : rows;
  if (!shown.length) {
    showEmpty("#scan-table",
      only ? "No whitelist matches" : "No results",
      only
        ? "No host in this scan was reachable internationally. Untick “whitelist matches only” to see every probed host."
        : "This scan returned no hosts. Check that the selected category has saved ranges.");
    return;
  }
  clearEmpty("#scan-table");
  renderTable($("#scan-table"), hostColumns(), decorateRows(shown));
}

let lastSequentialResult = null;
function renderSequentialScan(res) {
  lastSequentialResult = res;
  const only = $("#whitelist-only").checked;
  const wrap = $("#scan-sequential-wrap");
  wrap.innerHTML = "";
  for (const block of res.per_cidr || []) {
    const section = el("div", { class: "cidr-block" });
    section.append(el("div", { class: "cidr-block-head" },
      el("h3", {}, block.cidr)));
    if (block.error) {
      section.append(banner("error", "This range could not be scanned", block.error));
    } else {
      const countsEl = el("div", { class: "counts" });
      for (const [k, v] of Object.entries(block.counts || {})) countsEl.append(badge(`${k}: ${v}`));
      section.append(countsEl);
      const rows = only
        ? (block.results || []).filter((r) => r.combined === "INTERNATIONAL")
        : (block.results || []);
      if (!rows.length) {
        section.append(emptyState(
          only ? "No whitelist matches in this range" : "No hosts returned", ""));
      } else {
        const table = el("table");
        section.append(el("div", { class: "table-wrap" }, table));
        renderTable(table, hostColumns(), decorateRows(rows));
      }
    }
    wrap.append(section);
  }
  if (res.location_unverified && res.location_unverified.length) {
    wrap.append(banner("warn", "Excluded — not verified as located in Iran",
      res.location_unverified.join(", ")));
  }
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

// ---- proximity ping ("Test path to…") -------------------------------------
// Explicitly separate from the Iran/abroad reachability columns above: this
// measures an approximate path from the *nearest available RIPE Atlas probe*
// to the source IP's network, never the source IP's own ping.
async function startProximityPing(sourceIp) {
  const dest = window.prompt(`Test path from near ${sourceIp} to which destination IP?`);
  if (!dest) return;
  renderProximityResult(sourceIp, dest, { status: "pending" });
  const r = await api("/api/proximity-ping", {
    method: "POST", body: { source_ip: sourceIp, destination_ip: dest },
  });
  if (!r.ok) {
    renderProximityResult(sourceIp, dest, {
      status: "unavailable", note: (r.data && r.data.error) || "could not start the test",
    });
    return;
  }
  pollJob(r.data.job_id, (job) => {
    if (job.status === "error") {
      renderProximityResult(sourceIp, dest, { status: "unavailable", note: job.error || "" });
      return;
    }
    if (job.status === "done") renderProximityResult(sourceIp, dest, job.result || {});
  });
}

function renderProximityResult(sourceIp, dest, res) {
  const box = $("#proximity-result");
  box.classList.remove("hidden");
  box.innerHTML = "";
  box.append(el("h3", { class: "panel-title" }, `Path test — near ${sourceIp} → ${dest}`));

  if (res.status === "pending") {
    const line = el("p", { class: "status-line muted" });
    line.append(el("span", { class: "spinner" }),
      document.createTextNode("Measuring… this can take up to a minute."));
    box.append(line);
    box.append(el("div", { class: "progress indeterminate" }, el("span")));
  } else if (res.status === "ok") {
    const reachTxt = res.reachable === true ? "yes" : res.reachable === false ? "no" : "unknown";
    const dl = el("dl", { class: "kv" });
    dl.append(el("dt", {}, "Reachable"), el("dd", {}, reachTxt));
    if (res.avg_ms != null) dl.append(el("dt", {}, "Avg latency"), el("dd", {}, `${res.avg_ms} ms`));
    if (res.probe_id != null)
      dl.append(el("dt", {}, "Probe"), el("dd", {}, `#${res.probe_id} (AS${res.probe_asn || "?"})`));
    box.append(dl);
  } else if (res.status === "no_nearby_probe") {
    box.append(banner("info", "No nearby probe",
      "No RIPE Atlas probe was found in this IP's network, so no approximation is possible."));
  } else {
    box.append(banner("warn", "Measurement unavailable", res.note || ""));
  }

  box.append(el("p", { class: "proximity-note" },
    res.note || "Approximate — measured from the nearest available RIPE Atlas probe to this IP's network, not from the IP itself."));
}

// ---- history + trend chart ----------------------------------------------
async function loadHistory() {
  const r = await api("/api/history?limit=50");
  if (!r.ok) return;
  const scans = r.data.scans || [];
  const table = $("#history-table");
  if (!scans.length) {
    showEmpty("#history-table", "No scans yet",
      "Run a scan from the Live Scan page — completed scans and their trend will appear here.");
    drawTrend([]);
    return;
  }
  clearEmpty("#history-table");
  renderTable(table,
    [
      { key: "id", label: "ID", num: true }, { key: "started_at", label: "WHEN" },
      { key: "scope", label: "SCOPE" }, { key: "total", label: "TOTAL", num: true },
      { key: "good", label: "GOOD", num: true }, { key: "medium", label: "MED", num: true },
      { key: "bad", label: "BAD", num: true },
    ], scans);
  for (const tr of table.querySelectorAll("tbody tr")) {
    tr.classList.add("clickable");
    tr.addEventListener("click", () => loadScanDetail(tr.firstChild.textContent));
  }
  drawTrend(scans.slice().reverse());
}

async function loadScanDetail(scanId) {
  const r = await api(`/api/scan-results?id=${encodeURIComponent(scanId)}`);
  if (!r.ok) return;
  $("#history-detail").classList.remove("hidden");
  $("#history-detail-title").textContent = `Scan #${scanId} detail`;
  $("#dl-csv").onclick = () => window.location = `/api/export?kind=csv&scan=${scanId}`;
  $("#dl-json").onclick = () => window.location = `/api/export?kind=json&scan=${scanId}`;
  const rows = r.data.results || [];
  if (!rows.length) {
    showEmpty("#history-detail-table", "No stored results",
      "This scan completed without recording any per-host results.");
    return;
  }
  clearEmpty("#history-detail-table");
  renderTable($("#history-detail-table"),
    [
      { key: "host", label: "HOST" }, { key: "health", label: "HEALTH", badge: true },
      { key: "avg_ms", label: "AVG(ms)", num: true },
      { key: "combined", label: "WHITELIST", badge: true },
    ], rows);
}

// Reads its palette from the stylesheet's custom properties so the chart can
// never drift from the rest of the theme.
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function drawTrend(scans) {
  const cv = $("#trend-chart");
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!scans.length) {
    ctx.fillStyle = cssVar("--text-dim", "#66717f");
    ctx.font = "13px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No scans yet", cv.width / 2, cv.height / 2);
    ctx.textAlign = "start";
    return;
  }
  // Approximate INTERNATIONAL by GOOD count here (history summary columns).
  const series = scans.map((s) => s.good);
  const maxV = Math.max(1, ...scans.map((s) => s.total));
  const pad = 28, w = cv.width - pad * 2, h = cv.height - pad * 2;

  // Horizontal gridlines give the eye a reference for the two series.
  ctx.strokeStyle = cssVar("--border", "#26303d");
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad + (h / 4) * i;
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(pad + w, y); ctx.stroke();
  }

  const step = scans.length > 1 ? w / (scans.length - 1) : 0;
  const plot = (vals, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath();
    vals.forEach((v, i) => {
      const x = pad + step * i, y = pad + h - (v / maxV) * h;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  plot(scans.map((s) => s.total), cssVar("--accent", "#4cc2ff"));
  plot(series, cssVar("--ok", "#3fd07f"));
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
  const status = $("#settings-status");
  status.innerHTML = "";
  if (r.ok) setStatus("#settings-status", "Saved.");
  else status.append(banner("error", "Could not save settings",
    (r.data && r.data.error) || `HTTP ${r.status}`));
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
  const status = $("#cred-status");
  status.innerHTML = "";
  if (r.ok) {
    setStatus("#cred-status", "Updated — please sign in again.");
    setTimeout(showLogin, 1200);
  } else {
    status.append(banner("error", "Could not update credentials",
      (r.data && r.data.error) || "update failed"));
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
    panel.append(emptyState("No scan data yet",
      "Run a scan from the Live Scan page to populate provider connectivity.", "[ ]"));
    return;
  }
  for (const p of providers) {
    const pct = Math.round(p.fraction * 100);
    // Tint the meter by health so a wall of cards is scannable at a glance.
    const tone = pct >= 66 ? "--ok" : pct >= 33 ? "--warn" : "--danger";
    const card = el("div", { class: "card" },
      el("div", { class: "card-name" }, p.name),
      el("div", { class: "card-sub" }, `${p.country || "?"} · ${p.category}`),
      el("div", { class: "meter" },
        el("span", { style: `width:${pct}%; background: var(${tone})` })),
      el("div", { class: "frac" }, `${p.international}/${p.hosts} international (${pct}%)`),
    );
    panel.append(card);
  }
}

window.addEventListener("hashchange", () => navigate(location.hash.replace("#", "") || "home"));
boot();
