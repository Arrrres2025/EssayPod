import { Application, Container, Graphics, Rectangle, Text, TextStyle } from "pixi.js";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceRadial,
  forceSimulation,
} from "d3-force";

const COLORS = {
  concept: 0x858b91,
  entity: 0x74869a,
  source: 0xd2a44f,
  paper: 0xd2a44f,
  claim: 0xc96b62,
  method: 0x6686a3,
  dataset: 0x6d9985,
  experiment: 0x8e78a5,
  metric: 0xb68a52,
  result: 0x4f948e,
  limitation: 0xb66d67,
  hypothesis: 0x9474a3,
  definition: 0x7e858b,
  researchquestion: 0x637895,
  synthesis: 0xcc6258,
  comparison: 0x9a82ac,
  overview: 0x565d63,
  glossary: 0xb2b6ba,
  page: 0xa3a8ac,
  pending: 0xb5b9bd,
};

const host = document.querySelector("#canvasHost");
const tooltip = document.querySelector("#tooltip");
const inspector = document.querySelector("#inspector");
const stats = document.querySelector("#graphStats");
const emptyState = document.querySelector("#emptyState");
const depthInput = document.querySelector("#depth");
const distanceInput = document.querySelector("#distance");
const depthValue = document.querySelector("#depthValue");
const distanceValue = document.querySelector("#distanceValue");
const description = document.querySelector("#graphDescription");
const legend = document.querySelector("#graphLegend");
const modeButtons = [...document.querySelectorAll("[data-graph-mode]")];
const graphShell = document.querySelector(".graph-shell");
const pageOverlay = document.querySelector("#pageOverlay");
const pageFrame = document.querySelector("#pageFrame");
const graphEndpoint = window.LLM_WIKI_GRAPH_ENDPOINT || "/api/graph";
const atomicGraphEndpoint = window.LLM_WIKI_ATOMIC_GRAPH_ENDPOINT || "/api/knowledge/graph";
const pageBase = window.LLM_WIKI_PAGE_BASE || "/page";
const autoRefreshMs = Number(window.LLM_WIKI_AUTO_REFRESH_MS || 0);

const app = new Application();
await app.init({
  resizeTo: host,
  backgroundAlpha: 0,
  antialias: true,
  autoDensity: true,
  resolution: Math.min(window.devicePixelRatio || 1, 2),
  powerPreference: "high-performance",
});
app.ticker.maxFPS = 120;
app.canvas.setAttribute("tabindex", "0");
host.appendChild(app.canvas);

const world = new Container();
const edgeLayer = new Graphics();
const nodeLayer = new Container();
world.addChild(edgeLayer, nodeLayer);
app.stage.addChild(world);
app.stage.eventMode = "static";
app.stage.hitArea = new Rectangle(0, 0, app.screen.width, app.screen.height);

let graph = { nodes: [], links: [] };
let simulation;
let nodeViews = new Map();
let selectedId = null;
let activeIds = null;
let dragged = null;
let panning = null;
let userMovedView = false;
let graphFingerprint = "";
let graphSourceData = { nodes: [], links: [] };
let pageOverlayCloseTimer = 0;
let pageOverlayReturnFocus = null;
let graphMode = window.localStorage.getItem("essaypod-knowledge-graph-mode") === "atomic"
  ? "atomic"
  : "page";
let parentGraphActive = window.parent === window;
let graphVisible = parentGraphActive && document.visibilityState !== "hidden";
let refreshTimer = 0;
let requestController = null;
let requestInFlight = null;
let agentTrace = {
  activeAgents: new Map(),
  frontier: new Set(),
  visited: new Set(),
  useful: new Set(),
  bridges: new Set(),
  deadEnds: new Set(),
  pathEdges: new Set(),
};
const SWARM_COLORS = [0x5479d4, 0x8b69c9, 0x2f9b7a, 0xd28a45, 0xc85f78];

const GRAPH_CACHE_KEY = "essaypod-knowledge-graph-cache-v1";
const GRAPH_CACHE_MAX_AGE = 7 * 24 * 60 * 60 * 1000;

function activeCacheKey() {
  return `${GRAPH_CACHE_KEY}:${graphMode}`;
}

function endpointId(value) {
  return typeof value === "object" ? value?.id : value;
}

function serializableGraph(data) {
  return {
    nodes: (data.nodes || []).map(({ x, y, vx, vy, fx, fy, index, ...node }) => node),
    links: (data.links || []).map((link) => ({
      ...link,
      source: endpointId(link.source),
      target: endpointId(link.target),
    })),
  };
}

function fingerprintGraph(data) {
  return JSON.stringify({
    nodes: (data.nodes || []).map((node) => [
      node.id, node.label, node.kind, node.status, node.summary, node.path,
    ]),
    links: (data.links || []).map((link) => [
      endpointId(link.source), endpointId(link.target), link.label,
    ]),
  });
}

function readGraphCache() {
  try {
    const cached = JSON.parse(window.localStorage.getItem(activeCacheKey()) || "null");
    if (!cached?.fingerprint || !cached?.data || Date.now() - cached.savedAt > GRAPH_CACHE_MAX_AGE) {
      return null;
    }
    return cached;
  } catch {
    return null;
  }
}

function writeGraphCache() {
  if (!graphFingerprint || !graphSourceData.nodes.length) return;
  try {
    const positions = Object.fromEntries(graph.nodes
      .filter((node) => Number.isFinite(node.x) && Number.isFinite(node.y))
      .map((node) => [node.id, [Number(node.x.toFixed(2)), Number(node.y.toFixed(2))]]));
    window.localStorage.setItem(activeCacheKey(), JSON.stringify({
      version: 1,
      savedAt: Date.now(),
      fingerprint: graphFingerprint,
      data: graphSourceData,
      positions,
    }));
  } catch {
    // Graph rendering must continue even when browser storage is unavailable.
  }
}

function atomicGraphToDisplay(data) {
  const nodes = (data.nodes || []).map((node) => {
    const type = String(node.type || "Knowledge");
    const isPaper = type === "Paper";
    return {
      ...node,
      label: node.label || node.id,
      kind: isPaper ? "paper" : type.toLowerCase(),
      summary: node.summary || (isPaper ? "论文来源与原子知识的证据入口。" : "暂无摘要。"),
      status: node.status || (isPaper ? "source_backed" : "extracted"),
      path: isPaper ? (node.path || "") : "",
      evidenceCount: (node.evidence_ids || []).length,
    };
  });
  const known = new Set(nodes.map((node) => node.id));
  const links = (data.edges || [])
    .filter((edge) => known.has(endpointId(edge.source)) && known.has(endpointId(edge.target)))
    .map((edge) => ({
      ...edge,
      source: endpointId(edge.source),
      target: endpointId(edge.target),
      label: edge.relation || edge.label || "related",
    }));

  // Paper provenance is structurally useful even when the model did not emit a
  // direct paper-to-node relation. Keep it visually available without mutating
  // the persisted scientific relations.
  const connected = new Set();
  for (const link of links) {
    connected.add(`${link.source}\0${link.target}`);
    connected.add(`${link.target}\0${link.source}`);
  }
  for (const node of nodes) {
    const sourceId = String(node.source_id || "");
    if (!sourceId || sourceId === node.id || !known.has(sourceId)) continue;
    if (connected.has(`${sourceId}\0${node.id}`)) continue;
    links.push({ source: sourceId, target: node.id, label: "contains", inferred: true });
  }
  return { nodes, links };
}

function displayGraphPayload(data) {
  return graphMode === "atomic" ? atomicGraphToDisplay(data) : data;
}

function updateModeChrome() {
  for (const button of modeButtons) {
    const selected = button.dataset.graphMode === graphMode;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  if (graphMode === "atomic") {
    description.innerHTML = "论文论证被拆解为 <code>Method / Dataset / Experiment / Result</code> 等原子节点";
    legend.innerHTML = `
      <span><i style="background:#d2a44f"></i>Paper</span>
      <span><i style="background:#6686a3"></i>Method</span>
      <span><i style="background:#6d9985"></i>Dataset</span>
      <span><i style="background:#8e78a5"></i>Experiment</span>
      <span><i style="background:#4f948e"></i>Result</span>
      <span><i style="background:#c96b62"></i>Claim</span>`;
  } else {
    description.innerHTML = "页面是节点，<code>[[wikilink]]</code> 是连接";
    legend.innerHTML = `
      <span><i class="concept"></i>Concept</span>
      <span><i class="entity"></i>Entity</span>
      <span><i class="source"></i>Source</span>
      <span><i class="synthesis"></i>Synthesis</span>
      <span><i class="pending"></i>Pending</span>`;
  }
}

function showCachedGraph() {
  const cached = readGraphCache();
  if (!cached) return false;
  graphFingerprint = cached.fingerprint;
  renderGraph(cached.data, cached.positions);
  return true;
}

function switchGraphMode(mode) {
  if (!['page', 'atomic'].includes(mode) || mode === graphMode) return;
  writeGraphCache();
  graphMode = mode;
  window.localStorage.setItem("essaypod-knowledge-graph-mode", graphMode);
  graphFingerprint = "";
  updateModeChrome();
  if (!showCachedGraph()) {
    simulation?.stop();
    graph = { nodes: [], links: [] };
    nodeLayer.removeChildren().forEach((child) => child.destroy({ children: true }));
    nodeViews = new Map();
    stats.textContent = "正在读取图谱…";
  }
  loadCurrentWiki({ force: true });
}

function nodeRadius(node) {
  return 5.5 + Math.min(14, Math.sqrt(node.degree || 1) * 3);
}

function activeNeighborhood(root, depth) {
  if (!root) return null;
  const adjacency = new Map(graph.nodes.map((node) => [node.id, new Set()]));
  for (const link of graph.links) {
    const source = typeof link.source === "object" ? link.source.id : link.source;
    const target = typeof link.target === "object" ? link.target.id : link.target;
    adjacency.get(source)?.add(target);
    adjacency.get(target)?.add(source);
  }
  const seen = new Set([root]);
  let frontier = [root];
  for (let level = 0; level < depth; level += 1) {
    const next = [];
    for (const id of frontier) {
      for (const neighbor of adjacency.get(id) || []) {
        if (!seen.has(neighbor)) {
          seen.add(neighbor);
          next.push(neighbor);
        }
      }
    }
    frontier = next;
  }
  return seen;
}

function updateInspector(node) {
  inspector.classList.toggle("is-empty", !node);
  if (!node) {
    inspector.innerHTML = `
      <span class="inspector-type">SELECT A NODE</span>
      <h2>探索知识连接</h2>
      <p>点击任意节点，查看它在指定深度内的知识邻域。</p>`;
    return;
  }
  const summary = node.summary || "No summary.";
  const summaryLimit = 190;
  const truncated = summary.length > summaryLimit
    ? `${summary.slice(0, summaryLimit).trimEnd()}...`
    : summary;
  const details = node.path
    ? `<a class="details-link" href="${pageBase}?path=${encodeURIComponent(node.path)}">show the details</a>`
    : "";
  const evidence = graphMode === "atomic" && Number.isFinite(node.evidenceCount)
    ? `<span class="path">${node.evidenceCount} evidence span${node.evidenceCount === 1 ? "" : "s"}</span>`
    : "";
  inspector.innerHTML = `
    <span class="inspector-type">${node.kind.toUpperCase()}${node.status ? ` · ${node.status.toUpperCase()}` : ""}</span>
    <h2>${escapeHtml(node.label)}</h2>
    <p>${escapeHtml(truncated)}${details ? ` ${details}` : ""}</p>
    ${node.path ? `<span class="path">${escapeHtml(node.path)}</span>` : ""}
    ${evidence}`;
}

function openPageOverlay(href, returnFocus) {
  window.clearTimeout(pageOverlayCloseTimer);
  pageOverlayReturnFocus = returnFocus || null;
  const url = new URL(href, window.location.href);
  url.searchParams.set("embedded", "graph");
  pageFrame.src = url.toString();
  pageOverlay.hidden = false;
  graphShell.inert = true;
  window.requestAnimationFrame(() => {
    pageOverlay.classList.add("is-open");
    pageFrame.focus();
  });
}

function closePageOverlay() {
  if (pageOverlay.hidden) return;
  pageOverlay.classList.remove("is-open");
  graphShell.inert = false;
  pageOverlayCloseTimer = window.setTimeout(() => {
    pageOverlay.hidden = true;
    pageFrame.src = "about:blank";
    pageOverlayReturnFocus?.focus();
    pageOverlayReturnFocus = null;
  }, 240);
}

inspector.addEventListener("click", (event) => {
  const link = event.target.closest(".details-link");
  if (!link) return;
  event.preventDefault();
  openPageOverlay(link.href, link);
});

window.addEventListener("message", (event) => {
  if (
    event.origin === window.location.origin
    && event.source === pageFrame.contentWindow
    && event.data?.type === "essaypod-wiki-close-page"
  ) {
    closePageOverlay();
  }
});

function escapeHtml(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span.innerHTML;
}

function selectNode(node) {
  selectedId = selectedId === node?.id ? null : node?.id || null;
  activeIds = activeNeighborhood(selectedId, Number(depthInput.value));
  updateInspector(selectedId ? graph.nodes.find((item) => item.id === selectedId) : null);
  updateVisualState();
}

function updateVisualState() {
  for (const [id, view] of nodeViews) {
    const active = !activeIds || activeIds.has(id);
    view.alpha = active || agentTrace.activeAgents.has(id) ? 1 : 0.14;
    view.scale.set(id === selectedId ? 1.3 : 1);
    applyAgentNodeState(id, view);
  }
  drawEdges();
}

function edgeKey(source, target) {
  return [String(source || ""), String(target || "")].sort().join("::");
}

function applyAgentNodeState(id, view) {
  if (!view.agentHalo || !view.agentRing) return;
  const radius = view.agentRadius;
  const activeAgent = agentTrace.activeAgents.get(id);
  const current = Boolean(activeAgent);
  let color = 0;
  let alpha = 0;
  let width = 0;
  if (current) { color = activeAgent.color; alpha = 0.98; width = 2.5; }
  else if (agentTrace.useful.has(id)) { color = 0x31a876; alpha = 0.9; width = 2; }
  else if (agentTrace.bridges.has(id)) { color = 0x8a74c9; alpha = 0.82; width = 1.8; }
  else if (agentTrace.deadEnds.has(id)) { color = 0xcc756d; alpha = 0.64; width = 1.5; }
  else if (agentTrace.frontier.has(id)) { color = 0x7891c6; alpha = 0.65; width = 1.35; }
  else if (agentTrace.visited.has(id)) { color = 0x889099; alpha = 0.44; width = 1.1; }
  view.agentRing.clear();
  view.agentHalo.clear();
  view.agentHalo.visible = current;
  if (current) {
    view.agentHalo
      .circle(0, 0, radius + 12)
      .fill({ color, alpha: 0.12 })
      .circle(0, 0, radius + 7)
      .stroke({ color, width: 1.25, alpha: 0.42 });
  }
  if (width) {
    view.agentRing.circle(0, 0, radius + 4.2).stroke({ color, width, alpha });
  }
  if (view.agentLabel) {
    view.agentLabel.alpha = agentTrace.deadEnds.has(id) ? 0.48 : 1;
  }
}

function applyAgentTraceState(state = {}) {
  const activeAgents = new Map();
  for (const agent of state.activeAgents || []) {
    if (!agent?.nodeId) continue;
    activeAgents.set(String(agent.nodeId), {
      ...agent,
      color: SWARM_COLORS[Math.abs(Number(agent.agentIndex) || 0) % SWARM_COLORS.length],
    });
  }
  if (!activeAgents.size && state.current) {
    activeAgents.set(String(state.current), { color: SWARM_COLORS[0] });
  }
  agentTrace = {
    activeAgents,
    frontier: new Set(state.frontier || []),
    visited: new Set(state.visited || []),
    useful: new Set(state.useful || []),
    bridges: new Set(state.bridges || []),
    deadEnds: new Set(state.deadEnds || []),
    pathEdges: new Set((state.pathEdges || []).map((edge) => edgeKey(edge[0], edge[1]))),
  };
  updateVisualState();
}

function drawEdges() {
  edgeLayer.clear();
  for (const link of graph.links) {
    const source = link.source;
    const target = link.target;
    if (!source || !target || typeof source !== "object" || typeof target !== "object") continue;
    const active = !activeIds || (activeIds.has(source.id) && activeIds.has(target.id));
    const traced = agentTrace.pathEdges.has(edgeKey(source.id, target.id));
    const color = traced ? 0x6c87c9 : active ? 0xa8adb2 : 0xd1d4d6;
    const alpha = traced ? 0.84 : active ? 0.56 : 0.16;
    edgeLayer
      .moveTo(source.x, source.y)
      .lineTo(target.x, target.y)
      .stroke({ color, width: traced ? 2.15 : active ? 1.05 : 0.65, alpha });

    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const length = Math.hypot(dx, dy);
    if (length > 28) {
      const ux = dx / length;
      const uy = dy / length;
      const tipX = target.x - ux * (nodeRadius(target) + 2);
      const tipY = target.y - uy * (nodeRadius(target) + 2);
      const wing = 3.2;
      const back = 7;
      edgeLayer
        .poly([
          tipX, tipY,
          tipX - ux * back - uy * wing, tipY - uy * back + ux * wing,
          tipX - ux * back + uy * wing, tipY - uy * back - ux * wing,
        ])
        .fill({ color, alpha });
    }
  }
}

function makeNodeView(node) {
  const container = new Container();
  const radius = nodeRadius(node);
  const color = COLORS[node.kind] || COLORS.page;
  const pending = node.status === "pending";
  const provisional = node.status === "web_bootstrap";
  const halo = new Graphics();
  halo.visible = false;
  const agentRing = new Graphics();
  const circle = new Graphics()
    .circle(0, 0, radius)
    .fill({ color, alpha: pending ? 0.08 : provisional ? 0.28 : 0.94 })
    .circle(0, 0, radius)
    .stroke({
      color: pending || provisional ? color : 0xffffff,
      width: pending || provisional ? 1.8 : 1.3,
      alpha: 0.95,
    });
  const label = new Text({
    text: node.label.length > 25 ? `${node.label.slice(0, 24)}…` : node.label,
    style: new TextStyle({
      fontFamily: "Inter, system-ui, sans-serif",
      fontSize: 10.5,
      fill: 0x555b61,
      fontWeight: "450",
      dropShadow: { color: 0xffffff, alpha: 0.96, blur: 2, distance: 0 },
    }),
  });
  label.anchor.set(0.5, 0);
  label.y = radius + 7;
  container.addChild(halo, agentRing, circle, label);
  container.agentHalo = halo;
  container.agentRing = agentRing;
  container.agentLabel = label;
  container.agentRadius = radius;
  container.eventMode = "static";
  container.cursor = "pointer";
  container.on("pointerdown", (event) => {
    event.stopPropagation();
    userMovedView = true;
    dragged = node;
    node.fx = node.x;
    node.fy = node.y;
    simulation.alphaTarget(0.25).restart();
  });
  container.on("pointertap", (event) => {
    event.stopPropagation();
    if (!dragged) selectNode(node);
  });
  container.on("pointerover", (event) => {
    tooltip.hidden = false;
    tooltip.textContent = `${node.kind} · ${node.label}`;
    tooltip.style.left = `${event.global.x + 14}px`;
    tooltip.style.top = `${event.global.y + 14}px`;
  });
  container.on("pointermove", (event) => {
    if (!tooltip.hidden) {
      tooltip.style.left = `${event.global.x + 14}px`;
      tooltip.style.top = `${event.global.y + 14}px`;
    }
  });
  container.on("pointerout", () => { tooltip.hidden = true; });
  applyAgentNodeState(node.id, container);
  return container;
}

function normalizeGraph(data, cachedPositions = null) {
  const nodes = (data.nodes || []).map((node, index) => ({
    ...node,
    x: Number.isFinite(cachedPositions?.[node.id]?.[0])
      ? cachedPositions[node.id][0]
      : Math.cos(index * 2.39996) * (18 + Math.sqrt(index) * 21),
    y: Number.isFinite(cachedPositions?.[node.id]?.[1])
      ? cachedPositions[node.id][1]
      : Math.sin(index * 2.39996) * (18 + Math.sqrt(index) * 21),
  }));
  const known = new Set(nodes.map((node) => node.id));
  const links = (data.links || []).filter((link) => known.has(link.source) && known.has(link.target));
  const degrees = new Map(nodes.map((node) => [node.id, 0]));
  for (const link of links) {
    degrees.set(link.source, degrees.get(link.source) + 1);
    degrees.set(link.target, degrees.get(link.target) + 1);
  }
  for (const node of nodes) node.degree = degrees.get(node.id);
  return { nodes, links };
}

function adaptiveLinkDistance(link) {
  const base = Number(distanceInput.value);
  const sourceDegree = link.source?.degree || 1;
  const targetDegree = link.target?.degree || 1;
  return Math.max(46, base - Math.sqrt(sourceDegree * targetDegree) * 3.2);
}

function fitGraph() {
  if (!graph.nodes.length) return;
  const xs = graph.nodes.map((node) => node.x || 0);
  const ys = graph.nodes.map((node) => node.y || 0);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const width = Math.max(100, maxX - minX);
  const height = Math.max(100, maxY - minY);
  const scale = Math.min(
    1.12,
    Math.max(0.2, (app.screen.width - 150) / width, 0),
    Math.max(0.2, (app.screen.height - 170) / height, 0),
  );
  world.scale.set(scale);
  world.position.set(
    app.screen.width / 2 - ((minX + maxX) / 2) * scale,
    app.screen.height / 2 - ((minY + maxY) / 2) * scale,
  );
}

function renderGraph(data, cachedPositions = null) {
  simulation?.stop();
  nodeLayer.removeChildren().forEach((child) => child.destroy({ children: true }));
  nodeViews = new Map();
  selectedId = null;
  activeIds = null;
  userMovedView = false;
  updateInspector(null);
  graphSourceData = serializableGraph(data);
  graph = normalizeGraph(data, cachedPositions);
  emptyState.hidden = graph.nodes.length > 0;
  stats.textContent = `${graph.nodes.length} nodes · ${graph.links.length} links`;
  for (const node of graph.nodes) {
    const view = makeNodeView(node);
    nodeViews.set(node.id, view);
    nodeLayer.addChild(view);
  }
  simulation = forceSimulation(graph.nodes)
    .force("link", forceLink(graph.links).id((node) => node.id).distance(adaptiveLinkDistance).strength(0.48))
    .force("charge", forceManyBody()
      .strength((node) => -150 - Math.min(290, (node.degree || 0) * 26))
      .distanceMax(900))
    .force("collision", forceCollide()
      .radius((node) => nodeRadius(node) + 14 + Math.min(34, node.label.length * 0.75))
      .strength(0.82))
    .force("center", forceCenter(0, 0))
    .alphaDecay(0.028)
    .on("tick", () => {
      drawEdges();
      for (const node of graph.nodes) {
        const view = nodeViews.get(node.id);
        view.position.set(node.x, node.y);
      }
      if (!userMovedView) fitGraph();
    })
    .on("end", () => {
      if (!userMovedView) fitGraph();
      writeGraphCache();
    });
  if (graph.nodes.length >= 24) {
    simulation.force(
      "radial",
      forceRadial(
        (node) => 105 + Math.max(0, 7 - (node.degree || 0)) * 38,
        0,
        0,
      ).strength((node) => (node.degree || 0) > 6 ? 0.085 : 0.035),
    );
  }
  resetView({ warm: Boolean(cachedPositions && Object.keys(cachedPositions).length) });
  updateVisualState();
}

app.ticker.add(() => {
  const phase = (Math.sin(performance.now() / 360) + 1) / 2;
  for (const nodeId of agentTrace.activeAgents.keys()) {
    const current = nodeViews.get(nodeId);
    if (!current?.agentHalo?.visible) continue;
    current.agentHalo.scale.set(0.94 + phase * 0.13);
    current.agentHalo.alpha = 0.58 + phase * 0.42;
  }
});

window.addEventListener("message", (event) => {
  if (event.source !== window.parent) return;
  if (event.data?.type === "essaypod-agent-trace-state") {
    applyAgentTraceState(event.data.state || {});
    return;
  }
  if (event.data?.type === "essaypod-graph-visibility") {
    parentGraphActive = Boolean(event.data.active);
    applyGraphVisibility();
  }
});

function resetView({ warm = false } = {}) {
  userMovedView = false;
  selectNode(null);
  simulation?.alpha(warm ? 0.12 : 0.75).restart();
  fitGraph();
}

app.stage.on("pointerdown", (event) => {
  if (event.target !== app.stage) return;
  userMovedView = true;
  panning = { x: event.global.x, y: event.global.y, worldX: world.x, worldY: world.y };
});
app.stage.on("globalpointermove", (event) => {
  if (dragged) {
    const local = world.toLocal(event.global);
    dragged.fx = local.x;
    dragged.fy = local.y;
    return;
  }
  if (panning) {
    world.x = panning.worldX + event.global.x - panning.x;
    world.y = panning.worldY + event.global.y - panning.y;
  }
});
app.stage.on("pointerup", () => {
  if (dragged) {
    dragged.fx = null;
    dragged.fy = null;
    simulation.alphaTarget(0);
  }
  dragged = null;
  panning = null;
});
app.stage.on("pointerupoutside", () => {
  dragged = null;
  panning = null;
  simulation?.alphaTarget(0);
});

app.canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  userMovedView = true;
  const rect = app.canvas.getBoundingClientRect();
  const pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
  const before = world.toLocal(pointer);
  const nextScale = Math.min(2.8, Math.max(0.28, world.scale.x * Math.exp(-event.deltaY * 0.0012)));
  world.scale.set(nextScale);
  const after = world.toGlobal(before);
  world.x += pointer.x - after.x;
  world.y += pointer.y - after.y;
}, { passive: false });

new ResizeObserver(() => {
  app.stage.hitArea = new Rectangle(0, 0, app.screen.width, app.screen.height);
}).observe(host);

depthInput.addEventListener("input", () => {
  depthValue.value = depthInput.value;
  activeIds = activeNeighborhood(selectedId, Number(depthInput.value));
  updateVisualState();
});
distanceInput.addEventListener("input", () => {
  distanceValue.value = distanceInput.value;
  simulation?.force("link").distance(adaptiveLinkDistance);
  simulation?.alpha(0.65).restart();
});
document.querySelector("#resetView").addEventListener("click", resetView);
for (const button of modeButtons) {
  button.addEventListener("click", () => switchGraphMode(button.dataset.graphMode));
}

function stopRefreshTimer() {
  if (!refreshTimer) return;
  window.clearInterval(refreshTimer);
  refreshTimer = 0;
}

function startRefreshTimer() {
  stopRefreshTimer();
  if (!graphVisible || autoRefreshMs < 1000) return;
  refreshTimer = window.setInterval(() => loadCurrentWiki(), autoRefreshMs);
}

function applyGraphVisibility() {
  const nextVisible = parentGraphActive && document.visibilityState !== "hidden";
  if (nextVisible === graphVisible) {
    if (nextVisible) startRefreshTimer();
    return;
  }
  graphVisible = nextVisible;
  if (!graphVisible) {
    stopRefreshTimer();
    requestController?.abort();
    simulation?.stop();
    app.ticker.stop();
    return;
  }
  app.ticker.start();
  loadCurrentWiki();
  startRefreshTimer();
}

async function loadCurrentWiki({ force = false } = {}) {
  if (!graphVisible) return;
  if (requestInFlight && !force) return requestInFlight;
  if (force) requestController?.abort();
  const controller = new AbortController();
  requestController = controller;
  const endpoint = graphMode === "atomic" ? atomicGraphEndpoint : graphEndpoint;
  const pending = (async () => {
    try {
      const response = await fetch(endpoint, { signal: controller.signal, cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Graph request failed.");
      const displayData = displayGraphPayload(data);
      const fingerprint = fingerprintGraph(displayData);
      if (fingerprint === graphFingerprint) return;
      graphFingerprint = fingerprint;
      renderGraph(displayData);
    } catch (error) {
      if (error?.name === "AbortError") return;
      if (graph.nodes.length) {
        stats.textContent = `${graph.nodes.length} nodes · ${graph.links.length} links · 同步暂缓`;
        return;
      }
      emptyState.hidden = false;
      emptyState.querySelector("strong").textContent = "暂时无法同步当前 Wiki";
      emptyState.querySelector("span").textContent = "保留已有缓存，重新打开图谱时会再次尝试。";
    } finally {
      if (requestController === controller) requestController = null;
    }
  })();
  requestInFlight = pending;
  try {
    await pending;
  } finally {
    if (requestInFlight === pending) requestInFlight = null;
  }
}

document.addEventListener("visibilitychange", applyGraphVisibility);
updateModeChrome();
showCachedGraph();
if (graphVisible) {
  loadCurrentWiki();
  startRefreshTimer();
} else {
  app.ticker.stop();
}
