const params = new URLSearchParams(window.location.search);
const path = params.get("path");
const embeddedInGraph = params.get("embedded") === "graph";
const content = document.querySelector("#pageContent");
const meta = document.querySelector("#pageMeta");
const backLink = document.querySelector(".back");
const translationStatus = document.querySelector("#translationStatus");
const languageButtons = [...document.querySelectorAll(".language-button")];
const pageEndpoint = window.LLM_WIKI_PAGE_ENDPOINT || "/api/page";
const translationEndpoint = window.LLM_WIKI_TRANSLATION_ENDPOINT || "/api/page-translation";
const pages = new Map();
let activeLanguage = "en";
let requestVersion = 0;

function closeToGraph() {
  window.parent.postMessage({ type: "essaypod-wiki-close-page" }, window.location.origin);
}

if (embeddedInGraph) {
  backLink.href = "#knowledge-graph";
  backLink.addEventListener("click", (event) => {
    event.preventDefault();
    closeToGraph();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeToGraph();
  });
}

function keepPageLinksInsideOverlay() {
  if (!embeddedInGraph) return;
  content.querySelectorAll("a[href]").forEach((link) => {
    const url = new URL(link.href, window.location.href);
    const isWikiPage = url.origin === window.location.origin
      && url.searchParams.has("path")
      && (url.pathname === "/page" || url.pathname === "/api/wiki/page-view");
    if (!isWikiPage) return;
    url.searchParams.set("embedded", "graph");
    link.href = url.toString();
  });
}

function renderPage(data, language) {
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  document.title = `${data.title} · LLM Wiki`;
  const details = [data.type, data.status, data.updated].filter(Boolean);
  if (language === "zh") details.push(data.translation_provider || "Google 免费翻译");
  meta.hidden = false;
  meta.textContent = details.join(" · ");
  content.innerHTML = data.html;
  keepPageLinksInsideOverlay();
  content.setAttribute("aria-busy", "false");
}

function setLanguageState(language, busy = false) {
  languageButtons.forEach((button) => {
    const selected = button.dataset.language === language;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", String(selected));
    button.disabled = busy;
  });
  content.setAttribute("aria-busy", String(busy));
  translationStatus.textContent = busy ? "正在翻译…" : "";
}

async function fetchPage(language) {
  if (!path || !path.startsWith("wiki/")) {
    throw new Error("缺少有效的 Wiki 页面路径。");
  }
  if (pages.has(language)) return pages.get(language);
  const response = language === "en"
    ? await fetch(`${pageEndpoint}?path=${encodeURIComponent(path)}`)
    : await fetch(translationEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, language: "zh" }),
      });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "无法读取 Wiki 页面。");
  pages.set(language, data);
  return data;
}

async function switchLanguage(language) {
  if (language === activeLanguage && pages.has(language)) return;
  const version = ++requestVersion;
  const previousLanguage = activeLanguage;
  setLanguageState(language, true);
  try {
    const data = await fetchPage(language);
    if (version !== requestVersion) return;
    activeLanguage = language;
    renderPage(data, language);
    setLanguageState(language, false);
  } catch (error) {
    if (version !== requestVersion) return;
    setLanguageState(previousLanguage, false);
    translationStatus.textContent = error.message;
    translationStatus.classList.add("is-error");
  }
}

languageButtons.forEach((button) => {
  button.addEventListener("click", () => {
    translationStatus.classList.remove("is-error");
    switchLanguage(button.dataset.language);
  });
});

fetchPage("en").then((data) => {
  activeLanguage = "en";
  renderPage(data, "en");
  setLanguageState("en", false);
}).catch((error) => {
  content.innerHTML = "";
  const message = document.createElement("p");
  message.className = "error";
  message.textContent = error.message;
  content.append(message);
});
