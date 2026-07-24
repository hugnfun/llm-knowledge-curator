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
  const modal = document.getElementById("item-modal");
  const body = document.getElementById("modal-body");
  body.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);">Loading...</div>';
  modal.style.display = "flex";
  try {
    const data = await api("/api/items/" + encodeURIComponent(unitId));
    const item = data.item;
    const events = data.events || [];
    const eventList = events.map(e =>
      `<div class="modal-event">${escapeHtml(e.event_type)} - ${fmtDate(e.created_at)}</div>`
    ).join("");
    body.innerHTML = `
      <h1>${escapeHtml(item.title || "Untitled")}</h1>
      <div class="modal-meta">
        <span class="tag">${item.source || "?"}</span>
        <span class="tag">${item.verdict}</span>
        ${item.category ? `<span class="tag">${escapeHtml(item.category)}</span>` : ""}
        ${item.priority === "high" ? `<span class="tag">HIGH</span>` : ""}
      </div>
      <div class="modal-field"><div class="label">Trigger</div><div class="value">${escapeHtml(item.trigger || "")}</div></div>
      <div class="modal-field"><div class="label">Reason</div><div class="value">${escapeHtml(item.reason || "")}</div></div>
      <div class="modal-field"><div class="label">Confidence</div><div class="value">${escapeHtml(item.confidence || "")}</div></div>
      ${item.summary ? `<div class="modal-field"><div class="label">Summary</div><div class="value">${escapeHtml(item.summary)}</div></div>` : ""}
      ${item.tags ? `<div class="modal-field"><div class="label">Tags</div><div class="value">${(() => { try { return JSON.parse(item.tags).map(t => '<span class="tag">' + escapeHtml(t) + '</span>').join(''); } catch { return escapeHtml(item.tags); } })()}</div></div>` : ""}
      <div class="modal-field"><div class="label">Source Path</div><div class="value" style="font-family:monospace;font-size:12px;word-break:break-all;">${escapeHtml(item.source_path || "")}</div></div>
      ${item.raw_content ? `<div class="modal-field"><div class="label">Raw Text</div><div class="value" style="white-space:pre-wrap;max-height:200px;overflow-y:auto;font-size:13px;">${escapeHtml(item.raw_content)}</div></div>` : ""}
      <div class="modal-actions">
        <label style="font-size:12px;color:var(--text-muted);">Move to:</label>
        <select id="modal-verdict-select">
          <option value="seed" ${item.verdict === "seed" ? "selected" : ""}>Seed</option>
          <option value="asset" ${item.verdict === "asset" ? "selected" : ""}>Asset</option>
          <option value="archive" ${item.verdict === "archive" ? "selected" : ""}>Archive</option>
        </select>
        <button class="btn btn-sm btn-primary" onclick="moveItemVerdict('${item.unit_id}')">Apply</button>
      </div>
      <div class="modal-events">
        <div class="label" style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;margin-bottom:4px;">Events (${events.length})</div>
        ${eventList}
      </div>
    `;
  } catch(e) {
    body.innerHTML = `<div style="color:var(--danger);padding:20px;">Failed: ${escapeHtml(e.message)}</div>`;
  }
};

window.closeItemModal = function() {
  document.getElementById("item-modal").style.display = "none";
};

window.moveItemVerdict = async function(unitId) {
  const verdict = document.getElementById("modal-verdict-select").value;
  try {
    await api("/api/items/" + encodeURIComponent(unitId) + "/verdict", {
      method: "PATCH", body: {verdict, category: "", trigger: "", reason: "manual move", confidence: "", priority: ""}
    });
    toast("Moved to " + verdict);
    closeItemModal();
    loadMaterials(); loadStats();
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
    <div class="run-card ${cls}" onclick="showRunDetail('${r.id}')" style="cursor:pointer">
      <div class="run-id">${r.id}</div>
      <div class="run-time">${fmtDate(r.started_at)} ${itemId ? "- " + escapeHtml(itemId) : ""}</div>
      ${err}
    </div>
  `;
}

window.showRunDetail = async function(runId) {
  const modal = document.getElementById("item-modal");
  const body = document.getElementById("modal-body");
  body.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);">Loading...</div>';
  modal.style.display = "flex";
  try {
    const data = await api("/api/pipeline/runs/" + encodeURIComponent(runId));
    const run = data.run;
    const eventList = (data.events || []).map(event =>
      `<div class="modal-event">${escapeHtml(event.event_type)} - ${fmtDate(event.created_at)}</div>`
    ).join("");
    body.innerHTML = `
      <h1>Run ${run.id}</h1>
      <div class="modal-meta">
        <span class="tag">${run.stage || "?"}</span>
        <span class="tag">${run.status}</span>
        ${run.thinking_date ? `<span class="tag">${run.thinking_date}</span>` : ""}
        ${run.item_id ? `<span class="tag">${escapeHtml(run.item_id)}</span>` : ""}
      </div>
      <div class="modal-field"><div class="label">Stage</div><div class="value">${escapeHtml(run.stage || "")}</div></div>
      <div class="modal-field"><div class="label">Status</div><div class="value">${escapeHtml(run.status || "")}</div></div>
      <div class="modal-field"><div class="label">Started</div><div class="value">${escapeHtml(run.started_at || "")}</div></div>
      <div class="modal-field"><div class="label">Completed</div><div class="value">${escapeHtml(run.completed_at || "")}</div></div>
      <div class="modal-field"><div class="label">Duration</div><div class="value">${run.duration_sec ? run.duration_sec + "s" : "-"}</div></div>
      ${run.error ? `<div class="modal-field"><div class="label">Error</div><div class="value" style="color:var(--danger);">${escapeHtml(run.error)}</div></div>` : ""}
      ${run.artifacts ? `<div class="modal-field"><div class="label">Artifacts</div><div class="value" style="font-family:monospace;font-size:12px;word-break:break-all;">${escapeHtml(run.artifacts)}</div></div>` : ""}
      ${run.item_id ? `<div class="modal-field"><div class="label">Item</div><div class="value" style="font-family:monospace;font-size:12px;">${escapeHtml(run.item_id)}</div></div>` : ""}
      <div class="modal-events">
        <div class="label" style="font-size:11px;color:var(--text-muted);text-transform:uppercase;font-weight:600;margin-bottom:4px;">Events (${(data.events || []).length})</div>
        ${eventList || '<div class="modal-event">No events</div>'}
      </div>
    `;
  } catch(e) {
    body.innerHTML = `<div style="color:var(--danger);padding:20px;">Failed: ${escapeHtml(e.message)}</div>`;
  }
};

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
    // Make seed titles clickable
    container.querySelectorAll(".seed-item").forEach((el, i) => {
      if (seeds[i]) el.style.cursor = "pointer";
      if (seeds[i]) el.addEventListener("click", () => showItemDetail(seeds[i].unit_id));
    });
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
  await loadDraftsForDate(dateInput.value);
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
        <div class="draft-body">${escapeHtml((d.body || d.draft || "").slice(0, 500))}</div>
        ${d.hook ? `<div class="draft-hook">> ${escapeHtml(d.hook)}</div>` : ""}
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
// Modal: close on overlay click / Escape
document.getElementById("item-modal").addEventListener("click", (e) => {
  if (e.target.id === "item-modal") closeItemModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeItemModal(); closeIngestModal(); }
});

// --- Ingest URL ---
const SOURCE_LABEL = {
  wechat: "微信公众号", douyin: "抖音", tiktok: "TikTok（暂不支持采集）",
  xhs: "小红书", generic: "一般网页",
};

function classifyUrlPreview(url) {
  if (!url) return "";
  if (/mp\.weixin\.qq\.com/.test(url)) return "wechat";
  if (/(v\.douyin\.com|www\.douyin\.com|iesdouyin\.com)/.test(url)) return "douyin";
  if (/(www\.tiktok\.com|vm\.tiktok\.com)/.test(url)) return "tiktok";
  if (/(xiaohongshu\.com|xhslink\.com)/.test(url)) return "xhs";
  if (/^https?:\/\//.test(url)) return "generic";
  return "";
}

// Extract the first http(s) URL from arbitrary shared text
// (e.g. Douyin/Xiaohongshu share strings that wrap the link in emojis + notes).
function extractFirstUrl(text) {
  if (!text) return "";
  const m = text.match(/https?:\/\/[^\s<>"'\u4e00-\u9fff]+/);
  return m ? m[0] : "";
}

window.openIngestModal = function() {
  document.getElementById("ingest-url-input").value = "";
  document.getElementById("ingest-hint").textContent = "";
  document.getElementById("ingest-status").textContent = "";
  document.getElementById("ingest-modal").style.display = "flex";
  setTimeout(() => document.getElementById("ingest-url-input").focus(), 50);
};

window.closeIngestModal = function() {
  document.getElementById("ingest-modal").style.display = "none";
};

document.getElementById("btn-ingest-url").addEventListener("click", openIngestModal);

document.getElementById("ingest-modal").addEventListener("click", (e) => {
  if (e.target.id === "ingest-modal") closeIngestModal();
});

document.getElementById("ingest-url-input").addEventListener("input", (e) => {
  const raw = e.target.value.trim();
  const url = extractFirstUrl(raw) || raw;
  const t = classifyUrlPreview(url);
  const hint = document.getElementById("ingest-hint");
  if (t) {
    const extracted = url !== raw ? ` (提取到: ${url})` : "";
    hint.textContent = "识别为: " + (SOURCE_LABEL[t] || t) + extracted;
  } else {
    hint.textContent = raw ? "未识别到 http(s) 链接" : "";
  }
});

document.getElementById("btn-ingest-submit").addEventListener("click", async () => {
  const raw = document.getElementById("ingest-url-input").value.trim();
  const url = extractFirstUrl(raw) || raw;
  const auto = document.getElementById("ingest-auto-classify").checked;
  if (!url) { toast("请输入 URL", "error"); return; }
  const status = document.getElementById("ingest-status");
  const btn = document.getElementById("btn-ingest-submit");
  btn.disabled = true;
  status.textContent = "正在提取内容...（视频/图文可能需要 30 秒 - 2 分钟）";
  try {
    const r = await api("/api/ingest/url", {method: "POST", body: {url, auto_classify: auto}});
    if (r.ok) {
      const bits = [];
      if (r.has_video) bits.push("视频");
      if (r.has_transcript) bits.push("转录");
      if (r.has_images) bits.push("图片×" + (r.original_files || []).filter(p => /\.(png|jpe?g|webp|heic)$/i.test(p)).length);
      status.textContent = `✅ 已写入 ${r.inbox_path}\n资产: ${bits.join("、") || "文本"}\nunit_id: ${r.unit_id}`;
      toast("Ingested: " + (r.title || "").slice(0, 30));
      loadStats(); loadMaterials();
    } else {
      status.textContent = "❌ " + (r.error || "未知错误");
      toast("Ingest failed: " + (r.error || ""), "error");
    }
  } catch(e) {
    status.textContent = "❌ " + e.message;
    toast("Ingest failed: " + e.message, "error");
  } finally {
    btn.disabled = false;
  }
});

loadStats();
loadMaterials();
