const $ = (selector) => document.querySelector(selector);
let researchSession = null;
let backgroundPoll = null;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

function setResult(element, value, error = false) {
  element.hidden = false;
  element.classList.toggle("error", error);
  element.textContent = value;
}

async function refresh() {
  const [status, pageData, pendingData] = await Promise.all([
    api("/api/status"),
    api("/api/pages"),
    api("/api/pending"),
  ]);
  $("#provider").textContent = `${status.provider} · ${status.model}`;
  $("#tavilyStatus").textContent = pendingData.available
    ? backgroundLabel(pendingData.background)
    : "Tavily 未配置";
  $("#researchForm button").disabled = !pendingData.available;
  $("#sourceCount").textContent = status.sources;
  $("#pageCount").textContent = status.pages;
  $("#entityCount").textContent = status.by_type.entities;
  $("#conceptCount").textContent = status.by_type.concepts;
  const list = $("#pages");
  list.replaceChildren();
  for (const page of pageData.pages) {
    const button = document.createElement("button");
    button.className = "page-item";
    const title = document.createElement("strong");
    title.textContent = page.title;
    const type = document.createElement("small");
    type.textContent = page.type;
    button.append(title, type);
    button.addEventListener("click", () => viewPage(page.path));
    list.append(button);
  }
  renderPending(pendingData.concepts, pendingData.background);
  scheduleBackgroundRefresh(pendingData.background);
}

function backgroundLabel(background = {}) {
  if (background.running) return `后台补全中 · ${background.running} 正在生成`;
  if (background.queued) return `后台补全中 · ${background.queued} 排队`;
  if (background.failed) return `后台已完成 · ${background.failed} 项需重试`;
  if (background.completed) return `后台已补全 ${background.completed} 项`;
  return "Tavily 已连接 · 自动补全";
}

function scheduleBackgroundRefresh(background = {}) {
  if (backgroundPoll) window.clearTimeout(backgroundPoll);
  backgroundPoll = null;
  if (background.active || background.queued || background.running) {
    backgroundPoll = window.setTimeout(() => {
      refresh().catch((error) => setResult($("#researchResult"), error.message, true));
    }, 4000);
  }
}

function renderPending(concepts, background = {}) {
  const list = $("#pendingConcepts");
  list.replaceChildren();
  const jobs = new Map((background.jobs || []).map((job) => [job.canonical_id, job]));
  if (!concepts.length) {
    const empty = document.createElement("p");
    empty.className = "pending-empty";
    empty.textContent = "当前没有待补全概念。你仍可以在上方主动搜索一个背景概念。";
    list.append(empty);
    return;
  }
  for (const concept of concepts) {
    const card = document.createElement("div");
    card.className = "pending-item";
    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = concept.label || `[[${concept.canonical_id}]]`;
    const meta = document.createElement("small");
    meta.textContent = `${concept.mention_count} 个页面引用`;
    text.append(title, meta);
    const button = document.createElement("button");
    const job = jobs.get(concept.canonical_id);
    button.type = "button";
    button.className = "mini";
    button.disabled = ["queued", "running"].includes(job?.status);
    button.textContent = job?.status === "running"
      ? "正在补全"
      : job?.status === "queued"
        ? "等待补全"
        : "手动重试";
    button.addEventListener("click", () => {
      $("#researchConcept").value = concept.label || concept.canonical_id;
      searchConcept(concept.label || concept.canonical_id, button);
    });
    card.append(text, button);
    list.append(card);
  }
}

async function searchConcept(concept, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在搜索…";
  try {
    const data = await api("/api/research/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ concept }),
    });
    researchSession = data;
    renderCandidates(data);
    $("#candidatePanel").hidden = false;
    setResult(
      $("#researchResult"),
      `找到 ${data.candidates.length} 个候选来源。请选择 1–3 个后再创建页面。`,
    );
  } catch (error) {
    setResult($("#researchResult"), error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function renderCandidates(data) {
  $("#candidateTitle").textContent = `${data.concept} · 候选来源`;
  const list = $("#candidates");
  list.replaceChildren();
  data.candidates.forEach((candidate, index) => {
    const label = document.createElement("label");
    label.className = "candidate";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = String(index);
    checkbox.checked = index < 3;
    const body = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = candidate.title;
    const domain = document.createElement("small");
    domain.textContent = `${candidate.domain} · relevance ${candidate.score}`;
    const snippet = document.createElement("span");
    snippet.className = "candidate-snippet";
    snippet.textContent = candidate.snippet;
    const link = document.createElement("a");
    link.href = candidate.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = "查看原网页";
    body.append(title, domain, snippet, link);
    label.append(checkbox, body);
    list.append(label);
  });
}

async function viewPage(path) {
  const data = await api(`/api/page?path=${encodeURIComponent(path)}`);
  const pre = document.createElement("pre");
  pre.textContent = data.content;
  $("#viewer").replaceChildren(pre);
}

$("#file").addEventListener("change", (event) => {
  $("#fileLabel").textContent = event.target.files[0]?.name || "选择文件或拖放到这里";
});
for (const eventName of ["dragenter", "dragover"]) {
  $("#dropzone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("#dropzone").classList.add("drag");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  $("#dropzone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("#dropzone").classList.remove("drag");
  });
}
$("#dropzone").addEventListener("drop", (event) => {
  const transfer = new DataTransfer();
  if (event.dataTransfer.files[0]) transfer.items.add(event.dataTransfer.files[0]);
  $("#file").files = transfer.files;
  $("#fileLabel").textContent = transfer.files[0]?.name || "选择文件或拖放到这里";
});

$("#ingestForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  button.textContent = "正在读取、分析并维护 Wiki…";
  try {
    const form = new FormData();
    form.append("file", $("#file").files[0]);
    form.append("focus", $("#focus").value);
    const data = await api("/api/ingest", { method: "POST", body: form });
    const text = data.warnings.length
      ? data.warnings.join("\n")
      : `编译完成\n\nRaw: ${data.source_file}\nSource page: ${data.source_page}\n创建 ${data.created.length} 页，更新 ${data.updated.length} 页`;
    setResult($("#ingestResult"), text);
    await refresh();
    if (data.source_page) await viewPage(data.source_page);
  } catch (error) {
    setResult($("#ingestResult"), error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "开始编译 Wiki";
  }
});

$("#queryForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const data = await api("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: $("#question").value, save: $("#saveAnswer").checked }),
    });
    setResult($("#queryResult"), `${data.answer}${data.saved ? `\n\n已保存：${data.saved}` : ""}`);
    await refresh();
  } catch (error) {
    setResult($("#queryResult"), error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#researchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await searchConcept($("#researchConcept").value, event.submitter);
});

$("#candidateForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!researchSession) return;
  const selected = [
    ...$("#candidates").querySelectorAll("input:checked"),
  ].map((input) => Number(input.value));
  if (selected.length < 1 || selected.length > 3) {
    setResult($("#researchResult"), "请选择 1–3 个网页来源。", true);
    return;
  }
  const button = event.submitter;
  button.disabled = true;
  button.textContent = "Ark 正在基于网页证据编译…";
  try {
    const data = await api("/api/research/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: researchSession.session_id,
        selected,
      }),
    });
    setResult(
      $("#researchResult"),
      `已创建 ${data.title}\n状态：${data.status}\n页面：${data.page}\n保存 ${data.sources.length} 份不可变网页快照`,
    );
    researchSession = null;
    $("#candidatePanel").hidden = true;
    await refresh();
    await viewPage(data.page);
  } catch (error) {
    setResult($("#researchResult"), error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "用所选来源创建临时 Concept";
  }
});

$("#lintButton").addEventListener("click", async () => {
  const button = $("#lintButton");
  button.disabled = true;
  try {
    const data = await api("/api/lint", { method: "POST" });
    setResult($("#lintResult"), `Lint 完成：发现 ${data.count} 个问题。\n报告：${data.report}`);
    await refresh();
    await viewPage(data.report);
  } catch (error) {
    setResult($("#lintResult"), error.message, true);
  } finally {
    button.disabled = false;
  }
});

refresh().catch((error) => setResult($("#ingestResult"), error.message, true));
