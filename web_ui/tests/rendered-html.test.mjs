import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the EssayPod workspace", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>EssayPod — Research Studio<\/title>/i);
  assert.match(html, /EssayPod/);
  assert.match(html, /Knowledge Graph/);
});

test("wires the graph surface and background status into the client", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api.ts", import.meta.url), "utf8"),
  ]);

  assert.match(page, /type Surface = "paper" \| "graph"/);
  assert.match(page, /className="knowledge-graph-frame"/);
  assert.match(page, /\/api\/wiki\/graph-view/);
  assert.match(page, /\["queued","running"\].*llm_wiki/);
  assert.match(api, /llm_wiki\?:/);
});

test("keeps legacy tasks from blanking the workspace", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api.ts", import.meta.url), "utf8"),
  ]);

  assert.match(page, /class WorkspaceErrorBoundary/);
  assert.match(page, /task\.condense_level\|\|"mid"/);
  assert.match(api, /condense_level\?:/);
});

test("uses one neural EssayPod mark and an aligned graph icon", async () => {
  const [page, css] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/light-ui.css", import.meta.url), "utf8"),
  ]);

  assert.match(page, /className="logo-network"/);
  assert.match(css, /\.logo-mark \.logo-network/);
  assert.match(css, /\.sidebar \.knowledge-nav-icon \{[^}]*width: 22px;[^}]*height: 22px;/s);
  assert.match(css, /rotate\(-22deg\)/);
  assert.match(css, /rotate\(22deg\)/);
});

test("extends the app into a native draggable macOS titlebar", async () => {
  const [page, css, desktop] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/light-ui.css", import.meta.url), "utf8"),
    readFile(new URL("../../desktop/main.m", import.meta.url), "utf8"),
  ]);

  assert.match(page, /className="window-drag-rail" data-window-drag/);
  assert.match(page, /className="topbar" data-window-drag/);
  assert.match(css, /\.app-shell\.sidebar-collapsed \.topbar \{ padding-left: 92px; \}/);
  assert.match(desktop, /NSWindowStyleMaskFullSizeContentView/);
  assert.match(desktop, /titlebarAppearsTransparent = YES/);
  assert.match(desktop, /addScriptMessageHandler:self name:@"windowDrag"/);
  assert.match(desktop, /addScriptMessageHandler:self name:@"sidebarState"/);
  assert.match(desktop, /addScriptMessageHandler:self name:@"agentPanelState"/);
  assert.match(page, /messageHandlers\?\.sidebarState\?\.postMessage\(\{collapsed: sidebarCollapsed\}\)/);
  assert.match(desktop, /sidebarDragView\.hidden = \[state\[@"collapsed"\] boolValue\]/);
  assert.match(page, /messageHandlers\?\.agentPanelState\?\.postMessage\(\{open: agentPanelOpen\}\)/);
  assert.match(desktop, /CGFloat rightSafety = open \? 640 : 180/);
  assert.match(desktop, /performWindowDragWithEvent/);
  assert.match(desktop, /@interface WindowDragView : NSView/);
  assert.match(desktop, /NSMakeRect\(88, contentHeight - 54, 172, 54\)/);
  assert.match(desktop, /CGFloat headerRightSafety = 180/);
  assert.match(desktop, /NSMakeRect\(520, contentHeight - 60, headerDragWidth, 60\)/);
  assert.match(css, /\.window-drag-rail \{ height: 54px; margin: -12px -10px 4px; \}/);
  assert.match(page, /className="agent-panel-icon"/);
  assert.match(page, /className=\{`agent-panel-trigger/);
  assert.match(page, /data-window-no-drag/);
  assert.match(css, /\.agent-panel-trigger \{[^}]*position: fixed;[^}]*right: 14px;[^}]*width: 36px;/s);
  assert.match(css, /\.agent-panel-icon \{[^}]*width: 17px;[^}]*height: 16px;/s);
});

test("keeps the knowledge graph mounted after its first visit", async () => {
  const [page, css] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/light-ui.css", import.meta.url), "utf8"),
  ]);

  assert.match(page, /const \[graphMounted,setGraphMounted\] = useState\(false\)/);
  assert.match(page, /setGraphMounted\(true\);setSurface\("graph"\)/);
  assert.match(page, /graphMounted&&<KnowledgeGraphFrame active=\{surface==="graph"\} frameRef=\{graphFrameRef\} onLoad=\{\(\)=>sendGraphTrace\(\)\}\/>/);
  assert.match(page, /knowledge-graph-shell \$\{active\?"is-active":"is-cached"\}/);
  assert.match(page, /essaypod-graph-visibility/);
  assert.match(css, /\.workspace > \.knowledge-graph-shell\.is-active \{ visibility: visible; pointer-events: auto; \}/);
});

test("restores cached graph positions and fits throughout force simulation", async () => {
  const graph = await readFile(
    new URL("../../llm_wiki/llm_wiki/web/graph-src.js", import.meta.url),
    "utf8",
  );

  assert.match(graph, /GRAPH_CACHE_KEY = "essaypod-knowledge-graph-cache-v1"/);
  assert.match(graph, /window\.localStorage\.setItem\(activeCacheKey\(\)/);
  assert.match(graph, /renderGraph\(cached\.data, cached\.positions\)/);
  assert.match(graph, /on\("tick", \(\) => \{[\s\S]*if \(!userMovedView\) fitGraph\(\);/);
  assert.match(graph, /fingerprintGraph\(data\)/);
});

test("switches between page and atomic graphs and pauses refresh while hidden", async () => {
  const [graph, graphHtml] = await Promise.all([
    readFile(new URL("../../llm_wiki/llm_wiki/web/graph-src.js", import.meta.url), "utf8"),
    readFile(new URL("../../llm_wiki/llm_wiki/web/graph.html", import.meta.url), "utf8"),
  ]);

  assert.match(graphHtml, /data-graph-mode="page"/);
  assert.match(graphHtml, /data-graph-mode="atomic"/);
  assert.match(graph, /LLM_WIKI_ATOMIC_GRAPH_ENDPOINT/);
  assert.match(graph, /essaypod-graph-visibility/);
  assert.match(graph, /function stopRefreshTimer\(\)/);
  assert.match(graph, /if \(!graphVisible\) return/);
});

test("keeps the graph instance alive while wiki details open in an overlay", async () => {
  const [graph, graphHtml, page] = await Promise.all([
    readFile(new URL("../../llm_wiki/llm_wiki/web/graph-src.js", import.meta.url), "utf8"),
    readFile(new URL("../../llm_wiki/llm_wiki/web/graph.html", import.meta.url), "utf8"),
    readFile(new URL("../../llm_wiki/llm_wiki/web/page.js", import.meta.url), "utf8"),
  ]);

  assert.match(graphHtml, /id="pageOverlay"/);
  assert.match(graphHtml, /id="pageFrame"/);
  assert.match(graph, /event\.preventDefault\(\);\s*openPageOverlay\(link\.href, link\)/);
  assert.match(graph, /graphShell\.inert = true/);
  assert.match(graph, /essaypod-wiki-close-page/);
  assert.match(page, /params\.get\("embedded"\) === "graph"/);
  assert.match(page, /window\.parent\.postMessage\(\{ type: "essaypod-wiki-close-page" \}/);
  assert.match(page, /keepPageLinksInsideOverlay\(\)/);
});

test("downloads translated PDFs without navigating the macOS app away", async () => {
  const [page, desktop] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../../desktop/main.m", import.meta.url), "utf8"),
  ]);

  assert.match(page, /href=\{taskTranslationPdfUrl\(task\.id\)\} download/);
  assert.match(page, /href=\{taskTranslationReadingPdfUrl\(task\.id\)\} download/);
  assert.match(desktop, /WKDownloadDelegate/);
  assert.match(desktop, /navigationAction\.shouldPerformDownload \|\| \[self isEssayPodDownloadURL:url\]/);
  assert.match(desktop, /WKNavigationActionPolicyDownload/);
  assert.match(desktop, /decideDestinationUsingResponse/);
  assert.match(desktop, /NSSavePanel \*panel/);
  assert.doesNotMatch(desktop, /!navigationResponse\.canShowMIMEType/);
});
