const state = {
  taxonomy: [],
  periodChoices: [],
  selectedCategories: new Set(),
  keywords: [],
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.message || `请求失败 ${res.status}`);
  }
  return data;
}

function fillPeriodSelects() {
  for (const id of ["cat-period", "kw-period"]) {
    const el = document.getElementById(id);
    el.innerHTML = state.periodChoices
      .map((p) => `<option value="${p.days}">${p.label}</option>`)
      .join("");
  }
}

function renderTaxonomy(filter = "") {
  const root = document.getElementById("taxonomy");
  const q = filter.trim().toLowerCase();
  root.innerHTML = "";

  for (const group of state.taxonomy) {
    const cats = group.categories.filter((c) => {
      if (!q) return true;
      return (
        c.id.toLowerCase().includes(q) ||
        c.name.toLowerCase().includes(q) ||
        (c.description || "").toLowerCase().includes(q)
      );
    });
    if (!cats.length) continue;

    const details = document.createElement("details");
    details.className = "group";
    details.open = Boolean(q) || group.name === "Computer Science";
    const selectedInGroup = cats.filter((c) => state.selectedCategories.has(c.id)).length;
    details.innerHTML = `
      <summary>
        <span>${group.name}</span>
        <span class="group-count">${selectedInGroup}/${cats.length}</span>
      </summary>
      <div class="cat-list"></div>
    `;
    const list = details.querySelector(".cat-list");
    for (const cat of cats) {
      const item = document.createElement("label");
      item.className = "cat-item";
      item.innerHTML = `
        <input type="checkbox" value="${cat.id}" ${
          state.selectedCategories.has(cat.id) ? "checked" : ""
        } />
        <div>
          <div class="id">${cat.id}</div>
          <div class="name">${cat.name}</div>
          <div class="desc">${cat.description || ""}</div>
        </div>
      `;
      item.querySelector("input").addEventListener("change", (e) => {
        if (e.target.checked) state.selectedCategories.add(cat.id);
        else state.selectedCategories.delete(cat.id);
        updateSelectedCount();
      });
      list.appendChild(item);
    }
    root.appendChild(details);
  }
  updateSelectedCount();
}

function updateSelectedCount() {
  document.getElementById("cat-selected-count").textContent = `已选 ${state.selectedCategories.size}`;
}

function renderKeywords() {
  const box = document.getElementById("kw-chips");
  box.innerHTML = "";
  for (const kw of state.keywords) {
    const chip = document.createElement("label");
    chip.className = "chip";
    chip.innerHTML = `
      <input type="checkbox" ${kw.enabled ? "checked" : ""} />
      <span>${escapeHtml(kw.text)}</span>
      <button type="button" aria-label="删除">×</button>
    `;
    chip.querySelector("input").addEventListener("change", (e) => {
      kw.enabled = e.target.checked;
      updateKeywordCount();
    });
    chip.querySelector("button").addEventListener("click", (e) => {
      e.preventDefault();
      state.keywords = state.keywords.filter((x) => x.text !== kw.text);
      renderKeywords();
    });
    box.appendChild(chip);
  }
  updateKeywordCount();
}

function updateKeywordCount() {
  const active = state.keywords.filter((k) => k.enabled).length;
  document.getElementById("kw-count").textContent = `已选 ${active} / ${state.keywords.length}`;
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderPapers(containerId, metaId, payload) {
  const container = document.getElementById(containerId);
  const meta = document.getElementById(metaId);
  const papers = payload.papers || [];
  if (!papers.length) {
    container.innerHTML = `<div class="empty">还没有结果。保存设置后点击「立即拉取」试一下。</div>`;
    meta.textContent = "暂无数据";
    return;
  }
  meta.textContent = `共 ${papers.length} 篇 · ${payload.fetched_at || ""}`;
  container.innerHTML = papers
    .map((p) => {
      const cats = (p.categories || [])
        .slice(0, 4)
        .map((c) => `<span class="tag">${escapeHtml(c)}</span>`)
        .join("");
      const newBadge = p.is_new ? `<span class="tag new">NEW</span>` : "";
      return `
        <article class="paper">
          <h4>${escapeHtml(p.title || "(无标题)")}</h4>
          <div class="authors">${escapeHtml((p.authors || []).join(", ") || "Unknown authors")}</div>
          <div class="summary">${escapeHtml(p.summary || "")}</div>
          <div class="footer">
            ${newBadge}
            <span class="tag">${escapeHtml(p.id || "")}</span>
            ${cats}
            <span class="links">
              <a href="${p.abs_url}" target="_blank" rel="noreferrer">Abstract</a>
              <a href="${p.pdf_url}" target="_blank" rel="noreferrer">PDF</a>
            </span>
          </div>
        </article>
      `;
    })
    .join("");
}

function setStatus(id, text, isError = false) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.classList.toggle("error", isError);
}

function syncTimeModeUI(prefix) {
  const mode = document.getElementById(`${prefix}-time-mode`).value;
  document.getElementById(`${prefix}-time-wrap`).hidden = mode !== "scheduled";
}

function applyPullTimeUI(prefix, pullTime) {
  const isNow = !pullTime || pullTime === "now" || pullTime === "immediate";
  document.getElementById(`${prefix}-time-mode`).value = isNow ? "now" : "scheduled";
  if (!isNow) document.getElementById(`${prefix}-time`).value = pullTime;
  syncTimeModeUI(prefix);
}

function readPullTime(prefix) {
  const mode = document.getElementById(`${prefix}-time-mode`).value;
  if (mode === "now") return "now";
  return document.getElementById(`${prefix}-time`).value || "09:00";
}

function applyCategoryConfig(cfg) {
  document.getElementById("cat-enabled").checked = !!cfg.enabled;
  applyPullTimeUI("cat", cfg.pull_time);
  document.getElementById("cat-period").value = String(
    cfg.period_days === 0 || cfg.period_days ? cfg.period_days : 1
  );
  document.getElementById("cat-max").value = cfg.max_results || 20;
  state.selectedCategories = new Set(cfg.categories || []);
  renderTaxonomy(document.getElementById("cat-filter").value);
  const scheduleHint =
    cfg.pull_time === "now"
      ? "拉取时间：立即"
      : `拉取时间：${cfg.pull_time || "09:00"}`;
  const periodHint = Number(cfg.period_days) === 0 ? "周期：仅一次" : `周期：每 ${cfg.period_days} 天`;
  const last = cfg.last_run_at
    ? `上次运行：${cfg.last_run_at} · ${cfg.last_status || ""}${
        cfg.last_error ? " · " + cfg.last_error : ""
      }`
    : "尚未自动/手动运行";
  setStatus("cat-status", `${scheduleHint} · ${periodHint} · ${last}`, cfg.last_status === "error");
}

function applyKeywordConfig(cfg) {
  document.getElementById("kw-enabled").checked = !!cfg.enabled;
  applyPullTimeUI("kw", cfg.pull_time);
  document.getElementById("kw-period").value = String(
    cfg.period_days === 0 || cfg.period_days ? cfg.period_days : 1
  );
  document.getElementById("kw-max").value = cfg.max_results || 20;
  document.getElementById("kw-title").checked = cfg.match_title !== false;
  document.getElementById("kw-abs").checked = cfg.match_abstract !== false;
  state.keywords = (cfg.keywords || []).map((k) =>
    typeof k === "string" ? { text: k, enabled: true } : { text: k.text, enabled: k.enabled !== false }
  );
  renderKeywords();
  const scheduleHint =
    cfg.pull_time === "now"
      ? "拉取时间：立即"
      : `拉取时间：${cfg.pull_time || "09:00"}`;
  const periodHint = Number(cfg.period_days) === 0 ? "周期：仅一次" : `周期：每 ${cfg.period_days} 天`;
  const last = cfg.last_run_at
    ? `上次运行：${cfg.last_run_at} · ${cfg.last_status || ""}${
        cfg.last_error ? " · " + cfg.last_error : ""
      }`
    : "尚未自动/手动运行";
  setStatus("kw-status", `${scheduleHint} · ${periodHint} · ${last}`, cfg.last_status === "error");
}

async function saveCategory({ rearm = true } = {}) {
  const body = {
    enabled: document.getElementById("cat-enabled").checked,
    categories: [...state.selectedCategories],
    max_results: Number(document.getElementById("cat-max").value || 20),
    period_days: Number(document.getElementById("cat-period").value),
    pull_time: readPullTime("cat"),
    timezone: "Asia/Shanghai",
  };
  const qs = rearm ? "" : "?rearm=false";
  const cfg = await api(`/api/category/config${qs}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
  applyCategoryConfig(cfg);
  const armed =
    rearm && cfg.enabled && (cfg.pull_time === "now" || Number(cfg.period_days) === 0)
      ? "已启用，调度器将在约 15 秒内触发。"
      : "设置已保存";
  setStatus("cat-status", armed);
  return cfg;
}

async function saveKeyword({ rearm = true } = {}) {
  const body = {
    enabled: document.getElementById("kw-enabled").checked,
    keywords: state.keywords,
    match_title: document.getElementById("kw-title").checked,
    match_abstract: document.getElementById("kw-abs").checked,
    max_results: Number(document.getElementById("kw-max").value || 20),
    period_days: Number(document.getElementById("kw-period").value),
    pull_time: readPullTime("kw"),
    timezone: "Asia/Shanghai",
  };
  const qs = rearm ? "" : "?rearm=false";
  const cfg = await api(`/api/keyword/config${qs}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
  applyKeywordConfig(cfg);
  const armed =
    rearm && cfg.enabled && (cfg.pull_time === "now" || Number(cfg.period_days) === 0)
      ? "已启用，调度器将在约 15 秒内触发。"
      : "设置已保存";
  setStatus("kw-status", armed);
  return cfg;
}

function showLoadingPapers(containerId, metaId, text) {
  document.getElementById(metaId).textContent = text;
  document.getElementById(containerId).innerHTML =
    `<div class="empty">${text}<br/><span style="font-size:12px">周末走 API，通常 5–20 秒；若限流会提示错误。</span></div>`;
}

function revealResults(blockId) {
  const el = document.getElementById(blockId);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runCategory() {
  const btn = document.getElementById("cat-run");
  btn.disabled = true;
  setStatus("cat-status", "正在拉取…请稍候");
  showLoadingPapers("cat-papers", "cat-result-meta", "正在从 arXiv 拉取论文…");
  revealResults("cat-results-block");
  try {
    // 手动拉取时不重新武装定时任务，避免和调度器抢请求
    await saveCategory({ rearm: false });
    const result = await api("/api/category/run", { method: "POST", body: "{}" });
    renderPapers("cat-papers", "cat-result-meta", result);
    revealResults("cat-results-block");
    const cfg = await api("/api/category/config");
    applyCategoryConfig(cfg);
    const n = (result.papers || []).length;
    const err = result.meta?.errors ? Object.keys(result.meta.errors).length : 0;
    setStatus(
      "cat-status",
      n ? `拉取完成：${n} 篇（见右侧上方「最新结果」）` : `拉取完成但无论文${err ? "，部分分类失败" : ""}`,
      !n
    );
  } catch (err) {
    setStatus("cat-status", err.message, true);
    document.getElementById("cat-papers").innerHTML =
      `<div class="empty">拉取失败：${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

async function runKeyword() {
  const btn = document.getElementById("kw-run");
  btn.disabled = true;
  setStatus("kw-status", "正在拉取…请稍候");
  showLoadingPapers("kw-papers", "kw-result-meta", "正在按关键词查询 arXiv…");
  revealResults("kw-results-block");
  try {
    await saveKeyword({ rearm: false });
    const result = await api("/api/keyword/run", { method: "POST", body: "{}" });
    renderPapers("kw-papers", "kw-result-meta", result);
    revealResults("kw-results-block");
    const cfg = await api("/api/keyword/config");
    applyKeywordConfig(cfg);
    const n = (result.papers || []).length;
    const q = result.meta?.search_query ? ` · ${result.meta.search_query}` : "";
    setStatus(
      "kw-status",
      n ? `拉取完成：${n} 篇（见右侧上方「最新结果」）${q}` : `拉取完成但无论文${q}`,
      !n
    );
  } catch (err) {
    setStatus("kw-status", err.message, true);
    document.getElementById("kw-papers").innerHTML =
      `<div class="empty">拉取失败：${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function addKeyword() {
  const input = document.getElementById("kw-input");
  const value = input.value.trim();
  if (!value) return;
  if (!state.keywords.some((k) => k.text.toLowerCase() === value.toLowerCase())) {
    state.keywords.push({ text: value, enabled: true });
  }
  input.value = "";
  renderKeywords();
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      document.getElementById(`panel-${tab.dataset.tab}`).classList.add("active");
    });
  });
}

async function init() {
  bindTabs();
  const meta = await api("/api/meta");
  state.taxonomy = meta.taxonomy || [];
  state.periodChoices = meta.period_choices || [];
  fillPeriodSelects();

  const [catCfg, kwCfg, catResults, kwResults] = await Promise.all([
    api("/api/category/config"),
    api("/api/keyword/config"),
    api("/api/category/results"),
    api("/api/keyword/results"),
  ]);
  applyCategoryConfig(catCfg);
  applyKeywordConfig(kwCfg);
  renderPapers("cat-papers", "cat-result-meta", catResults);
  renderPapers("kw-papers", "kw-result-meta", kwResults);

  document.getElementById("cat-filter").addEventListener("input", (e) => {
    renderTaxonomy(e.target.value);
  });
  document.getElementById("cat-time-mode").addEventListener("change", () => syncTimeModeUI("cat"));
  document.getElementById("kw-time-mode").addEventListener("change", () => syncTimeModeUI("kw"));
  document.getElementById("cat-save").addEventListener("click", () =>
    saveCategory().catch((err) => setStatus("cat-status", err.message, true))
  );
  document.getElementById("cat-run").addEventListener("click", runCategory);
  document.getElementById("kw-save").addEventListener("click", () =>
    saveKeyword().catch((err) => setStatus("kw-status", err.message, true))
  );
  document.getElementById("kw-run").addEventListener("click", runKeyword);
  document.getElementById("kw-add").addEventListener("click", addKeyword);
  document.getElementById("kw-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addKeyword();
    }
  });
}

init().catch((err) => {
  console.error(err);
  alert(`初始化失败: ${err.message}`);
});
