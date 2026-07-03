const API = "";
let currentView = "materials";
let itemsCache = [];
let statsCache = {};

// --- Utils ---
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: {"Content-Type": "application/json"},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({detail: res.statusText}));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

function toast(msg, type = "success") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast show " + type;
  setTimeout(() => el.classList.remove("show"), 3000);
}

function fmtDate(d) {
  if (!d) return "";
  return d.slice(0, 10);
}

function escapeHtml(s) {
  if (!s) return "";
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// --- Tab switching ---
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    tab.classList.add("active");
    const view = tab.dataset.view;
    document.getElementById("view-" + view).classList.add("active");
    currentView = view;
    if (view === "materials") loadMaterials();
    if (view === "pipeline") loadPipeline();
    if (view === "thinking") loadThinking();
    if (view === "drafts") loadDrafts();
  });
});

// --- Stats ---
async function loadStats() {
  try {
    statsCache = await api("/api/stats");
    const nav = document.getElementById("nav-stats");
    const s = statsCache;
    nav.innerHTML = `
      <div class="nav-stat"><span class="count">${s.total_items}</span> items</div>
      <div class="nav-stat"><span class="count">${(s.items||{}).seed||0}</span> seeds</div>
      <div class="nav-stat"><span class="count">${(s.items||{}).asset||0}</span> assets</div>
      <div class="nav-stat"><span class="count">${s.daily_thinking_count||0}</span> days</div>
      <div class="nav-stat"><span class="count">${s.draft_count||0}</span> drafts</div>
    `;
  } catch(e) { console.error("stats:", e); }
}

// --- Material Pool ---
async function loadMaterials() {
  const source = document.getElementById("filter-source").value;
  const search = document.getElementById("filter-search").value.toLowerCase();
  let query = "?limit=500";
  if (source) query += "&source=" + source;
  try {
    const data = await api("/api/items" + query);
    itemsCache = data.items;
    renderMaterials(search);
  } catch(e) { toast("Failed to load items: " + e.message, "error"); }
}

function renderMaterials(search) {
  const board = document.getElementById("materials-board");
  const groups = {seed: [], asset: [], archive: []};
  for (const item of itemsCache) {
    if (search && !item.title?.toLowerCase().includes(search) &&
        !item.trigger?.toLowerCase().includes(search) &&
        !item.category?.toLowerCase().includes(search)) continue;
    const v = item.verdict in groups ? item.verdict : "archive";
    groups[v].push(item);
  }
  const cols = [
    {key: "seed", label: "Seed", cls: "col-seed"},
    {key: "asset", label: "Asset", cls: "col-asset"},
    {key: "archive", label: "Archive", cls: "col-archive"},
  ];
  board.innerHTML = cols.map(col => {
    const items = groups[col.key] || [];
    return `
      <div class="kanban-column ${col.cls}">
        <div class="kanban-column-header">
          <span>${col.label}</span>
          <span class="col-badge">${items.length}</span>
        </div>
        <div class="kanban-cards">
          ${items.map(item => renderCard(item)).join("")}
        </div>
      </div>
    `;
  }).join("");
}

function renderCard(item) {
  const srcClass = "tag-source-" + (item.source || "unknown");
  const prioTag = item.priority === "high" ? '<span class="tag tag-priority-high">HIGH</span>' : "";
  return `
    <div class="card" onclick="showItemDetail('${item.unit_id}')">
      <div class="card-title">${escapeHtml(item.title || "Untitled")}</div>
      <div class="card-meta">
        <span class="tag ${srcClass}">${item.source || "?"}</span>
        ${item.category ? `<span class="tag">${escapeHtml(item.category)}</span>` : ""}
        ${prioTag}
      </div>
      ${item.trigger ? `<div class="card-trigger">${escapeHtml(item.trigger)}</div>` : ""}
    </div>
  `;
}

window.showItemDetail = async function(unitId) {
  try {
    const data = await api("/api/items/" + encodeURIComponent(unitId));
    const item = data.item;
    const events = data.events || [];
    const eventList = events.map(e => `<div class="run-time">${e.event_type} - ${fmtDate(e.created_at)}</div>`).join("");
    const win = window.open("", "_blank", "width=600,height=700");
    win.document.write(`
      <html><head><title>${escapeHtml(item.title)}</title>
      <style>
        body{font-family:system-ui;padding:24px;max-width:600px;margin:0 auto;color:#2c2825;}
        h1{font-size:18px;margin-bottom:8px;}
        .meta{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px;}
        .tag{font-size:11px;padding:2px 8px;border-radius:4px;background:#f0efec;color:#8a8378;}
        .field{margin-bottom:12px;}
        .label{font-size:11px;color:#8a8378;text-transform:uppercase;font-weight:600;}
        .value{font-size:14px;}
        .events{margin-top:16px;border-top:1px solid #e0ddd7;padding-top:12px;}
      </style></head><body>
      <h1>${escapeHtml(item.title)}</h1>
      <div class="meta">
        <span class="tag">${item.source}</span>
        <span class="tag">${item.verdict}</span>
        ${item.category ? `<span class="tag">${escapeHtml(item.category)}</span>` : ""}
        ${item.priority === "high" ? `<span class="tag">HIGH</span>` : ""}
      </div>
      <div class="field"><div class="label">Trigger</div><div class="value">${escapeHtml(item.trigger || "")}</div></div>
      <div class="field"><div class="label">Reason</div><div class="value">${escapeHtml(item.reason || "")}</div></div>
      <div class="field"><div class="label">Confidence</div><div class="value">${escapeHtml(item.confidence || "")}</div></div>
      <div class="field"><div class="label">Source Path</div><div class="value" style="font-family:monospace;font-size:12px;">${escapeHtml(item.source_path || "")}</div></div>
      <div class="events"><div class="label">Events (${events.length})</div>${eventList}</div>
      </body></html>
    `);
    win.document.close();
  } catch(e) { toast("Failed: " + e.message, "error"); }
};

document.getElementById("filter-source").addEventListener("change", loadMaterials);
document.getElementById("filter-search").addEventListener("input", () => {
  const search = document.getElementById("filter-search").value.toLowerCase();
  renderMaterials(search);
});

// --- Pipeline ---
async function loadPipeline() {
  try {
    const [overview, stagesData] = await Promise.all([
      api("/api/pipeline/overview"),
      api("/api/pipeline/stages"),
    ]);
    const board = document.getElementById("pipeline-board");
    const stages = stagesData.stages;
    board.innerHTML = stages.map(stage => {
      const data = overview[stage] || {total: 0, running: 0, done: 0, failed: 0, recent: []};
      return `
        <div class="pipeline-stage">
          <div class="pipeline-stage-header">
            <span>${stage}</span>
            <span class="stage-counts">${data.done}/${data.total}</span>
          </div>
          <div class="pipeline-stage-body">
            ${(data.recent || []).map(r => renderRunCard(r)).join("") || '<div style="color:#8a8378;font-size:12px;padding:8px;">No runs</div>'}
          </div>
        </div>
      `;
    }).join("");
  } catch(e) { toast("Failed to load pipeline: " + e.message, "error"); }
}

function renderRunCard(r) {
  const cls = r.status || "pending";
  const err = r.error ? `<div class="run-error">${escapeHtml(r.error.slice(0,80))}</div>` : "";
  const itemId = r.item_id || r.thinking_date || "";
  return `
    <div class="run-card ${cls}">
      <div class="run-id">${r.id}</div>
      <div class="run-time">${fmtDate(r.started_at)} ${itemId ? "- " + escapeHtml(itemId) : ""}</div>
      ${err}
    </div>
  `;
}

document.getElementById("btn-run-incremental").addEventListener("click", async () => {
  toast("Running incremental pipeline...");
  try {
    const r = await api("/api/pipeline/run", {method: "POST", body: {action: "incremental"}});
    toast("Incremental done: " + JSON.stringify(r.result.classify || r.result));
    loadPipeline(); loadStats();
  } catch(e) { toast("Failed: " + e.message, "error"); }
});
document.getElementById("btn-run-classify").addEventListener("click", async () => {
  toast("Classifying pending items...");
  try {
    const r = await api("/api/pipeline/run", {method: "POST", body: {action: "classify"}});
    toast("Classified: " + JSON.stringify(r.result));
    loadPipeline(); loadStats();
  } catch(e) { toast("Failed: " + e.message, "error"); }
});
document.getElementById("btn-run-pool").addEventListener("click", async () => {
  toast("Pooling items...");
  try {
    const r = await api("/api/pipeline/run", {method: "POST", body: {action: "pool"}});
    toast("Pooled: " + JSON.stringify(r.result));
    loadPipeline(); loadStats();
  } catch(e) { toast("Failed: " + e.message, "error"); }
});

// --- Daily Thinking ---
async function loadThinking() {
  const today = new Date().toISOString().slice(0, 10);
  const dateInput = document.getElementById("thinking-date");
  if (!dateInput.value) dateInput.value = today;
  await loadThinkingForDate(dateInput.value);
}

async function loadThinkingForDate(targetDate) {
  try {
    const data = await api("/api/daily-thinking/" + targetDate);
    const entry = data.entry;
    const seeds = data.seeds || [];
    document.getElementById("free-write").value = entry.free_write || "";
    const container = document.getElementById("thinking-seeds");
    container.innerHTML = seeds.map(s => `
      <div class="seed-item">
        <div class="seed-item-header">
          <span class="tag tag-source-${s.source}">${s.source}</span>
          ${s.priority === "high" ? '<span class="tag tag-priority-high">HIGH</span>' : ""}
          <span class="seed-item-title">${escapeHtml(s.title || s.unit_id)}</span>
        </div>
        ${s.trigger ? `<div class="seed-item-trigger">${escapeHtml(s.trigger)}</div>` : ""}
        ${s.reason ? `<div class="seed-item-trigger">Reason: ${escapeHtml(s.reason)}</div>` : ""}
      </div>
    `).join("") || "<div style='color:#8a8378;padding:16px;'>No thinking session for this date. Click Generate.</div>";
  } catch(e) {
    document.getElementById("thinking-seeds").innerHTML = "<div style='color:#8a8378;padding:16px;'>No thinking session for this date. Click Generate.</div>";
    document.getElementById("free-write").value = "";
  }
}

document.getElementById("thinking-date").addEventListener("change", (e) => loadThinkingForDate(e.target.value));
document.getElementById("btn-generate-thinking").addEventListener("click", async () => {
  const d = document.getElementById("thinking-date").value;
  toast("Generating daily thinking...");
  try {
    const r = await api("/api/daily-thinking/generate", {method: "POST", body: {date: d, n_seeds: 5}});
    if (r.ok) { toast("Generated: " + r.seeds + " seeds"); loadThinkingForDate(d); loadStats(); }
    else toast("Failed: " + r.error, "error");
  } catch(e) { toast("Failed: " + e.message, "error"); }
});
document.getElementById("btn-save-freewrite").addEventListener("click", async () => {
  const d = document.getElementById("thinking-date").value;
  const text = document.getElementById("free-write").value;
  try {
    await api("/api/daily-thinking/" + d + "/free-write", {method: "PATCH", body: {free_write: text}});
    toast("Saved " + text.length + " chars");
  } catch(e) { toast("Failed: " + e.message, "error"); }
});

// --- Drafts ---
async function loadDrafts() {
  const today = new Date().toISOString().slice(0, 10);
  const dateInput = document.getElementById("draft-date");
  if (!dateInput.value) dateInput.value = today;
  await loadDraftsForDate(today);
}

async function loadDraftsForDate(targetDate) {
  try {
    const data = await api("/api/drafts?date=" + targetDate);
    const container = document.getElementById("drafts-list");
    const drafts = data.drafts || [];
    container.innerHTML = drafts.map(d => `
      <div class="draft-card">
        <div class="draft-angle">${escapeHtml(d.angle_id || "?")} - ${escapeHtml(d.angle_name || "")}</div>
        <div class="draft-headline">${escapeHtml(d.headline || "")}</div>
        <div class="draft-body">${escapeHtml((d.body || "").slice(0, 200))}...</div>
        <div class="draft-actions-bar">
          <button class="btn btn-sm" onclick="updateDraftStatus('${d.id}', 'selected')">Select</button>
          <button class="btn btn-sm" onclick="updateDraftStatus('${d.id}', 'dismissed')">Dismiss</button>
        </div>
      </div>
    `).join("") || "<div style='color:#8a8378;padding:16px;'>No drafts. Click Generate.</div>";
  } catch(e) { toast("Failed: " + e.message, "error"); }
}

window.updateDraftStatus = async function(draftId, status) {
  try {
    await api("/api/drafts/" + draftId + "/status", {method: "PATCH", body: {status}});
    toast("Draft " + status);
    const d = document.getElementById("draft-date").value;
    loadDraftsForDate(d);
  } catch(e) { toast("Failed: " + e.message, "error"); }
};

document.getElementById("btn-generate-drafts").addEventListener("click", async () => {
  const d = document.getElementById("draft-date").value;
  toast("Generating drafts (may take 30-60s)...");
  try {
    const r = await api("/api/drafts/generate", {method: "POST", body: {date: d, allow_empty: false}});
    if (r.ok) { toast("Generated " + r.drafts + " drafts (" + r.elapsed + "s)"); loadDraftsForDate(d); loadStats(); }
    else toast("Failed: " + r.error, "error");
  } catch(e) { toast("Failed: " + e.message, "error"); }
});

// --- Init ---
loadStats();
loadMaterials();
