"use client";

import { Component, DragEvent, PointerEvent, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ErrorInfo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { API_BASE, ApiTask, request, taskAudioUrl, taskCoverUrl, taskDvrPlaylistUrl, taskFigureUrl, taskLiveAudioUrl, taskPdfUrl, taskTranslationAssetUrl, taskTranslationDeeplPdfUrl, taskTranslationPdfUrl, taskTranslationReadingPdfUrl, taskVideoUrl, VideoLayout } from "./api";

type View = "summary" | "translation" | "studio";
type Surface = "paper" | "graph";
type TaskStatus = ApiTask["status"];
type RegionName = "title" | "key_points" | "page" | "figures";
type Region = { x: number; y: number; w: number; h: number };
type LayoutPreset = "recommended" | "template1" | "template2" | "template3" | null;
type StudioLayer = RegionName | "subtitle" | "background";
type TitleAlignment = "left" | "center" | "right";
type LayoutTemplate = { regions: Record<RegionName,Region>; subtitleY:number; subtitleSize:number; titleSize?:number; keyPointsSize?:number; titleShadow?:boolean; keyPointsShadow?:boolean; titleAlignment?:TitleAlignment; visiblePageCount?:number; figureDuration?:number; backgroundColor?:string; layerOrder:RegionName[] };
type TokenUsageGroup = { name:string; input_tokens:number; output_tokens:number; total_tokens:number; calls:number };
type TokenUsageSummary = { date:string; timezone:string; input_tokens:number; output_tokens:number; total_tokens:number; cached_tokens:number; actual_tokens:number; estimated_tokens:number; calls:number; all_actual:boolean; tracking_since:number; by_feature:TokenUsageGroup[]; by_provider:TokenUsageGroup[] };
type AgentNodeCard = { id:string; label:string; kind:string; status?:string; summary?:string; path?:string; score?:number };
type AgentEvidence = { node_id:string; node_title:string; node_path:string; claims:string[]; excerpt:string; sources:{title:string;path:string}[] };
type AgentMemory = { memory_id:string; type:"evidence"|"impression"|"rejected_path"; round:number; agent_id:string; agent_role:string; focus:string; node_id:string; node_title:string; node_path:string; claim:string; relation_to_query:string; relevance:number; confidence:number; citations:{title:string;path:string}[]; suggested_next_nodes:string[] };
type AgentAssignment = { agent_id:string; agent_index:number; role:string; role_label:string; focus:string; rationale:string; parent_node?:string; node:AgentNodeCard };
type AgentEvent = { seq:number; type:string; timestamp:number; message?:string; round?:number; phase?:string; agent_id?:string; agent_index?:number; agent_role?:string; role_id?:string; focus?:string; node?:AgentNodeCard; path?:string[]; role?:"evidence"|"bridge"|"dead_end"; thought?:string; useful_claims?:string[]; suggested_next_nodes?:string[]; frontier?:string[]; visited?:string[]; dead_ends?:string[]; seeds?:AgentNodeCard[]; assignments?:AgentAssignment[]; answer?:string; evidence?:AgentEvidence[]; memory?:AgentMemory[]|AgentMemory; memory_total?:number; known?:string[]; uncertain?:string[]; unknown?:string[]; shared_memory?:AgentMemory[]; research_state?:{known:string[];uncertain:string[];unknown:string[]}; from_node?:string; to_node?:string };
type ActiveSwarmAgent = { agentId:string; agentIndex:number; nodeId:string; role:string; round:number };
type AgentTrace = { sessionKey:number; activeAgents:ActiveSwarmAgent[]; frontier:string[]; visited:string[]; useful:string[]; bridges:string[]; deadEnds:string[]; pathEdges:string[][] };

const emptyAgentTrace=():AgentTrace=>({sessionKey:Date.now(),activeAgents:[],frontier:[],visited:[],useful:[],bridges:[],deadEnds:[],pathEdges:[]});
const unique=(items:string[])=>Array.from(new Set(items.filter(Boolean)));
function reduceAgentTrace(state:AgentTrace,event:AgentEvent):AgentTrace {
  if(event.type==="session_started")return emptyAgentTrace();
  if(event.type==="catalog_scanned")return {...state,frontier:unique(event.frontier||event.seeds?.map(item=>item.id)||[])};
  if(event.type==="director_plan")return {...state,frontier:unique([...(event.assignments||[]).map(item=>item.node.id),...state.frontier])};
  if(event.type==="worker_started"){
    const path=event.path||[];
    const active={agentId:event.agent_id||"",agentIndex:event.agent_index||0,nodeId:event.node?.id||"",role:event.agent_role||"",round:event.round||1};
    return {...state,activeAgents:[...state.activeAgents.filter(item=>item.agentId!==active.agentId),active],frontier:state.frontier.filter(id=>id!==event.node?.id),pathEdges:path.length>1?uniqueEdges([...state.pathEdges,[path[path.length-2],path[path.length-1]]]):state.pathEdges};
  }
  if(event.type==="worker_completed"){
    const id=event.node?.id||"";
    return {...state,activeAgents:state.activeAgents.filter(item=>item.agentId!==event.agent_id),visited:unique([...state.visited,id]),useful:event.role==="evidence"?unique([...state.useful,id]):state.useful,bridges:event.role==="bridge"?unique([...state.bridges,id]):state.bridges,deadEnds:event.role==="dead_end"?unique([...state.deadEnds,id]):state.deadEnds};
  }
  if(event.type==="worker_failed")return {...state,activeAgents:state.activeAgents.filter(item=>item.agentId!==event.agent_id)};
  if(event.type==="frontier_updated")return {...state,frontier:unique(event.frontier||[]),visited:unique(event.visited||state.visited),deadEnds:unique(event.dead_ends||state.deadEnds)};
  if(["completed","failed","cancelled"].includes(event.type))return {...state,activeAgents:[]};
  return state;
}
function uniqueEdges(edges:string[][]){const seen=new Set<string>();return edges.filter(edge=>{const key=[...edge].sort().join("::");if(!edge[0]||!edge[1]||seen.has(key))return false;seen.add(key);return true})}

const tokenFeatureLabels:Record<string,string>={paper_condense:"论文浓缩",podcast_generation:"播客生成",reviewer_brief:"审稿导读",paper_chat_routing:"问答规划",paper_chat_rerank:"证据重排",paper_chat_answer:"论文问答",video_key_points:"视频要点",social_copy:"发布文案",wiki_compile:"Wiki 编译",wiki_query:"Wiki 查询",wiki_web_research:"网页补全"};
const formatTokens=(value:number)=>value>=1_000_000?`${(value/1_000_000).toFixed(value>=10_000_000?0:1)}M`:value>=1_000?`${(value/1_000).toFixed(value>=10_000?0:1)}K`:String(value||0);

const statusText: Record<TaskStatus, string> = {
  downloading: "正在下载论文", uploaded: "等待生成", ready: "播客已完成", generating: "正在生成播客", pausing: "正在暂停", paused: "已暂停", failed: "需要处理",
};
const taskStatusText=(task:ApiTask)=>task.status==="generating"&&task.podcast_stage==="queued"
  ? "等待播客生成队列"
  : statusText[task.status];

const wikiStageText:Record<string,string>={queued:"图谱排队",initializing:"图谱初始化",yielding_web_research:"图谱准备中",saving_source:"校验论文",extracting_text:"提取正文",requesting_llm:"编译知识",compiling_pages:"编译 Wiki 页面",extracting_foundations:"提取基础知识",extracting_findings:"提取实验与结论",writing_pages:"写入页面",writing_knowledge:"写入知识",rebuilding_index:"更新图谱",compiled:"图谱整理",linting:"图谱校验",scheduling_web_research:"安排网页补全",completed:"图谱已更新",interrupted:"图谱待恢复",failed:"图谱失败"};
const wikiTaskLabel=(wiki:ApiTask["llm_wiki"])=>wiki?wikiStageText[wiki.stage||wiki.status]||"图谱处理中":"";

const defaults: Record<RegionName, Region> = {
  title: { x: 28, y: 26, w: 800, h: 174 },
  key_points: { x: 32, y: 201, w: 590, h: 790 },
  page: { x: 528, y: 0, w: 1392, h: 1080 },
  figures: { x: 595, y: 199, w: 1282, h: 824 },
};

const regionLabels: Record<RegionName, string> = {
  title: "论文标题", key_points: "记忆板书", page: "PDF 页面", figures: "Figure 图表",
};

function Logo() {
  return <div className="logo-mark" aria-label="EssayPod"><span className="logo-network" aria-hidden><i/><i/><i/><b/></span></div>;
}

function MiniWave({ active = false }: { active?: boolean }) {
  return <div className={`mini-wave ${active ? "active" : ""}`} aria-hidden>{[10,18,26,14,22,30,18,11,24,16,28,13].map((h,i)=><i key={i} style={{height:h}} />)}</div>;
}

function RetryIcon() {
  return <svg className="retry-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M18.7 7.8A8 8 0 1 0 20 12"/><path d="M18.7 3.8v4h-4"/></svg>;
}

const playbackPositionKey=(taskId:string)=>`essaypod-playback-position:${taskId}`;

function preferredAudioSource(task:ApiTask,nativeHls:boolean) {
  if(task.audio_ready)return taskAudioUrl(task.id);
  const generating=task.status==="generating"||task.status==="pausing";
  if(!generating)return undefined;
  if(nativeHls&&task.live_dvr?.ready)return `${taskDvrPlaylistUrl(task.id)}?session=${Math.floor(task.updated_at)}`;
  if(task.partial_ready)return `${taskLiveAudioUrl(task.id)}?v=${task.updated_at}`;
  return undefined;
}

type WorkspaceErrorBoundaryProps={children:ReactNode;resetKey:string;onRecover:()=>void};
type WorkspaceErrorBoundaryState={failed:boolean};

class WorkspaceErrorBoundary extends Component<WorkspaceErrorBoundaryProps,WorkspaceErrorBoundaryState> {
  state:WorkspaceErrorBoundaryState={failed:false};
  static getDerivedStateFromError():WorkspaceErrorBoundaryState{return {failed:true}}
  componentDidCatch(error:Error,info:ErrorInfo){console.error("EssayPod workspace render failed",error,info.componentStack)}
  componentDidUpdate(previous:WorkspaceErrorBoundaryProps){
    if(this.state.failed&&previous.resetKey!==this.props.resetKey)this.setState({failed:false});
  }
  render(){
    if(!this.state.failed)return this.props.children;
    return <section className="workspace workspace-error" role="alert">
      <div><span>PROJECT RECOVERY</span><h2>这个旧项目暂时无法显示</h2><p>项目文件仍然保留。你可以返回其他项目，EssayPod 不会再因为单个项目异常而整页白屏。</p><button onClick={this.props.onRecover}>返回最近项目</button></div>
    </section>;
  }
}

export default function Home() {
  const [view, setView] = useState<View>("summary");
  const [surface, setSurface] = useState<Surface>("paper");
  const [tasks, setTasks] = useState<ApiTask[]>([]);
  const [selectedTask, setSelectedTask] = useState<string>("");
  const [query, setQuery] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [format, setFormat] = useState<"16:9" | "9:16">("16:9");
  const [error, setError] = useState("");
  const [regions, setRegions] = useState(defaults);
  const [selectedRegion, setSelectedRegion] = useState<RegionName>("page");
  const [dirty, setDirty] = useState(false);
  const [sidebarCollapsed,setSidebarCollapsed] = useState(false);
  const [graphMounted,setGraphMounted] = useState(false);
  const [panelMinHeight,setPanelMinHeight] = useState(0);
  const [accountMenuOpen,setAccountMenuOpen] = useState(false);
  const [tokenUsage,setTokenUsage] = useState<TokenUsageSummary|null>(null);
  const [tokenUsageError,setTokenUsageError] = useState("");
  const [agentPanelOpen,setAgentPanelOpen] = useState(false);
  const [agentTrace,setAgentTrace] = useState<AgentTrace>(emptyAgentTrace);
  const contentScrollRef = useRef<HTMLDivElement>(null);
  const activePanelRef = useRef<HTMLDivElement>(null);
  const pendingScrollTopRef = useRef<number|null>(null);
  const accountMenuRef = useRef<HTMLDivElement>(null);
  const graphFrameRef = useRef<HTMLIFrameElement>(null);
  const currentTask = tasks.find(task => task.id === selectedTask) || tasks[0];
  const filteredTasks = tasks.filter(t => (t.display_title || t.title).toLowerCase().includes(query.toLowerCase()));
  const showingImport = Boolean(
    currentTask && !currentTask.pdf_ready && currentTask.source_url &&
    (currentTask.status === "downloading" || currentTask.status === "failed")
  );

  const refreshTasks = async () => {
    try {
      const next = await request<ApiTask[]>("/api/tasks");
      setTasks(next);
      setSelectedTask(current => current && next.some(t=>t.id===current) ? current : (next[0]?.id || ""));
      setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "无法连接后端"); }
  };

  useEffect(() => { refreshTasks(); }, []);
  useEffect(()=>{setSidebarCollapsed(window.localStorage.getItem("essaypod-sidebar-collapsed")==="1")},[]);
  useEffect(()=>{
    // The macOS wrapper places a native drag surface over the quiet part of the
    // sidebar title rail. Hide it while the sidebar is collapsed so the reveal
    // button in the workspace header receives the click; restore it on expand.
    (window as Window & {webkit?: {messageHandlers?: {sidebarState?: {postMessage: (value: {collapsed: boolean}) => void}}}})
      .webkit?.messageHandlers?.sidebarState?.postMessage({collapsed: sidebarCollapsed});
  },[sidebarCollapsed]);
  useEffect(()=>{
    // The Agent toggle remains at the workspace's right edge. When the panel
    // opens that edge moves left, so shorten the native header drag surface as
    // well and keep a generous click-safe gap around the fixed toggle.
    (window as Window & {webkit?: {messageHandlers?: {agentPanelState?: {postMessage: (value: {open: boolean}) => void}}}})
      .webkit?.messageHandlers?.agentPanelState?.postMessage({open: agentPanelOpen});
  },[agentPanelOpen]);
  useEffect(() => {
    if (!tasks.some(t => t.status === "downloading" || t.status === "generating" || t.status === "pausing" || t.brief?.status === "generating" || t.translation?.status === "generating" || ["queued","generating"].includes(t.translation?.deepl_pdf_status || "") || ["queued","running","interrupted"].includes(t.llm_wiki?.status || "") || Object.values(t.video || {}).some(v=>v.status==="generating"))) return;
    const timer = window.setInterval(refreshTasks, 2500);
    return () => window.clearInterval(timer);
  }, [tasks]);
  useEffect(()=>{
    if(!accountMenuOpen)return;
    let active=true;
    const load=()=>request<TokenUsageSummary>("/api/token-usage/today").then(data=>{if(active){setTokenUsage(data);setTokenUsageError("")}}).catch(cause=>{if(active)setTokenUsageError(cause instanceof Error?cause.message:"无法读取用量")});
    load();
    const timer=window.setInterval(load,15000);
    const close=(event:globalThis.PointerEvent)=>{if(accountMenuRef.current&&!accountMenuRef.current.contains(event.target as Node))setAccountMenuOpen(false)};
    const escape=(event:KeyboardEvent)=>{if(event.key==="Escape")setAccountMenuOpen(false)};
    document.addEventListener("pointerdown",close);
    document.addEventListener("keydown",escape);
    return()=>{active=false;window.clearInterval(timer);document.removeEventListener("pointerdown",close);document.removeEventListener("keydown",escape)};
  },[accountMenuOpen]);

  useLayoutEffect(()=>{
    if(pendingScrollTopRef.current===null||!contentScrollRef.current)return;
    const target=pendingScrollTopRef.current;
    contentScrollRef.current.scrollTop=target;
    const frame=window.requestAnimationFrame(()=>{
      if(contentScrollRef.current)contentScrollRef.current.scrollTop=target;
      pendingScrollTopRef.current=null;
    });
    return()=>window.cancelAnimationFrame(frame);
  },[view]);

  const switchView=(next:View)=>{if(next===view)return;pendingScrollTopRef.current=contentScrollRef.current?.scrollTop??0;setPanelMinHeight(next==="translation"?0:Math.max(panelMinHeight,activePanelRef.current?.offsetHeight??0));setView(next)};

  const chooseTask = (id: string) => { setSurface("paper"); setSelectedTask(id); setPanelMinHeight(0); setView("summary"); };
  const toggleSidebar=()=>setSidebarCollapsed(current=>{const next=!current;window.localStorage.setItem("essaypod-sidebar-collapsed",next?"1":"0");return next});
  const deleteTask = async (task: ApiTask) => {
    const title = task.display_title || task.title;
    if (!window.confirm(`确定删除「${title}」吗？\n\nPDF、音频、逐字稿和视频文件都会被永久删除。`)) return;
    try {
      await request(`/api/tasks/${task.id}`, { method: "DELETE" });
      if (selectedTask === task.id) setSelectedTask("");
      await refreshTasks();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "删除失败"); }
  };
  const recoverWorkspace=()=>{
    const fallback=tasks.find(task=>task.id!==selectedTask);
    setSurface("paper");setView("summary");setPanelMinHeight(0);setSelectedTask(fallback?.id||"");
  };
  const sendGraphTrace=(trace:AgentTrace=agentTrace)=>{
    try{graphFrameRef.current?.contentWindow?.postMessage({type:"essaypod-agent-trace-state",state:trace},new URL(API_BASE).origin)}catch{}
  };
  const handleAgentEvent=(event:AgentEvent)=>setAgentTrace(current=>{const next=reduceAgentTrace(current,event);window.setTimeout(()=>sendGraphTrace(next),0);return next});
  useEffect(()=>{if(surface==="graph")sendGraphTrace()},[surface,agentTrace.sessionKey]);

  return (
    <main className={`app-shell ${sidebarCollapsed?"sidebar-collapsed":""} ${agentPanelOpen?"agent-panel-open":""}`}>
      <aside className="sidebar" aria-hidden={sidebarCollapsed}>
        <div className="window-drag-rail" data-window-drag aria-hidden />
        <header className="brand"><Logo /><div><b>EssayPod</b><span>AI Research Workspace</span></div></header>
        <nav className="sidebar-nav" aria-label="主要功能">
          <button className="new-project" onClick={()=>{setSurface("paper");setShowNew(true)}}>
            <span className="new-project-icon" aria-hidden>＋</span><b>导入新论文</b><kbd>⌘ N</kbd>
          </button>
          <button className={`knowledge-nav ${surface==="graph"?"active":""}`} onClick={()=>{setGraphMounted(true);setSurface("graph")}} aria-pressed={surface==="graph"}>
            <span className="knowledge-nav-icon" aria-hidden><i/><i/><i/></span>
            <span><b>知识图谱</b><small>Knowledge Graph</small></span>
          </button>
        </nav>
        <section className="sidebar-library" aria-label="论文项目库">
          <label className="search"><span aria-hidden>⌕</span><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索论文" /></label>
          <div className="library-head"><span>{query ? "搜索结果" : "最近论文"}</span><small>{filteredTasks.length}</small></div>
          <nav className="task-list" aria-label="论文列表">
            {filteredTasks.map(task => <div key={task.id} role="button" tabIndex={0} onClick={()=>chooseTask(task.id)} onKeyDown={e=>{if(e.key==="Enter"||e.key===" ")chooseTask(task.id)}} className={`task ${surface==="paper"&&selectedTask===task.id ? "selected" : ""}`}>
              <div className="task-main"><span className={`status-dot ${task.status}`} /><strong title={task.display_title || task.title}>{task.display_title || task.title}</strong><time>{new Date(task.updated_at*1000).toLocaleDateString("zh-CN",{month:"numeric",day:"numeric"})}</time></div>
              <div className="task-meta"><span>{task.source_url ? "链接" : "PDF"}</span><i/><span>{taskStatusText(task)}</span>{(task.status==="downloading"||task.status==="generating") && <MiniWave active />}{task.llm_wiki&&["queued","running","interrupted","failed"].includes(task.llm_wiki.status)&&<><i/><span title={task.llm_wiki.message||task.llm_wiki.error||""}>{wikiTaskLabel(task.llm_wiki)}{typeof task.llm_wiki.progress==="number"&&task.llm_wiki.status==="running"?` ${task.llm_wiki.progress}%`:""}</span>{["queued","running"].includes(task.llm_wiki.status)&&<MiniWave active />}</>}</div>
              <button className="task-delete" title="删除论文" aria-label={`删除 ${task.display_title || task.title}`} onClick={e=>{e.stopPropagation();deleteTask(task)}}>×</button>
            </div>)}
            {!filteredTasks.length && <div className="task-empty"><b>没有找到论文</b><span>试试其他关键词</span></div>}
          </nav>
        </section>
        <footer className="sidebar-foot" ref={accountMenuRef}>
          <div className="avatar">Q</div><div className="workspace-identity"><b>个人工作区</b><span>本地运行 · 数据私有</span></div>
          <button className="account-menu-trigger" aria-label="打开工作区菜单" aria-haspopup="menu" aria-expanded={accountMenuOpen} onClick={()=>setAccountMenuOpen(value=>!value)}><i/><i/><i/></button>
          {accountMenuOpen&&<section className="account-menu" role="menu" aria-label="工作区菜单">
            <header><div className="account-menu-avatar">Q</div><div><b>个人工作区</b><span>EssayPod Local</span></div></header>
            <article className="token-usage-card">
              <div className="token-usage-head"><span>今日 Token 用量</span>{tokenUsage&&tokenUsage.calls>0&&<small className={tokenUsage.all_actual?"actual":"estimated"}>{tokenUsage.all_actual?"API 实际":"含估算"}</small>}</div>
              {tokenUsage?<><div className="token-usage-total"><strong>{formatTokens(tokenUsage.total_tokens)}</strong><span>tokens · {tokenUsage.calls} 次调用</span></div><div className="token-usage-split"><span><i/>输入 <b>{formatTokens(tokenUsage.input_tokens)}</b></span><span><i/>输出 <b>{formatTokens(tokenUsage.output_tokens)}</b></span></div>{tokenUsage.by_feature.length>0&&<div className="token-feature-list">{tokenUsage.by_feature.slice(0,3).map(item=><div key={item.name}><span>{tokenFeatureLabels[item.name]||item.name}</span><b>{formatTokens(item.total_tokens)}</b></div>)}</div>}</>:tokenUsageError?<p className="token-usage-error">{tokenUsageError}</p>:<div className="token-usage-loading"><i/><i/></div>}
            </article>
            <div className="account-menu-items"><button role="menuitem" disabled><span>用量历史</span><small>即将推出</small></button><button role="menuitem" disabled><span>模型与提供商</span><small>即将推出</small></button><button role="menuitem" disabled><span>工作区偏好</span><small>即将推出</small></button></div>
            <footer>统计从启用 Token 账本后开始</footer>
          </section>}
        </footer>
      </aside>

      <WorkspaceErrorBoundary resetKey={`${surface}:${selectedTask}`} onRecover={recoverWorkspace}>
      <section className="workspace">
        <header className="topbar" data-window-drag>
          <button className="sidebar-toggle" onClick={toggleSidebar} aria-label={sidebarCollapsed?"展开项目库":"收起项目库"} title={sidebarCollapsed?"展开项目库":"收起项目库"}><i/><span>{sidebarCollapsed?"展开项目库":"收起项目库"}</span></button>
          <div className="crumb">{surface==="graph"?<><span>知识库</span><b>/</b><strong>Knowledge Graph</strong></>:<><span>项目库</span><b>/</b><strong>{currentTask?.display_title || currentTask?.title || "暂无论文"}</strong></>}</div>
          <button className={`agent-panel-trigger ${agentPanelOpen?"active":""}`} data-window-no-drag onClick={()=>setAgentPanelOpen(value=>!value)} aria-label={agentPanelOpen?"关闭研究 Agent":"打开研究 Agent"} aria-expanded={agentPanelOpen} title={agentPanelOpen?"关闭 Research Agent":"打开 Research Agent"}><span className="agent-panel-icon" aria-hidden><i/></span></button>
        </header>

        {graphMounted&&<KnowledgeGraphFrame active={surface==="graph"} frameRef={graphFrameRef} onLoad={()=>sendGraphTrace()}/>} 
        {surface!=="graph"&&
          <div className="content-scroll" ref={contentScrollRef}>
            <div className={`content view-${view}`}>
              {error && <div className="api-error">后端连接失败：{error}</div>}
              {currentTask ? showingImport ? <UrlImportProgress task={currentTask} onRefresh={refreshTasks} /> : <PersistentPodcastHero task={currentTask} playing={playing} setPlaying={setPlaying} onRefresh={refreshTasks} /> : <div className="empty-real"><h2>还没有论文</h2><p>从左侧导入 PDF 开始。</p></div>}

              {!showingImport && <nav className="view-tabs">
                {([['summary','摘要'],['translation','论文翻译'],['studio','视频工作室']] as [View,string][]).map(([id,label])=><button key={id} onClick={()=>switchView(id)} className={view===id?"active":""}>{label}</button>)}
              </nav>}

              {currentTask && !showingImport && view === "summary" && <div className="summary-page view-panel" ref={activePanelRef} style={{minHeight:panelMinHeight||undefined}}><PaperSummary task={currentTask} /></div>}
              {currentTask && !showingImport && view === "translation" && <div className="view-panel" ref={activePanelRef}><PaperTranslation task={currentTask} /></div>}
              {currentTask && !showingImport && view === "studio" && <div className="view-panel" ref={activePanelRef} style={{minHeight:panelMinHeight||undefined}}><Studio task={currentTask} format={format} setFormat={setFormat} regions={regions} setRegions={setRegions} selected={selectedRegion} setSelected={setSelectedRegion} dirty={dirty} setDirty={setDirty} onRefresh={refreshTasks} /></div>}
            </div>
          </div>
        }
      </section>
      </WorkspaceErrorBoundary>
      <AgentPanel open={agentPanelOpen} onEvent={handleAgentEvent}/>
      {showNew && <ImportModal onClose={()=>setShowNew(false)} onCreated={async task=>{setShowNew(false);setSurface("paper");await refreshTasks();setSelectedTask(task.id)}} />}
    </main>
  );
}

function KnowledgeGraphFrame({active,frameRef,onLoad}:{active:boolean;frameRef:React.RefObject<HTMLIFrameElement|null>;onLoad:()=>void}) {
  const notifyVisibility=()=>{
    try{frameRef.current?.contentWindow?.postMessage({type:"essaypod-graph-visibility",active},new URL(API_BASE).origin)}catch{}
  };
  useEffect(()=>{notifyVisibility()},[active]);
  return <div className={`knowledge-graph-shell ${active?"is-active":"is-cached"}`} aria-hidden={!active}>
    <iframe ref={frameRef} onLoad={()=>{notifyVisibility();onLoad()}} className="knowledge-graph-frame" src={`${API_BASE}/api/wiki/graph-view`} title="EssayPod Knowledge Graph" tabIndex={active?0:-1} />
  </div>;
}

function AgentPanel({open,onEvent}:{open:boolean;onEvent:(event:AgentEvent)=>void}) {
  const [query,setQuery]=useState("");
  const [events,setEvents]=useState<AgentEvent[]>([]);
  const [sessionId,setSessionId]=useState("");
  const [status,setStatus]=useState<"idle"|"starting"|"running"|"completed"|"failed"|"cancelled">("idle");
  const [error,setError]=useState("");
  const sourceRef=useRef<EventSource|null>(null);
  const streamRef=useRef<HTMLDivElement>(null);
  const running=status==="starting"||status==="running";
  const latestThought=[...events].reverse().find(event=>event.thought||event.message);
  const completed=[...events].reverse().find(event=>event.type==="completed");
  const activeMap=new Map<string,AgentEvent>();
  events.forEach(event=>{if(event.type==="worker_started"&&event.agent_id)activeMap.set(event.agent_id,event);if(["worker_completed","worker_failed"].includes(event.type)&&event.agent_id)activeMap.delete(event.agent_id)});
  const activeWorkers=Array.from(activeMap.values());
  const memories=events.flatMap(event=>event.type==="shared_memory_updated"&&Array.isArray(event.memory)?event.memory:[]);
  const latestReview=[...events].reverse().find(event=>event.type==="director_review");
  useEffect(()=>()=>sourceRef.current?.close(),[]);
  useEffect(()=>{if(streamRef.current)streamRef.current.scrollTo({top:streamRef.current.scrollHeight,behavior:"smooth"})},[events.length]);
  const connect=(id:string)=>{
    sourceRef.current?.close();
    const source=new EventSource(`${API_BASE}/api/agentic-retrieval/sessions/${id}/events`);
    sourceRef.current=source;
    source.onmessage=message=>{
      const event=JSON.parse(message.data) as AgentEvent;
      setEvents(current=>current.some(item=>item.seq===event.seq)?current:[...current,event]);
      onEvent(event);
      if(event.type==="session_started")setStatus("running");
      if(event.type==="completed"){setStatus("completed");source.close()}
      if(event.type==="failed"){setStatus("failed");setError(event.message||"检索失败");source.close()}
      if(event.type==="cancelled"){setStatus("cancelled");source.close()}
    };
    source.onerror=()=>{if(source.readyState===EventSource.CLOSED&&running)setError("动作流连接中断，可以重新发起检索。")};
  };
  const submit=async()=>{
    const question=query.trim();if(!question||running)return;
    setEvents([]);setError("");setStatus("starting");setSessionId("");
    try{
      const session=await request<{session_id:string}>("/api/agentic-retrieval/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query:question,max_nodes:10,max_depth:2})});
      setSessionId(session.session_id);connect(session.session_id);
    }catch(cause){setStatus("failed");setError(cause instanceof Error?cause.message:"无法启动检索")}
  };
  const cancel=async()=>{if(!sessionId)return;try{await request(`/api/agentic-retrieval/sessions/${sessionId}/cancel`,{method:"POST"})}catch{}}
  const actionEvents=events.filter(event=>!["session_started","frontier_updated","notice","worker_started"].includes(event.type));
  return <aside className={`agent-panel ${open?"is-open":""}`} aria-hidden={!open}>
    <header><div className="agent-orb swarm" aria-hidden><i/><i/><b/><b/><b/></div><div><span>TWO-ROUND RESEARCH SWARM</span><h2>Research Swarm</h2></div></header>
    <section className="agent-query-box"><textarea value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{if((event.metaKey||event.ctrlKey)&&event.key==="Enter")submit()}} placeholder="描述研究方向、问题或 Proposal 目标…" rows={3}/><footer><span>⌘ ↵ 运行 · 5 Workers × 2 rounds</span>{running?<button className="agent-stop" onClick={cancel}>停止</button>:<button onClick={submit} disabled={!query.trim()}>启动蜂群 <i>→</i></button>}</footer></section>
    {events.length===0&&!running?<section className="agent-empty"><div><i/><i/><i/></div><h3>把知识图谱交给研究团队</h3><p>Director 先选种子，角色化 Worker 并行探索；所有印象进入共享黑板，再进行第二轮验证。</p><ul><li>两轮并行蜂群</li><li>共享黑板记忆</li><li>来源精准引用</li></ul></section>:<>
      <section className={`agent-current swarm-current ${running?"is-live":""}`}>
        <span>{activeWorkers.length?`ROUND ${activeWorkers[0].round||1} · ${activeWorkers.length} WORKERS ACTIVE`:running?"DIRECTOR WORKING":status==="completed"?"SWARM COMPLETE":"SESSION"}</span>
        {activeWorkers.length?<div className="agent-swarm-grid">{activeWorkers.map(worker=><article key={worker.agent_id} style={{"--agent-color":`var(--swarm-${(worker.agent_index||0)%5})`} as CSSProperties}><i/><section><b>{worker.agent_role}</b><small>{worker.node?.label}</small></section></article>)}</div>:<div><i/><b>{status==="starting"?"Director 正在阅读目录…":latestThought?.message||"整理共享记忆"}</b></div>}
        <p>{latestThought?.thought||latestThought?.message||"正在等待下一步动作。"}</p>
      </section>
      {latestReview&&<section className="agent-research-state"><header><span>DIRECTOR GAP MAP</span><b>已知 {latestReview.known?.length||0} · 待确认 {latestReview.uncertain?.length||0} · 未知 {latestReview.unknown?.length||0}</b></header><div>{Boolean(latestReview.known?.length)&&<p><i className="known"/>已形成证据</p>}{Boolean(latestReview.uncertain?.length)&&<p><i className="uncertain"/>需要交叉确认</p>}{Boolean(latestReview.unknown?.length)&&<p><i className="unknown"/>尚未覆盖</p>}</div></section>}
      <div className="agent-stream" ref={streamRef}>
        <div className="agent-stream-head"><span>蜂群活动</span><small>{actionEvents.filter(event=>event.type==="worker_completed").length} 个节点 · {memories.length} 条记忆</small></div>
        {actionEvents.map(event=><AgentAction key={event.seq} event={event}/>) }
        {error&&<p className="agent-error">{error}</p>}
        {completed?.answer&&<article className="agent-answer"><span>RESEARCH SYNTHESIS</span><ReactMarkdown remarkPlugins={[remarkGfm]} components={{a:({href,children})=><a href={href?.startsWith("/")?`${API_BASE}${href}`:href} target="_blank" rel="noreferrer">{children}</a>}}>{completed.answer}</ReactMarkdown>{Boolean(completed.shared_memory?.length)&&<details><summary>共享黑板 · {completed.shared_memory!.length} 条记忆</summary>{completed.shared_memory!.map(item=><div key={item.memory_id} className={`memory-${item.type}`}><b>{item.agent_role} · {item.node_title}</b><p>{item.claim}</p><small>{Math.round(item.confidence*100)}% confidence · Round {item.round}</small></div>)}</details>}{Boolean(completed.evidence?.length)&&<details><summary>来源证据 · {completed.evidence!.length}</summary>{completed.evidence!.map((item,index)=><div key={`${item.node_id}-${index}`}><b>{item.node_title}</b><p>{item.claims.join("；")}</p>{(item.sources||[]).map(source=><a key={source.path} href={`${API_BASE}/api/wiki/page-view?path=${encodeURIComponent(source.path)}`} target="_blank" rel="noreferrer">{source.title} ↗</a>)}</div>)}</details>}</article>}
      </div>
    </>}
  </aside>;
}

function AgentAction({event}:{event:AgentEvent}) {
  const config:Record<string,{label:string;icon:string}>={catalog_scanned:{label:"Director 阅读目录",icon:"⌕"},director_plan:{label:event.round===2?"第二轮任务规划":"种子组合规划",icon:"◎"},round_started:{label:`Round ${event.round} 并行启动`,icon:"⎈"},worker_completed:{label:event.role==="dead_end"?"Worker 终止路径":event.role==="bridge"?"Worker 发现桥梁":"Worker 提交证据",icon:event.role==="dead_end"?"×":event.role==="bridge"?"◇":"✓"},worker_failed:{label:"Worker 中断",icon:"!"},shared_memory_updated:{label:"共享黑板更新",icon:"▦"},round_completed:{label:`Round ${event.round} 完成`,icon:"◉"},director_review:{label:"Director 缺口分析",icon:"∆"},round_skipped:{label:"提前收束",icon:"–"},synthesis_started:{label:"研究综合",icon:"∑"},completed:{label:"蜂群完成",icon:"✓"},failed:{label:"检索中断",icon:"!"},cancelled:{label:"已停止",icon:"■"}};
  const item=config[event.type]||{label:"蜂群动作",icon:"·"};
  return <div className={`agent-action action-${event.role||event.type}`}><i>{item.icon}</i><div><span>{event.agent_role?`${event.agent_role} · ${item.label}`:item.label}<time>{new Date(event.timestamp*1000).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",second:"2-digit"})}</time></span>{event.node&&<b>{event.node.label}</b>}<p>{event.thought||event.message}</p>{Boolean(event.useful_claims?.length)&&<ul>{event.useful_claims!.map((claim,index)=><li key={index}>{claim}</li>)}</ul>}{event.type==="director_review"&&<ul>{event.unknown?.slice(0,2).map((item,index)=><li key={index}>未知：{item}</li>)}</ul>}</div></div>;
}

function UrlImportProgress({task,onRefresh}:{task:ApiTask;onRefresh:()=>Promise<void>}) {
  const [retrying,setRetrying]=useState(false);
  const stages=[
    ["queued","创建任务"],
    ["resolving","解析论文链接"],
    ["downloading","下载并验证 PDF"],
    ["processing","启动后台处理"],
  ] as const;
  const current=Math.max(0,stages.findIndex(([id])=>id===task.import_stage));
  const failed=task.status==="failed";
  const retry=async()=>{if(retrying)return;setRetrying(true);try{await request(`/api/tasks/${task.id}/download/retry`,{method:"POST"});await onRefresh()}finally{setRetrying(false)}};
  return <section className={`url-import-progress ${failed?"failed":""}`}>
    <span className="section-kicker">PAPER IMPORT</span>
    <h1>{task.display_title||task.title}</h1>
    <p>{failed?(task.error||"论文下载失败"):task.import_message||"正在准备论文"}</p>
    <div className="url-import-steps">{stages.map(([id,label],index)=><div key={id} className={`${index<current?"complete":""} ${index===current&&!failed?"active":""}`}><i>{index<current?"✓":index+1}</i><span>{label}</span></div>)}</div>
    <small>下载在后台执行，你可以切换到 Knowledge Graph 或其他论文。</small>
    {failed&&<button className="primary" onClick={retry} disabled={retrying}>{retrying?"正在重新加入队列…":"重新下载"}</button>}
  </section>;
}

function PersistentPodcastHero({task,playing,setPlaying,onRefresh}:{task:ApiTask;playing:boolean;setPlaying:(value:boolean)=>void;onRefresh:()=>Promise<void>}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const seekingRef = useRef(false);
  const resumePositionRef = useRef(0);
  const resumeAppliedRef = useRef(false);
  const [currentTime,setCurrentTime] = useState(0);
  const [duration,setDuration] = useState(0);
  const [seekableStart,setSeekableStart] = useState(0);
  const [seekableEnd,setSeekableEnd] = useState(0);
  const [speed,setSpeed] = useState(1);
  const [nativeHls,setNativeHls] = useState(false);
  const [audioSource,setAudioSource] = useState<string|undefined>(()=>preferredAudioSource(task,false));
  const [cues,setCues] = useState<{start:number;end:number;text:string}[]>([]);
  const [rawSegments,setRawSegments] = useState<string[]>([]);
  const [transcript,setTranscript] = useState("播客逐字稿会根据当前播放时间显示在这里。");
  const [retrying,setRetrying] = useState(false);
  const [retryError,setRetryError] = useState("");
  const isGenerating = task.status === "generating" || task.status === "pausing";
  const canListen = Boolean(task.audio_ready || task.stream_ready || (task.buffered_seconds||0)>=10);
  const audioFailed = task.status === "failed" && !task.audio_ready;
  const showRetry = audioFailed || retrying;
  const controlsEnabled = canListen && !showRetry;
  const usingLiveDvr = Boolean(nativeHls&&task.live_dvr?.ready&&isGenerating&&!task.audio_ready);
  const desiredAudioSource = preferredAudioSource(task,nativeHls);
  useEffect(()=>{
    const key=playbackPositionKey(task.id);
    const saved=Number(window.localStorage.getItem(key)||0);
    resumePositionRef.current=Number.isFinite(saved)&&saved>0?saved:0;
    resumeAppliedRef.current=false;
    const supportsHls=Boolean(audioRef.current?.canPlayType("application/vnd.apple.mpegurl"));
    setNativeHls(supportsHls);
    setPlaying(false); setCurrentTime(resumePositionRef.current); setDuration(0);
    setSeekableStart(0); setSeekableEnd(0);
    setCues([]); setRawSegments([]);
    setRetrying(false); setRetryError("");
    setAudioSource(preferredAudioSource(task,supportsHls));
    return()=>{
      const position=audioRef.current?.currentTime||0;
      if(Number.isFinite(position)&&position>0)window.localStorage.setItem(key,String(position));
    };
  },[task.id]);
  useEffect(()=>{
    if (!task.transcript_ready) { setTranscript("播客正在生成，完成后即可开始收听。"); return; }
    request<{start:number;end:number;text:string}[]>(`/api/tasks/${task.id}/transcript-timed`).then(items=>{setCues(items);if(items[0]?.text)setTranscript(items[0].text)}).catch(()=>{});
    fetch(`${API_BASE}/api/tasks/${task.id}/transcript`).then(r=>r.text()).then(text=>{
      const segments=text.split(/\n+/).map(s=>s.replace(/^\[round[^\]]*\]\s*[^:]+:\s*/,"").trim()).filter(s=>s.length>0);
      setRawSegments(segments);
      if(segments[0])setTranscript(segments[0]);
    }).catch(()=>setTranscript("逐字稿暂时无法读取。"));
  },[task.id,task.transcript_ready]);
  useEffect(()=>{
    if(!desiredAudioSource||audioSource===desiredAudioSource)return;
    const position=audioRef.current?.currentTime||currentTime;
    if(Number.isFinite(position)&&position>0)resumePositionRef.current=position;
    resumeAppliedRef.current=false;
    setAudioSource(desiredAudioSource);
  },[desiredAudioSource,audioSource,currentTime]);
  useEffect(()=>{
    if(!task.transcript_ready||!isGenerating)return;
    let stopped=false;
    const update=()=>request<{start:number;end:number;text:string}[]>(`/api/tasks/${task.id}/transcript-timed`).then(items=>{if(!stopped&&items.length)setCues(items)}).catch(()=>{});
    update(); const timer=window.setInterval(update,1000); return()=>{stopped=true;window.clearInterval(timer)};
  },[task.id,task.transcript_ready,isGenerating]);
  useEffect(()=>{
    if(cues.length||isGenerating||!duration||!Number.isFinite(duration)||!rawSegments.length)return;
    const totalWeight=rawSegments.reduce((sum,text)=>sum+Math.max(8,text.length),0);
    let cursor=0;
    setCues(rawSegments.map(text=>{const start=cursor;cursor+=duration*Math.max(8,text.length)/totalWeight;return {start,end:cursor,text}}));
  },[duration,rawSegments,cues.length,isGenerating]);
  useEffect(()=>{const cue=cues.find(item=>currentTime>=item.start&&currentTime<item.end);if(cue)setTranscript(cue.text)},[currentTime,cues]);
  useEffect(()=>{if(!task.audio_ready&&(task.status==="uploaded"||task.status==="paused")){request(`/api/tasks/${task.id}/generate`,{method:"POST"}).then(onRefresh).catch(()=>{})}},[task.id,task.status,task.audio_ready]);
  const persistPlaybackPosition=(seconds:number)=>{
    if(Number.isFinite(seconds)&&seconds>=0)window.localStorage.setItem(playbackPositionKey(task.id),String(seconds));
  };
  const syncAudioTimeline=(audio:HTMLAudioElement,restore=true)=>{
    let start=0,end=0;
    if(audio.seekable.length){
      start=audio.seekable.start(0);
      end=audio.seekable.end(audio.seekable.length-1);
    }else if(Number.isFinite(audio.duration)&&audio.duration>0){
      end=audio.duration;
    }
    setSeekableStart(start);
    setSeekableEnd(end);
    if(restore&&!resumeAppliedRef.current&&resumePositionRef.current>0&&end>0){
      const restored=Math.max(start,Math.min(resumePositionRef.current,Math.max(start,end-.15)));
      audio.currentTime=restored;
      setCurrentTime(restored);
      resumeAppliedRef.current=true;
    }
  };
  const toggle = async () => {
    if (!controlsEnabled) return;
    const audio=audioRef.current; if(!audio)return;
    if(audio.paused){await audio.play();setPlaying(true)}else{persistPlaybackPosition(audio.currentTime);audio.pause();setPlaying(false)}
  };
  const retryAudio = async () => {
    if(retrying)return;
    const audio=audioRef.current;
    audio?.pause();
    audio?.removeAttribute("src");
    audio?.load();
    setAudioSource(undefined);
    setPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    setSeekableStart(0);
    setSeekableEnd(0);
    resumePositionRef.current=0;
    resumeAppliedRef.current=false;
    window.localStorage.removeItem(playbackPositionKey(task.id));
    setRetryError("");
    setRetrying(true);
    try {
      await request(`/api/tasks/${task.id}/generate`,{method:"POST"});
      await onRefresh();
    } catch(cause) {
      setRetryError(cause instanceof Error?cause.message:"重新生成失败");
    } finally {
      setRetrying(false);
    }
  };
  const finiteDuration=Number.isFinite(duration)?duration:0;
  const timelineEnd=Math.max(
    seekableEnd,
    finiteDuration,
    usingLiveDvr?task.live_dvr?.duration||0:0,
    isGenerating?task.buffered_seconds||0:0,
  );
  const timelineStart=Math.min(seekableStart,timelineEnd);
  const timelineLength=Math.max(0,timelineEnd-timelineStart);
  const progress=timelineLength?Math.max(0,Math.min(100,(currentTime-timelineStart)/timelineLength*100)):0;
  const liveThreshold=Math.max(2.5,(task.live_dvr?.target_duration||4)*.75);
  const atLiveEdge=Boolean(usingLiveDvr&&timelineEnd>0&&timelineEnd-currentTime<=liveThreshold);
  const seekAudio=(track:HTMLDivElement,clientX:number)=>{
    const audio=audioRef.current;if(!audio)return;
    let seekStart=timelineStart,seekLimit=timelineEnd;
    if(audio.seekable.length){
      seekStart=audio.seekable.start(0);
      seekLimit=audio.seekable.end(audio.seekable.length-1);
    }
    if((!seekLimit||seekLimit<=seekStart)&&audio.buffered.length){
      seekStart=audio.buffered.start(0);
      seekLimit=audio.buffered.end(audio.buffered.length-1);
    }
    if(!Number.isFinite(seekLimit)||seekLimit<=seekStart)return;
    const rect=track.getBoundingClientRect();
    const ratio=Math.max(0,Math.min(1,(clientX-rect.left)/rect.width));
    const target=seekStart+ratio*(seekLimit-seekStart);
    if(Number.isFinite(target)){audio.currentTime=target;setCurrentTime(target);persistPlaybackPosition(target)}
  };
  const goToLive=async()=>{
    const audio=audioRef.current;if(!audio)return;
    syncAudioTimeline(audio,false);
    const edge=audio.seekable.length?audio.seekable.end(audio.seekable.length-1):timelineEnd;
    if(!Number.isFinite(edge)||edge<=0)return;
    audio.currentTime=Math.max(0,edge-.15);
    setCurrentTime(audio.currentTime);
    persistPlaybackPosition(audio.currentTime);
    if(audio.paused){await audio.play();setPlaying(true)}
  };
  const cycleSpeed=()=>{const values=[1,1.25,1.5,2];const next=values[(values.indexOf(speed)+1)%values.length];setSpeed(next);if(audioRef.current)audioRef.current.playbackRate=next};
  const clock=(seconds:number)=>`${Math.floor(seconds/60).toString().padStart(2,"0")}:${Math.floor(seconds%60).toString().padStart(2,"0")}`;
  return <section className="podcast-hero">
    <div className="podcast-title-row">
      <div>
        <div className="eyebrow"><span className="live-dot" /> ESSAYPOD <b>·</b> {task.filename}</div>
        <h1 className={(task.display_title || task.title).length>55?"title-long":(task.display_title || task.title).length>35?"title-medium":""}>{task.display_title || task.title}</h1>
        <div className="meta-row"><span>{taskStatusText(task)}</span>{showRetry?<strong className="audio-failed">{retrying?"正在重新生成音频…":retryError||"音频生成失败，点击下方按钮重试"}</strong>:canListen?<strong className="listen-ready">● 可以收听{usingLiveDvr?"（直播时移已开启）":isGenerating?"（继续生成中）":""}</strong>:isGenerating?<strong className="stream-buffering">{task.podcast_stage==="queued"?"前面还有音频任务，当前已排队":`正在缓存 ${Math.floor(task.buffered_seconds||0)}/10 秒`}</strong>:null}<i /><span>{(task.condense_level||"mid").toUpperCase()} 浓缩</span><i /><button onClick={()=>window.open(taskPdfUrl(task.id),"_blank")}>打开原始 PDF ↗</button></div>
      </div>
      <div className={`record-sleeve ${playing||isGenerating ? "playing" : ""}`} aria-hidden="true">
        <div className="sleeve-art"><span>ESSAY</span><b>POD</b><small>018</small></div>
        <div className="vinyl"><i className="vinyl-label"><span>E</span><b>POD</b><small /></i><em /></div>
      </div>
    </div>
    <div className="live-transcript" aria-live="polite">
      <div><time>{clock(currentTime)}</time><p>{transcript}</p></div>
    </div>
    <div className="persistent-controls">
      <audio
        ref={audioRef}
        src={audioSource}
        preload="auto"
        onTimeUpdate={e=>{const audio=e.currentTarget;setCurrentTime(audio.currentTime);syncAudioTimeline(audio,false);persistPlaybackPosition(audio.currentTime)}}
        onLoadedMetadata={e=>{const audio=e.currentTarget;setDuration(audio.duration||0);audio.playbackRate=speed;syncAudioTimeline(audio)}}
        onDurationChange={e=>{setDuration(e.currentTarget.duration||0);syncAudioTimeline(e.currentTarget)}}
        onProgress={e=>syncAudioTimeline(e.currentTarget)}
        onPause={e=>persistPlaybackPosition(e.currentTarget.currentTime)}
        onEnded={()=>{setPlaying(false);if(!isGenerating)window.localStorage.removeItem(playbackPositionKey(task.id))}}
      />
      <button className="skip" disabled={!controlsEnabled} onClick={()=>{if(audioRef.current)audioRef.current.currentTime=Math.max(0,audioRef.current.currentTime-15)}}>↶<small>15</small></button>
      <button className={`hero-play ${showRetry?"audio-retry":""} ${retrying?"retrying":""}`} disabled={showRetry?retrying:!canListen} onClick={showRetry?retryAudio:toggle} aria-busy={retrying} aria-label={showRetry?(retrying?"正在重新生成音频":"重新生成音频"):!canListen?"正在缓存音频":playing?"暂停":"播放"} title={showRetry?(retrying?"正在重新生成音频":"重新生成音频"):undefined}>{showRetry?<RetryIcon/>:playing ? "Ⅱ" : "▶"}</button>
      <button className="skip" disabled={!controlsEnabled} onClick={()=>{if(audioRef.current)audioRef.current.currentTime=Math.min(timelineEnd||currentTime+15,currentTime+15)}}>↷<small>15</small></button>
      <span className="current-time">{clock(currentTime)}</span>
      <div className={`hero-track ${usingLiveDvr?"dvr":""}`}
        onPointerDown={e=>{seekingRef.current=true;e.currentTarget.setPointerCapture(e.pointerId);seekAudio(e.currentTarget,e.clientX)}}
        onPointerMove={e=>{if(seekingRef.current)seekAudio(e.currentTarget,e.clientX)}}
        onPointerUp={e=>{if(seekingRef.current)seekAudio(e.currentTarget,e.clientX);seekingRef.current=false;e.currentTarget.releasePointerCapture(e.pointerId)}}
        onPointerCancel={()=>{seekingRef.current=false}}>
        <i style={{width:`${progress}%`}}/><b style={{left:`${progress}%`}}/>
      </div>
      {usingLiveDvr?<button className={`live-edge-button ${atLiveEdge?"active":""}`} disabled={!timelineEnd} onClick={goToLive}>{atLiveEdge?"● 直播":"回到直播"}</button>:<span>{timelineEnd?clock(timelineEnd):isGenerating?"直播":"--:--"}</span>}
      <button className="volume" aria-label="音量">⌁</button>
      <button className="speed" disabled={!controlsEnabled} onClick={cycleSpeed}>{speed.toFixed(speed===1?1:2).replace(/0$/,'')}×</button>
    </div>
  </section>;
}

function PaperSummary({task}:{task:ApiTask}) {
  const [markdown,setMarkdown]=useState("");
  const [briefState,setBriefState]=useState<"idle"|"generating"|"ready"|"failed">(task.brief?.status||"idle");
  const [briefError,setBriefError]=useState("");
  useEffect(()=>{
    let stopped=false; let source:EventSource|undefined;
    setMarkdown(""); setBriefError(""); setBriefState(task.brief?.status||"idle");
    const connect=async()=>{try{
      const data=await request<{state:{status:"idle"|"generating"|"ready"|"failed";error?:string};markdown:string}>(`/api/tasks/${task.id}/brief`);
      if(stopped)return; setMarkdown(data.markdown||""); setBriefState(data.state.status); setBriefError(data.state.error||"");
      if(data.state.status==="ready"||data.state.status==="failed")return;
      source=new EventSource(`${API_BASE}/api/tasks/${task.id}/brief/stream?offset=${encodeURIComponent((data.markdown||"").length)}`);
      source.onmessage=event=>{const payload=JSON.parse(event.data) as {delta?:string};if(payload.delta)setMarkdown(current=>current+payload.delta!);setBriefState("generating")};
      source.addEventListener("done",()=>{setBriefState("ready");source?.close()});
      source.addEventListener("brief-error",event=>{const payload=JSON.parse((event as MessageEvent).data) as {error?:string};setBriefState("failed");setBriefError(payload.error||"导读生成失败");source?.close()});
    }catch(cause){if(!stopped){setBriefState("failed");setBriefError(cause instanceof Error?cause.message:"导读加载失败")}}};
    connect(); return()=>{stopped=true;source?.close()};
  },[task.id]);
  const loading=briefState==="idle"||briefState==="generating";
  return (
    <section className="summary-card brief-card"><header><div><span className="section-kicker">REVIEWER BRIEF</span><h2>十分钟判断一篇论文值不值得读</h2><p>{loading?"审稿人导读正在边分析边输出：先判价值，再定读法。":"从真实贡献、证据强度和阅读优先级判断论文价值。"}</p></div><span className={`brief-status ${briefState}`}>{loading?"● 正在审稿":briefState==="ready"?"✓ 分析完成":"生成失败"}</span></header>
      {markdown?<MarkdownArticle source={markdown} streaming={loading} taskId={task.id}/>:<BriefSkeleton wide />}
      {briefState==="failed"&&<p className="brief-error">导读生成失败：{briefError}</p>}
    </section>
  );
}

function InlineMarkdown({text}:{text:string}) {
  return <>{text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part,index)=>part.startsWith("**")&&part.endsWith("**")?<strong key={index}>{part.slice(2,-2)}</strong>:part.startsWith("`")&&part.endsWith("`")?<code key={index}>{part.slice(1,-1)}</code>:part)}</>;
}

type FigureManifestItem={figure_id:string;label:string;caption:string;page_number:number;ready:boolean};

function FigureSlot({taskId,figureId}:{taskId:string;figureId:string}) {
  const [figure,setFigure]=useState<FigureManifestItem|null>(null);
  useEffect(()=>{let stopped=false;let timer:number|undefined;const poll=async()=>{try{const data=await request<{status:string;figures:FigureManifestItem[]}>(`/api/tasks/${taskId}/figures`);if(stopped)return;const item=data.figures.find(entry=>entry.figure_id===figureId)||null;setFigure(item);if(!item?.ready&&(data.status==="pending"||data.status==="running"))timer=window.setTimeout(poll,1800)}catch{if(!stopped)timer=window.setTimeout(poll,2200)}};poll();return()=>{stopped=true;if(timer)window.clearTimeout(timer)}},[taskId,figureId]);
  if(!figure?.ready)return null;
  const displayLabel=figureId.replace(/^figure_/i,"Figure ").replace(/_/g," ");
  const caption=(figure.caption||"").replace(/^Figure\s+\d+[a-z]?\s*[:：.\-]?\s*/i,"").trim();
  return <figure style={{width:"min(68%, 900px)",margin:"30px auto 18px",padding:0,border:"none",borderRadius:0,background:"transparent",boxShadow:"none"}}><img src={taskFigureUrl(taskId,figureId)} alt={caption||displayLabel} style={{display:"block",width:"100%",height:"auto",maxHeight:"560px",objectFit:"contain",margin:"0 auto"}}/><figcaption style={{fontSize:"10px",lineHeight:1.55,color:"#747178",padding:"9px 0 0",textAlign:"left"}}><b style={{color:"#4d3bd0"}}>{displayLabel}</b>{caption?` · ${caption}`:""} <span>（第 {figure.page_number} 页）</span></figcaption></figure>;
}

type TranslationState={status:"idle"|"generating"|"ready"|"failed"|"interrupted";ready:boolean;source_ready:boolean;target_ready:boolean;pdf_status?:"idle"|"generating"|"ready"|"failed"|"interrupted";pdf_ready?:boolean;pdf_error?:string;reading_pdf_status?:"idle"|"generating"|"ready"|"failed"|"interrupted";reading_pdf_ready?:boolean;reading_pdf_error?:string;deepl_pdf_status?:"idle"|"queued"|"generating"|"ready"|"failed"|"interrupted";deepl_pdf_ready?:boolean;deepl_pdf_active?:boolean;deepl_pdf_error?:string;deepl_pdf_message?:string;deepl_character_count?:number|null;deepl_character_limit?:number|null;error?:string;message?:string};

function PaperTranslation({task}:{task:ApiTask}) {
  const [language,setLanguage]=useState<"source"|"zh">("zh");
  const [markdown,setMarkdown]=useState("");
  const [renderedLanguage,setRenderedLanguage]=useState<"source"|"zh">("zh");
  const [state,setState]=useState<TranslationState>(task.translation||{status:"idle",ready:false,source_ready:false,target_ready:false});
  const [downloadOpen,setDownloadOpen]=useState(false);
  const [deeplPollKey,setDeeplPollKey]=useState(0);
  const [deeplStarting,setDeeplStarting]=useState(false);
  const [deeplNotice,setDeeplNotice]=useState(false);
  const previousDeeplStatus=useRef(state.deepl_pdf_status||"idle");
  const [chatOpen,setChatOpen]=useState(false);
  const [chatState,setChatState]=useState(task.chatbot||{status:"idle",ready:false,message:"等待论文结构"});
  const [structure,setStructure]=useState<PaperStructure|null>(null);
  const [conversationId,setConversationId]=useState("");
  const [messages,setMessages]=useState<ChatMessage[]>([]);
  const [chatInput,setChatInput]=useState("");
  const [chatBusy,setChatBusy]=useState(false);
  const [chatStage,setChatStage]=useState("");
  const [chatTrace,setChatTrace]=useState<ChatTrace[]>([]);
  const [chatError,setChatError]=useState("");
  const [activeCitation,setActiveCitation]=useState<ChatCitation|null>(null);
  const documentPaneRef=useRef<HTMLDivElement>(null);
  const pendingReadingRatio=useRef<number|null>(null);
  const pendingOuterScrollTop=useRef<number|null>(null);
  useEffect(()=>{previousDeeplStatus.current=task.translation?.deepl_pdf_status||"idle";setDeeplNotice(false);setDownloadOpen(false)},[task.id]);
  const switchLanguage=(next:"source"|"zh")=>{if(next===language)return;const scroller=documentPaneRef.current;if(scroller){const range=Math.max(1,scroller.scrollHeight-scroller.clientHeight);pendingReadingRatio.current=scroller.scrollTop/range}setLanguage(next)};
  const setChatVisible=(open:boolean)=>{const outer=documentPaneRef.current?.closest(".content-scroll") as HTMLElement|null;pendingOuterScrollTop.current=outer?.scrollTop??null;setChatOpen(open)};
  useEffect(()=>{
    let stopped=false;let timer:number|undefined;
    const poll=async()=>{try{
      const data=await request<{state:TranslationState;markdown:string}>(`/api/tasks/${task.id}/translation?language=${language}`);
      if(stopped)return;
      const previous=previousDeeplStatus.current;
      const nextDeepl=data.state.deepl_pdf_status||"idle";
      if(nextDeepl==="ready"&&["queued","generating"].includes(previous))setDeeplNotice(true);
      previousDeeplStatus.current=nextDeepl;
      setState(data.state);setMarkdown(data.markdown||"");setRenderedLanguage(language);
      if(language==="zh"&&data.state.source_ready&&!data.state.target_ready)setLanguage("source");
      if(!["ready","failed"].includes(data.state.status)||["queued","generating"].includes(nextDeepl))timer=window.setTimeout(poll,1800);
    }catch(cause){if(!stopped){setState(current=>({...current,status:"failed",error:cause instanceof Error?cause.message:"翻译加载失败"}))}}};
    poll();return()=>{stopped=true;if(timer)window.clearTimeout(timer)};
  },[task.id,language,deeplPollKey]);
  useEffect(()=>{let stopped=false;let timer:number|undefined;const poll=async()=>{try{const next=await request<typeof chatState>(`/api/tasks/${task.id}/chatbot/status`);if(stopped)return;setChatState(next);if(next.ready){const documentData=await request<PaperStructure>(`/api/tasks/${task.id}/chatbot/document`);if(!stopped)setStructure(documentData)}else if(next.status!=="failed")timer=window.setTimeout(poll,1800)}catch{if(!stopped)timer=window.setTimeout(poll,2400)}};poll();return()=>{stopped=true;if(timer)window.clearTimeout(timer)}},[task.id]);
  useEffect(()=>{setConversationId("");setMessages([]);setChatError("");const load=async()=>{try{const data=await request<{conversations:ChatConversation[]}>(`/api/tasks/${task.id}/chatbot/conversations`);const preferred=window.localStorage.getItem(`essaypod-chat-${task.id}`);const conversation=data.conversations.find(item=>item.id===preferred)||data.conversations[0];if(conversation){setConversationId(conversation.id);setMessages(conversation.messages||[])}}catch{}};load()},[task.id]);
  useLayoutEffect(()=>{if(pendingOuterScrollTop.current===null)return;const outer=documentPaneRef.current?.closest(".content-scroll") as HTMLElement|null;if(!outer)return;const target=pendingOuterScrollTop.current;const restore=()=>{outer.scrollTop=target};restore();const frame=requestAnimationFrame(restore);const timer1=window.setTimeout(restore,120);const timer2=window.setTimeout(()=>{restore();pendingOuterScrollTop.current=null},320);return()=>{cancelAnimationFrame(frame);window.clearTimeout(timer1);window.clearTimeout(timer2)}},[chatOpen]);
  useLayoutEffect(()=>{if(pendingReadingRatio.current===null||!markdown||renderedLanguage!==language)return;const ratio=pendingReadingRatio.current;const restore=()=>{const scroller=documentPaneRef.current;if(scroller)scroller.scrollTop=ratio*Math.max(0,scroller.scrollHeight-scroller.clientHeight)};restore();const frame=requestAnimationFrame(restore);const timer1=window.setTimeout(restore,250);const timer2=window.setTimeout(()=>{restore();pendingReadingRatio.current=null},900);return()=>{cancelAnimationFrame(frame);window.clearTimeout(timer1);window.clearTimeout(timer2)}},[markdown,language,renderedLanguage]);
  useLayoutEffect(()=>{if(!activeCitation)return;const scroll=()=>{const scroller=documentPaneRef.current;const target=document.getElementById(`paper-${activeCitation.block_id}`);if(!scroller||!target)return;const paneRect=scroller.getBoundingClientRect();const targetRect=target.getBoundingClientRect();scroller.scrollTo({top:scroller.scrollTop+targetRect.top-paneRect.top-scroller.clientHeight/2+targetRect.height/2,behavior:"smooth"})};scroll();const timer=window.setTimeout(scroll,180);return()=>window.clearTimeout(timer)},[activeCitation,language,structure]);
  const ask=async()=>{const question=chatInput.trim();if(!question||chatBusy||!chatState.ready)return;setChatInput("");setChatError("");setChatBusy(true);setChatStage("正在检索论文");setChatTrace([]);let id=conversationId;let answerStarted=false;try{if(!id){const conversation=await request<ChatConversation>(`/api/tasks/${task.id}/chatbot/conversations`,{method:"POST"});id=conversation.id;setConversationId(id);window.localStorage.setItem(`essaypod-chat-${task.id}`,id)}setMessages(current=>[...current,{role:"user",content:question},{role:"assistant",content:"",citations:[]}]);const response=await fetch(`${API_BASE}/api/tasks/${task.id}/chatbot/chat`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:id,question})});if(!response.ok)throw new Error((await response.json()).detail||`问答失败 (${response.status})`);if(!response.body)throw new Error("浏览器不支持流式回答");const reader=response.body.getReader();const decoder=new TextDecoder();let buffer="";while(true){const {done,value}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});const events=buffer.split("\n\n");buffer=events.pop()||"";for(const raw of events){const line=raw.split("\n").find(item=>item.startsWith("data:"));if(!line)continue;const event=JSON.parse(line.slice(5).trim()) as ChatEvent;if(event.type==="status"&&!answerStarted)setChatStage(event.message||"");if(event.type==="trace"&&!answerStarted)setChatTrace([{title:event.title||"Thinking",content:event.content||""}]);if(event.type==="delta"&&event.content){answerStarted=true;setChatStage("");setChatTrace([]);setMessages(current=>current.map((item,index)=>index===current.length-1?{...item,content:item.content+event.content}:item))}if(event.type==="done")setMessages(current=>current.map((item,index)=>index===current.length-1?{...item,citations:event.citations||[]}:item));if(event.type==="error")throw new Error(event.message||"问答失败")}if(done)break}}catch(cause){setChatError(cause instanceof Error?cause.message:"问答失败");setMessages(current=>current.filter((item,index)=>!(index===current.length-1&&item.role==="assistant"&&!item.content)))}finally{setChatBusy(false);setChatStage("");setChatTrace([])}};
  const working=state.status==="idle"||state.status==="generating"||state.status==="interrupted";
  const startDeepl=async()=>{
    if(deeplStarting||["queued","generating"].includes(state.deepl_pdf_status||""))return;
    setDeeplStarting(true);setDeeplNotice(false);
    try{
      const result=await request<{state:{status:TranslationState["deepl_pdf_status"];ready:boolean;active:boolean;error:string;message:string}}>(`/api/tasks/${task.id}/translation/deepl-pdf/generate`,{method:"POST"});
      const next=result.state.status||"queued";
      previousDeeplStatus.current=next;
      setState(current=>({...current,deepl_pdf_status:next,deepl_pdf_ready:result.state.ready,deepl_pdf_active:result.state.active,deepl_pdf_error:result.state.error,deepl_pdf_message:result.state.message}));
      setDeeplPollKey(value=>value+1);
    }catch(cause){
      const message=cause instanceof Error?cause.message:"DeepL 翻译启动失败";
      setState(current=>({...current,deepl_pdf_status:"failed",deepl_pdf_error:message,deepl_pdf_message:"DeepL 翻译启动失败"}));
    }finally{setDeeplStarting(false)}
  };
  return <section className={`translation-card ${chatOpen?"chat-expanded":""}`}>
    <div className="language-toggle"><button className={language==="source"?"active":""} disabled={!state.source_ready&&working} onClick={()=>switchLanguage("source")}>原文</button><button className={language==="zh"?"active":""} disabled={!state.target_ready&&working} onClick={()=>switchLanguage("zh")}>中文</button></div>
    <header className="translation-head"><div><span className="section-kicker">PAPER TRANSLATION</span><h2>论文全文翻译</h2><p>保留原论文标题层级、图表和公式位置，按版面阅读顺序生成双语 Markdown。</p></div></header>
    <div className={`translation-state ${state.status}`}><i/><span>{state.status==="ready"?"翻译完成":state.status==="failed"?"翻译失败":state.source_ready?"原文结构已生成，正在翻译中文":"正在识别版面、恢复阅读顺序并裁剪图表"}</span></div>
    <div className="translation-floating-rail">
      <button className={`paper-chat-toggle ${chatOpen?"is-placeholder":""}`} onClick={()=>setChatVisible(true)} aria-label="展开论文问答" aria-hidden={chatOpen} disabled={chatOpen} tabIndex={chatOpen?-1:0}><span>✦</span>问论文</button>
      <div className="translation-download-float"><button className={`translation-pdf-float ${state.pdf_status==="failed"&&state.reading_pdf_status==="failed"&&state.deepl_pdf_status==="failed"?"failed":""}`} onClick={()=>setDownloadOpen(value=>!value)} aria-label="选择译文 PDF 下载格式" title="下载译文 PDF" aria-expanded={downloadOpen}><i aria-hidden/></button>{downloadOpen&&<div className="translation-download-menu">{state.pdf_ready?<a href={taskTranslationPdfUrl(task.id)} download onClick={()=>setDownloadOpen(false)}><b>原版式译文 · Google</b><span>免费翻译，贴回原论文版面</span></a>:<span className="disabled"><b>原版式译文 · Google</b><span>{state.pdf_status==="failed"?"生成失败":"正在生成…"}</span></span>}{state.deepl_pdf_ready?<a href={taskTranslationDeeplPdfUrl(task.id)} download onClick={()=>setDownloadOpen(false)}><b>原版式译文 · DeepL</b><span>DeepL 翻译完成，点击下载</span></a>:["queued","generating"].includes(state.deepl_pdf_status||"")?<button className="deepl-option working" disabled><b>{state.deepl_pdf_status==="queued"?"DeepL 翻译排队中":"DeepL 正在翻译"}</b><span>{state.deepl_pdf_message||"正在生成原版式译文…"}</span></button>:<button className="deepl-option" disabled={deeplStarting} onClick={startDeepl}><b>{state.deepl_pdf_status==="failed"?"重试 DeepL 原版式翻译":"原版式译文 · DeepL"}</b><span>{deeplStarting?"正在启动…":state.deepl_pdf_status==="failed"?(state.deepl_pdf_error||"上次生成失败，点击重试"):"按需生成 · 点击后才会产生 DeepL 字符费用"}</span></button>}{state.reading_pdf_ready?<a href={taskTranslationReadingPdfUrl(task.id)} download onClick={()=>setDownloadOpen(false)}><b>Markdown 阅读版</b><span>按网页阅读样式排版</span></a>:<span className="disabled"><b>Markdown 阅读版</b><span>{state.reading_pdf_status==="failed"?"生成失败":"正在生成…"}</span></span>}</div>}</div>
    </div>
    {deeplNotice&&<div className="deepl-ready-notice" role="status"><div><b>DeepL 原版式译文已完成</b><span>文件已经准备好，可以立即下载。</span></div><a href={taskTranslationDeeplPdfUrl(task.id)} download onClick={()=>setDeeplNotice(false)}>下载译文</a><button onClick={()=>setDeeplNotice(false)} aria-label="关闭提醒">×</button></div>}
    <div className="translation-reading-layout">
      {chatOpen&&<div className="paper-chat-rail"><PaperChatPanel taskId={task.id} messages={messages} value={chatInput} setValue={setChatInput} onAsk={ask} onClose={()=>setChatVisible(false)} busy={chatBusy} stage={chatStage} trace={chatTrace} error={chatError} ready={Boolean(chatState.ready)} stateMessage={chatState.error||chatState.message||"正在准备论文索引"} onCitation={citation=>{setActiveCitation(citation);setDownloadOpen(false)}}/></div>}
      <div className="translation-document-pane" ref={documentPaneRef} role="region" aria-label="论文翻译正文" tabIndex={0}>{markdown?<TranslationMarkdown source={markdown} taskId={task.id} language={language} blocks={structure?.blocks||[]} activeCitation={activeCitation}/>:working?<TranslationSkeleton />:<p className="translation-error">{state.error||"暂时无法生成论文翻译。"}</p>}</div>
      <div className="translation-pane-bottom-mask" aria-hidden/>
    </div>
  </section>;
}

type PaperBlock={block_id:string;type:string;source:string;translation:string;heading_level:number;heading_path:string[];asset_path:string;media_id:string;media_type:string;caption_source:string;caption_translation:string;page_number?:number};
type PaperStructure={version:number;blocks:PaperBlock[]};
type ChatCitation={citation_id:string;block_id:string;quote_source:string;quote_translation:string;heading_path:string[];page_number?:number};
type ChatMessage={role:"user"|"assistant";content:string;citations?:ChatCitation[]};
type ChatConversation={id:string;title:string;messages:ChatMessage[]};
type ChatTrace={title:string;content:string};
type ChatEvent={type:"status"|"trace"|"delta"|"done"|"error";message?:string;title?:string;content?:string;citations?:ChatCitation[]};

function PaperChatPanel({taskId,messages,value,setValue,onAsk,onClose,busy,stage,trace,error,ready,stateMessage,onCitation}:{taskId:string;messages:ChatMessage[];value:string;setValue:(value:string)=>void;onAsk:()=>void;onClose:()=>void;busy:boolean;stage:string;trace:ChatTrace[];error:string;ready:boolean;stateMessage:string;onCitation:(citation:ChatCitation)=>void}) {
  const bodyRef=useRef<HTMLDivElement>(null);
  const latestTrace=trace[trace.length-1];
  const progressText=latestTrace
    ? `${latestTrace.title.replace(/^Round\s*\d+\s*[·•]\s*/i,"")}${latestTrace.content?` · ${latestTrace.content.replace(/\s+/g," ").trim()}`:""}`
    : stage||"正在阅读论文";
  useEffect(()=>{if(bodyRef.current)bodyRef.current.scrollTop=bodyRef.current.scrollHeight},[messages,stage,trace]);
  return <aside className="paper-chat-panel"><header><div><span className="section-kicker">PAPER Q&A</span><h3>与这篇论文对话</h3></div><i className={ready?"ready":""}/><button className="paper-chat-close" onClick={onClose} aria-label="收起论文问答" title="收起问答">‹</button></header><div className="paper-chat-messages" ref={bodyRef}>{messages.length===0?<div className="paper-chat-empty"><b>从论文原文中寻找答案</b><p>可以追问方法、实验、公式或图表。回答会附带可点击的原文引用。</p><button onClick={()=>setValue("这篇论文真正解决了什么问题？")}>它真正解决了什么问题？</button><button onClick={()=>setValue("作者的核心贡献有哪些证据支持？")}>核心贡献有何证据？</button></div>:messages.map((message,index)=><article className={message.role} key={index}>{message.role==="user"?<p>{message.content}</p>:<ChatAnswer taskId={taskId} message={message} onCitation={onCitation}/>}</article>)}{busy&&<div className="paper-chat-thinking"><i/><span key={progressText}>{progressText}</span></div>}{error&&<p className="paper-chat-error">{error}</p>}</div><footer><textarea value={value} onChange={event=>setValue(event.target.value)} onKeyDown={event=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();onAsk()}}} placeholder={ready?"针对论文提问…":"论文索引准备中…"} disabled={!ready||busy}/><button onClick={onAsk} disabled={!ready||busy||!value.trim()} aria-label="发送问题">↑</button><small>{ready?"Enter 发送 · Shift + Enter 换行":stateMessage}</small></footer></aside>
}

function chatImageUrl(taskId:string,src?:string){
  const value=(src||"").trim().replace(/^<|>$/g,"");
  if(!value)return "";
  if(/^https?:\/\//i.test(value)||value.startsWith("data:")||value.startsWith(`${API_BASE}/`))return value;
  const clean=value.replace(/^\.\//,"").replace(/^\/+/,"");
  if(clean.startsWith("api/tasks/"))return `${API_BASE}/${clean}`;
  if(clean.includes("/translation/assets/"))return `${API_BASE}/${clean}`;
  return taskTranslationAssetUrl(taskId,clean);
}

function ChatAnswer({taskId,message,onCitation}:{taskId:string;message:ChatMessage;onCitation:(citation:ChatCitation)=>void}) {
  const citations=new Map((message.citations||[]).map(item=>[item.block_id,item]));
  const hasMarkers=/〔block_\d{5}〕/.test(message.content);
  const markdown=message.content
    .replace(/https?:\/\/[^\s<>"'，。；！？、（）【】]+/g,(url,offset,source)=>{
      const prefix=source.slice(Math.max(0,offset-2),offset);
      if(prefix==="]("||source[offset-1]==="<")return url;
      const clean=url.replace(/[\]),.;:!?]+$/,"");
      return `<${clean}>${url.slice(clean.length)}`;
    })
    .replace(/\\\[([\s\S]*?)\\\]/g,(_,formula)=>`\n$$\n${formula.trim()}\n$$\n`)
    .replace(/\\\((.+?)\\\)/g,(_,formula)=>`$${formula.trim()}$`)
    .replace(/〔(block_\d{5})〕/g,"[↗](citation:$1)");
  return <div className="chat-answer"><ReactMarkdown
    remarkPlugins={[remarkGfm,remarkMath]}
    rehypePlugins={[rehypeKatex]}
    urlTransform={url=>url}
    components={{
      a:({href,children})=>{
        if(href?.startsWith("citation:")){
          const citation=citations.get(href.slice("citation:".length));
          return citation?<button className="inline-citation" onClick={()=>onCitation(citation)} title={citation.heading_path.join(" › ")}>↗</button>:null;
        }
        return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
      },
      img:({src,alt})=>{
        const url=chatImageUrl(taskId,src);
        return url?<figure className="chat-answer-image"><img src={url} alt={alt||"论文图片"} loading="lazy"/>{alt&&<figcaption>{alt}</figcaption>}</figure>:<span className="chat-image-missing">{alt||"图片"} 暂无可加载路径</span>;
      },
    }}
  >{markdown}</ReactMarkdown>{!hasMarkers&&message.citations&&message.citations.length>0&&<div className="chat-citation-row"><span>原文依据</span>{message.citations.map(citation=><button className="inline-citation" key={citation.citation_id} onClick={()=>onCitation(citation)} title={citation.heading_path.join(" › ")}>↗</button>)}</div>}</div>;
}

type TranslationMediaToken={alt:string;path:string;mediaType:string;caption:string;end:number};

function readTranslationMedia(lines:string[],start:number):TranslationMediaToken|null {
  const image=(lines[start]||"").trim().match(/^!\[([^\]]*)\]\(([^)]+)\)$/);if(!image)return null;
  let end=start+1;while(end<lines.length&&!lines[end].trim())end++;
  let caption="";const candidate=(lines[end]||"").trim();
  if(candidate.startsWith("*")&&candidate.endsWith("*")&&candidate.length>2){caption=candidate.slice(1,-1);end++;while(end<lines.length&&!lines[end].trim())end++}
  return {alt:image[1]||"论文插图",path:image[2].replace(/^\.\//,"").replace(/^assets\//,""),mediaType:image[1].toLowerCase().replace(/\s+/g,"-")||"figure",caption,end};
}

function HighlightedInline({text,quote,active}:{text:string;quote:string;active:boolean}) {
  if(!active)return <InlineMarkdown text={text}/>;
  const needle=quote.trim();const index=needle?text.indexOf(needle):-1;
  if(index<0)return <mark className="paper-highlight"><InlineMarkdown text={text}/></mark>;
  return <>{index>0&&<InlineMarkdown text={text.slice(0,index)}/>}<mark className="paper-highlight"><InlineMarkdown text={text.slice(index,index+needle.length)}/></mark>{index+needle.length<text.length&&<InlineMarkdown text={text.slice(index+needle.length)}/>}</>;
}

function TranslationMarkdown({source,taskId,language,blocks,activeCitation}:{source:string;taskId:string;language:"source"|"zh";blocks:PaperBlock[];activeCitation:ChatCitation|null}) {
  const [mediaWidths,setMediaWidths]=useState<Record<string,number>>({});
  const maxMediaWidth=Math.max(0,...Object.values(mediaWidths));
  let blockCursor=0;
  const takeBlock=(type:string,assetPath="")=>{for(let index=blockCursor;index<Math.min(blocks.length,blockCursor+6);index++){const block=blocks[index];if(block.type===type&&(!assetPath||block.asset_path.replace(/^\.\//,"")===`assets/${assetPath}`||block.asset_path.replace(/^assets\//,"")===assetPath)){blockCursor=index+1;return block}}return undefined};
  const blockProps=(block?:PaperBlock)=>({id:block?`paper-${block.block_id}`:undefined,className:block&&activeCitation?.block_id===block.block_id?"citation-target":undefined});
  const quoteFor=(block?:PaperBlock)=>block&&activeCitation?.block_id===block.block_id?(language==="source"?activeCitation.quote_source:activeCitation.quote_translation):"";
  const lines=source.replace(/\r\n/g,"\n").split("\n");const nodes:React.ReactNode[]=[];
  for(let i=0;i<lines.length;){const line=lines[i].trim();if(!line){i++;continue}
    const heading=line.match(/^(#{1,3})\s+(.+)$/);if(heading){const block=takeBlock("heading");const level=heading[1].length;const Tag=(level===1?"h2":level===2?"h3":"h4") as "h2"|"h3"|"h4";nodes.push(<Tag {...blockProps(block)} key={i}><HighlightedInline text={heading[2]} quote={quoteFor(block)} active={Boolean(block&&activeCitation?.block_id===block.block_id)}/></Tag>);i++;continue}
    const media=readTranslationMedia(lines,i);if(media){const mediaBlock=takeBlock("media",media.path);const next=readTranslationMedia(lines,media.end);const mediaWidth=mediaWidths[media.path]||0;const nextWidth=next?mediaWidths[next.path]||0:0;const canPair=Boolean(next&&maxMediaWidth&&mediaWidth&&nextWidth&&mediaWidth+nextWidth<=maxMediaWidth);const renderCaption=(caption:string,block?:PaperBlock)=><HighlightedInline text={caption} quote={quoteFor(block)} active={Boolean(block&&activeCitation?.block_id===block.block_id)}/>;const renderMedia=(item:TranslationMediaToken,width:number,key:string,paired=false,block?:PaperBlock)=><figure {...blockProps(block)} className={`translation-media ${item.mediaType} ${block&&activeCitation?.block_id===block.block_id?"citation-target":""}`} style={paired?{flex:`${width} 1 0`,width:0}:{width:maxMediaWidth&&width?`${Math.min(100,width/maxMediaWidth*100)}%`:undefined}} key={key}><img src={taskTranslationAssetUrl(taskId,item.path)} alt={item.alt} style={{width:"100%"}} onLoad={event=>{const value=event.currentTarget.naturalWidth;if(value&&mediaWidths[item.path]!==value)setMediaWidths(current=>current[item.path]===value?current:{...current,[item.path]:value})}}/>{item.caption&&<figcaption>{renderCaption(item.caption,block)}</figcaption>}</figure>;if(canPair&&next){const nextBlock=takeBlock("media",next.path);nodes.push(<div className="translation-media-row" style={{width:`${Math.min(100,(mediaWidth+nextWidth)/maxMediaWidth*100)}%`}} key={`media-row-${i}`}><div className="translation-media-pair">{renderMedia({...media,caption:""},mediaWidth,`${i}-a`,true,mediaBlock)}{renderMedia({...next,caption:""},nextWidth,`${i}-b`,true,nextBlock)}</div>{media.caption&&<p className="translation-pair-caption">{renderCaption(media.caption,mediaBlock)}</p>}{next.caption&&<p className="translation-pair-caption">{renderCaption(next.caption,nextBlock)}</p>}</div>);i=next.end}else{nodes.push(renderMedia(media,mediaWidth,`media-${i}`,false,mediaBlock));i=media.end}continue}
    if(/^[-–—*•]\s+/.test(line)){const block=takeBlock("list");const items:string[]=[];while(i<lines.length&&/^[-–—*•]\s+/.test(lines[i].trim()))items.push(lines[i++].trim().replace(/^[-–—*•]\s+/,""));nodes.push(<ul {...blockProps(block)} key={i}>{items.map((item,j)=><li key={j}><HighlightedInline text={item} quote={quoteFor(block)} active={Boolean(block&&activeCitation?.block_id===block.block_id)}/></li>)}</ul>);continue}
    if(/^\d+[.)]\s+/.test(line)){const block=takeBlock("list");const items:string[]=[];while(i<lines.length&&/^\d+[.)]\s+/.test(lines[i].trim()))items.push(lines[i++].trim().replace(/^\d+[.)]\s+/,""));nodes.push(<ol {...blockProps(block)} key={i}>{items.map((item,j)=><li key={j}><HighlightedInline text={item} quote={quoteFor(block)} active={Boolean(block&&activeCitation?.block_id===block.block_id)}/></li>)}</ol>);continue}
    if(line.startsWith("*")&&line.endsWith("*")&&line.length>2){nodes.push(<p className="translation-caption" key={i}>{line.slice(1,-1)}</p>);i++;continue}
    const block=takeBlock("paragraph");const paragraph=[line];i++;while(i<lines.length&&lines[i].trim()&&!/^(#{1,3}\s+|!\[|[-–—*•]\s+|\d+[.)]\s+)/.test(lines[i].trim()))paragraph.push(lines[i++].trim());const text=paragraph.join(language==="source"?" ":"");nodes.push(<p {...blockProps(block)} key={i}><HighlightedInline text={text} quote={quoteFor(block)} active={Boolean(block&&activeCitation?.block_id===block.block_id)}/></p>);
  }
  return <article className="translation-markdown">{nodes}</article>;
}

function TranslationSkeleton(){return <div className="translation-skeleton"><div><i/><i/><i/></div><p>论文结构识别与翻译正在后台并行进行，完成后会自动显示。</p></div>}

function MarkdownArticle({source,streaming,taskId}:{source:string;streaming:boolean;taskId:string}) {
  const normalized=source.replace(/\s*\[\[(?:figure:)?(figure_[a-z0-9_-]+)\]\]\s*/gi,"\n[[figure:$1]]\n");
  const lines=normalized.split("\n"); const nodes:React.ReactNode[]=[];
  for(let i=0;i<lines.length;){const line=lines[i].trim();
    if(!line){i++;continue}
    if(line.startsWith("## ")){nodes.push(<h3 key={i}>{line.slice(3)}</h3>);i++;continue}
    if(line.startsWith("### ")){nodes.push(<h4 key={i}>{line.slice(4)}</h4>);i++;continue}
    const figureMatch=line.match(/^\[\[figure:([a-z0-9_-]+)\]\]$/i);if(figureMatch){nodes.push(<FigureSlot key={`${i}-${figureMatch[1]}`} taskId={taskId} figureId={figureMatch[1]}/>);i++;continue}
    if(/^---+$/.test(line)){nodes.push(<hr key={i}/>);i++;continue}
    if(line.startsWith("> ")){const quotes:string[]=[];while(i<lines.length&&lines[i].trim().startsWith("> "))quotes.push(lines[i++].trim().slice(2));nodes.push(<blockquote key={i}><InlineMarkdown text={quotes.join(" ")}/></blockquote>);continue}
    if(/^[-*] /.test(line)){const items:string[]=[];while(i<lines.length&&/^[-*] /.test(lines[i].trim()))items.push(lines[i++].trim().slice(2));nodes.push(<ul key={i}>{items.map((item,j)=><li key={j}><InlineMarkdown text={item}/></li>)}</ul>);continue}
    if(/^\d+[.)] /.test(line)){const items:string[]=[];while(i<lines.length&&/^\d+[.)] /.test(lines[i].trim()))items.push(lines[i++].trim().replace(/^\d+[.)] /,""));nodes.push(<ol key={i}>{items.map((item,j)=><li key={j}><InlineMarkdown text={item}/></li>)}</ol>);continue}
    const paragraph=[line];i++;while(i<lines.length&&lines[i].trim()&&!/^(#{2,3} |[-*] |\d+[.)] |> |---+$|\[\[figure:)/.test(lines[i].trim()))paragraph.push(lines[i++].trim());nodes.push(<p key={i}><InlineMarkdown text={paragraph.join("")}/></p>);
  }
  return <article className="markdown-brief">{nodes}{streaming&&<span className="stream-caret" aria-hidden />}</article>;
}

function BriefSkeleton({wide=false}:{wide?:boolean}) { return <div className={`brief-skeleton ${wide?"wide":""}`}><i/><i/><i/></div> }

function Studio({task,format,setFormat,regions,setRegions,selected,setSelected,dirty,setDirty,onRefresh}:{task:ApiTask;format:"16:9"|"9:16";setFormat:(v:"16:9"|"9:16")=>void;regions:Record<RegionName,Region>;setRegions:(v:Record<RegionName,Region>)=>void;selected:RegionName;setSelected:(v:RegionName)=>void;dirty:boolean;setDirty:(v:boolean)=>void;onRefresh:()=>Promise<void>}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{name:RegionName; sx:number; sy:number; origin:Region; mode:"move"|"resize"}|null>(null);
  const [snapGuides,setSnapGuides] = useState({x:false,y:false});
  const [subtitleY,setSubtitleY] = useState(60);
  const [subtitleSize,setSubtitleSize] = useState(54);
  const [titleSize,setTitleSize] = useState(42);
  const [keyPointsSize,setKeyPointsSize] = useState(36);
  const [titleShadow,setTitleShadow] = useState(true);
  const [keyPointsShadow,setKeyPointsShadow] = useState(true);
  const [titleAlignment,setTitleAlignment] = useState<TitleAlignment>("left");
  const [totalPages,setTotalPages] = useState(1);
  const [visiblePageCount,setVisiblePageCount] = useState(1);
  const [figureDuration,setFigureDuration] = useState(3);
  const [backgroundColor,setBackgroundColor] = useState("#ebe8df");
  const [selectedLayer,setSelectedLayer] = useState<StudioLayer>(selected);
  const [templateSaved,setTemplateSaved] = useState(false);
  const [templates,setTemplates] = useState<Array<LayoutTemplate|null>>([null,null,null]);
  const [activeLayout,setActiveLayout] = useState<LayoutPreset>("recommended");
  const [layerOrder,setLayerOrder] = useState<RegionName[]>(["title","key_points","page","figures"]);
  const [socialPreference,setSocialPreference] = useState("");
  const [socialCopy,setSocialCopy] = useState("");
  const [socialCopyGenerating,setSocialCopyGenerating] = useState(false);
  const [socialCopyError,setSocialCopyError] = useState("");
  const [socialCopyCopied,setSocialCopyCopied] = useState(false);
  const videoState = task.video?.[format];
  const isVideoGenerating = videoState?.status === "generating";
  const videoReady = Boolean(videoState?.ready);
  const videoProgress = Math.max(0, Math.min(100, videoState?.progress || 0));
  useEffect(()=>{try{const raw=window.localStorage.getItem("essaypod-video-layout-templates");if(raw){const saved=JSON.parse(raw);setTemplates([saved[0]??null,saved[1]??null,saved[2]??null])}}catch{}},[]);
  useEffect(()=>{if(format!=="16:9")return;request<{layout:VideoLayout;pdf_page_count:number}>(`/api/tasks/${task.id}/video/layout?video_format=16:9`).then(data=>{const pageTotal=Math.max(1,data.pdf_page_count||1);setRegions(data.layout.regions);setSubtitleY(data.layout.subtitle?.margin_v??60);setSubtitleSize(data.layout.subtitle?.font_size??54);setTitleSize(data.layout.typography?.title_font_size??42);setKeyPointsSize(data.layout.typography?.key_points_font_size??36);setTitleShadow(data.layout.typography?.title_shadow??true);setKeyPointsShadow(data.layout.typography?.key_points_shadow??true);setTitleAlignment(data.layout.typography?.title_alignment??"left");setTotalPages(pageTotal);setVisiblePageCount(Math.max(1,Math.min(data.layout.pdf?.visible_page_count||pageTotal,pageTotal)));setFigureDuration(data.layout.figures?.display_duration??3);setBackgroundColor(data.layout.background?.color??"#ebe8df");setLayerOrder(data.layout.z_order??["title","key_points","page","figures"]);const source=data.layout.layout_source;setActiveLayout(source&&source!=="custom"?source:null);setDirty(false)}).catch(()=>{})},[task.id,format]);
  useEffect(()=>{setSocialCopy("");setSocialCopyError("");request<{ready:boolean;content:string}>(`/api/tasks/${task.id}/social-copy`).then(data=>{if(data.ready)setSocialCopy(data.content)}).catch(()=>{})},[task.id]);
  const saveLayout=async()=>{await request(`/api/tasks/${task.id}/video/layout`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({video_format:"16:9",layout:{canvas_width:1920,canvas_height:1080,regions,subtitle:{margin_v:subtitleY,font_size:subtitleSize},typography:{title_font_size:titleSize,key_points_font_size:keyPointsSize,title_shadow:titleShadow,key_points_shadow:keyPointsShadow,title_alignment:titleAlignment},pdf:{visible_page_count:visiblePageCount},figures:{display_duration:figureDuration},background:{color:backgroundColor},z_order:layerOrder,layout_source:activeLayout??"custom"}})});setDirty(false)};
  const generateVideo=async()=>{if(format==="16:9"&&dirty)await saveLayout();const form=new FormData();form.append("video_format",format);await request(`/api/tasks/${task.id}/video/generate`,{method:"POST",body:form});await onRefresh()};
  const generateSocialCopy=async()=>{setSocialCopy("");setSocialCopyError("");setSocialCopyGenerating(true);try{const response=await fetch(`${API_BASE}/api/tasks/${task.id}/social-copy/stream`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({preference:socialPreference})});if(!response.ok)throw new Error(`文案生成失败 (${response.status})`);if(!response.body)throw new Error("浏览器不支持流式响应");const reader=response.body.getReader();const decoder=new TextDecoder();while(true){const {done,value}=await reader.read();if(done)break;if(value)setSocialCopy(current=>current+decoder.decode(value,{stream:true}))}const tail=decoder.decode();if(tail)setSocialCopy(current=>current+tail)}catch(cause){setSocialCopyError(cause instanceof Error?cause.message:"文案生成失败")}finally{setSocialCopyGenerating(false)}};
  const copySocialCopy=async()=>{if(!socialCopy)return;await navigator.clipboard.writeText(socialCopy);setSocialCopyCopied(true);window.setTimeout(()=>setSocialCopyCopied(false),1600)};
  const markCustom=()=>{setActiveLayout(null);setDirty(true)};
  const applyRecommended=()=>{setRegions(defaults);setSubtitleY(20);setSubtitleSize(54);setTitleSize(50);setKeyPointsSize(40);setTitleShadow(true);setKeyPointsShadow(true);setTitleAlignment("left");setVisiblePageCount(totalPages);setFigureDuration(5);setBackgroundColor("#ebe8df");setLayerOrder(["page","key_points","figures","title"]);setActiveLayout("recommended");setDirty(true)};
  const applyTemplate=(index:number)=>{const item=templates[index];if(!item)return;setRegions(item.regions);setSubtitleY(item.subtitleY);setSubtitleSize(Math.min(item.subtitleSize,76));setTitleSize(item.titleSize??42);setKeyPointsSize(item.keyPointsSize??36);setTitleShadow(item.titleShadow??true);setKeyPointsShadow(item.keyPointsShadow??true);setTitleAlignment(item.titleAlignment??"left");setVisiblePageCount(Math.max(1,Math.min(item.visiblePageCount??totalPages,totalPages)));setFigureDuration(item.figureDuration??3);setBackgroundColor(item.backgroundColor??"#ebe8df");setLayerOrder(item.layerOrder);setActiveLayout(`template${index+1}` as LayoutPreset);setDirty(true)};
  const saveAsTemplate=()=>{let index=templates.findIndex(item=>!item);if(index<0&&activeLayout?.startsWith("template"))index=Number(activeLayout.slice(-1))-1;if(index<0)index=2;const next=[...templates];next[index]={regions,subtitleY,subtitleSize,titleSize,keyPointsSize,titleShadow,keyPointsShadow,titleAlignment,visiblePageCount,figureDuration,backgroundColor,layerOrder};setTemplates(next);window.localStorage.setItem("essaypod-video-layout-templates",JSON.stringify(next));setActiveLayout(`template${index+1}` as LayoutPreset);setTemplateSaved(true);window.setTimeout(()=>setTemplateSaved(false),1800)};
  const deleteTemplate=(index:number)=>{const next=[...templates];next[index]=null;setTemplates(next);window.localStorage.setItem("essaypod-video-layout-templates",JSON.stringify(next));if(activeLayout===`template${index+1}`)setActiveLayout(null)};
  const selectLayer=(name:StudioLayer)=>{setSelectedLayer(name);if(name!=="subtitle"&&name!=="background")setSelected(name)};
  const moveLayer=(direction:1|-1)=>{if(selectedLayer==="subtitle"||selectedLayer==="background")return;const index=layerOrder.indexOf(selectedLayer);const target=index+direction;if(index<0||target<0||target>=layerOrder.length)return;const next=[...layerOrder];[next[index],next[target]]=[next[target],next[index]];setLayerOrder(next);markCustom()};
  const start = (e:PointerEvent,name:RegionName,mode:"move"|"resize") => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); drag.current={name,sx:e.clientX,sy:e.clientY,origin:{...regions[name]},mode};setSnapGuides({x:false,y:false});selectLayer(name); };
  const move = (e:PointerEvent) => { if(!drag.current||!stageRef.current)return; const d=drag.current; const scale=stageRef.current.clientWidth/1920; const dx=(e.clientX-d.sx)/scale,dy=(e.clientY-d.sy)/scale; const n={...d.origin}; if(d.mode==="move"){n.x=Math.max(0,Math.min(1920-n.w,n.x+dx));n.y=Math.max(0,Math.min(1080-n.h,n.y+dy));const threshold=14/scale;const snapX=Math.abs(n.x+n.w/2-960)<=threshold;const snapY=Math.abs(n.y+n.h/2-540)<=threshold;if(snapX)n.x=960-n.w/2;if(snapY)n.y=540-n.h/2;setSnapGuides({x:snapX,y:snapY});}else{n.w=Math.max(80,Math.min(1920-n.x,n.w+dx));n.h=Math.max(80,Math.min(1080-n.y,n.h+dy));setSnapGuides({x:false,y:false});} setRegions({...regions,[d.name]:{x:Math.round(n.x),y:Math.round(n.y),w:Math.round(n.w),h:Math.round(n.h)}});markCustom(); };
  const endDrag=()=>{drag.current=null;setSnapGuides({x:false,y:false})};
  const updateSubtitleY=(value:number)=>{setSubtitleY(value);markCustom()};
  const updateSubtitleSize=(value:number)=>{setSubtitleSize(value);markCustom()};
  return <div className="studio-shell">
    <header className="studio-toolbar"><div><span className="section-kicker">VIDEO STUDIO</span><h2>编排论文视频</h2></div><div className="format-toggle"><button className={format==="16:9"?"active":""} onClick={()=>setFormat("16:9")}>横屏 16:9</button><button className={format==="9:16"?"active":""} onClick={()=>setFormat("9:16")}>竖屏 9:16</button></div><div className="header-generate"><span className={dirty?"unsaved":""}>{isVideoGenerating?(videoState?.message||"正在生成视频"):dirty?"● 有未保存修改":"✓ 布局已保存"}</span><button className="generate-video" disabled={isVideoGenerating} onClick={generateVideo}>{isVideoGenerating?`${videoProgress}%`:"生成视频"} <b>→</b></button></div></header>
    {isVideoGenerating&&<div className="studio-progress-line" aria-label={`视频生成进度 ${videoProgress}%`}><i style={{width:`${videoProgress}%`}} /></div>}
    {videoState?.status === "interrupted" && <section className="video-interrupted"><span>上一次生成因服务重启而中断，没有继续占用资源。</span><button onClick={generateVideo}>重新生成</button></section>}
    {format==="9:16" ? <div className="vertical-empty"><div className="phone-frame"><span>9:16</span><b>论文短视频</b><MiniWave active/></div><div><span className="section-kicker">AUTO LAYOUT</span><h2>竖屏视频将自动编排</h2><p>当前后端仅支持 16:9 自定义布局。竖屏模式会自动排列 PDF、板书和字幕。请使用右上角按钮生成视频。</p></div></div> :
    <div className="editor-grid">
      <aside className="layers"><header><b>画面图层</b><span>6</span></header>{[...layerOrder].reverse().map((name,i)=><button className={selectedLayer===name?"active":""} key={name} onClick={()=>selectLayer(name)}><i>{layerOrder.length-i}</i><span><b>{regionLabels[name]}</b><small>{Math.round(regions[name].w)} × {Math.round(regions[name].h)}</small></span><em>◉</em></button>)}<button className={`subtitle-layer ${selectedLayer==="subtitle"?"active":""}`} onClick={()=>selectLayer("subtitle")}><i>CC</i><span><b>底部字幕</b><small>固定在画面最上层</small></span><em>可调</em></button><button className={`background-layer ${selectedLayer==="background"?"active":""}`} onClick={()=>selectLayer("background")}><i style={{background:backgroundColor}}/><span><b>画布背景</b><small>固定在画面最底层</small></span><em>锁定</em></button></aside>
      <section className="canvas-area"><div className="ruler"><span>50%</span><button>−</button><button>＋</button><button>适应画布</button></div><div className="canvas-wrap"><div className="canvas" ref={stageRef} style={{backgroundColor}} onPointerMove={move} onPointerUp={endDrag} onPointerCancel={endDrag}>
        {snapGuides.x&&<i className="snap-guide snap-guide-x"><span>水平居中</span></i>}{snapGuides.y&&<i className="snap-guide snap-guide-y"><span>垂直居中</span></i>}
        {(Object.keys(regions) as RegionName[]).map(name=>{const r=regions[name];return <div key={name} className={`region region-${name} ${selectedLayer===name?"selected":""}`} style={{left:`${r.x/19.2}%`,top:`${r.y/10.8}%`,width:`${r.w/19.2}%`,height:`${r.h/10.8}%`,zIndex:layerOrder.indexOf(name)+1,containerType:(name==="title"||name==="key_points")?"size":undefined}} onPointerDown={e=>start(e,name,"move")}><label>{regionLabels[name]}</label><RegionContent name={name} title={task.display_title || task.title} titleSize={titleSize} keyPointsSize={keyPointsSize} titleShadow={titleShadow} keyPointsShadow={keyPointsShadow} titleAlignment={titleAlignment} regionWidth={r.w}/><button className="resize" aria-label="调整大小" onPointerDown={e=>{e.stopPropagation();start(e,name,"resize")}} /></div>})}
        <div className={`subtitle-preview ${selectedLayer==="subtitle"?"selected":""}`} onPointerDown={()=>selectLayer("subtitle")} style={{bottom:`${subtitleY/10.8}%`,fontSize:`max(6px, ${subtitleSize/19.2}cqw)`}}>真正的推理，必须能够迁移到没有见过的问题上。</div></div></div><footer><div className="layout-presets"><button className={activeLayout==="recommended"?"active":""} onClick={applyRecommended}><PresetThumb regions={defaults}/><span><b>推荐布局</b><small>三栏论文解读</small></span></button>{templates.map((item,index)=><div className="template-slot" key={index}><button disabled={!item} className={`preset-button ${item?"":"empty"} ${activeLayout===`template${index+1}`?"active":""}`} onClick={()=>applyTemplate(index)}><PresetThumb regions={item?.regions} empty={!item}/><span><b>模板 {index+1}</b><small>{item?"自定义布局":"等待保存"}</small></span></button>{item&&<button className="template-delete" aria-label={`删除模板 ${index+1}`} title={`删除模板 ${index+1}`} onClick={()=>deleteTemplate(index)}>×</button>}</div>)}</div><div className="layout-actions"><button onClick={saveAsTemplate}>{templateSaved?"✓ 已保存":"保存为模板"}</button><button className="layout-save" onClick={saveLayout}>保存布局</button></div></footer></section>
      <aside className="properties contextual-properties"><header><b>{selectedLayer==="subtitle"?"字幕样式":selectedLayer==="background"?"画布背景":regionLabels[selectedLayer]}</b><span>所见即所得 · 生成视频时同步应用</span></header>
        {selectedLayer==="subtitle"&&<><RangeControl id="subtitle-y" label="垂直位置" value={subtitleY} min={20} max={360} step={4} suffix="px" hint="向右拖动，字幕会向上移动" onChange={updateSubtitleY}/><RangeControl id="subtitle-size" label="字幕大小" value={subtitleSize} min={28} max={76} step={2} suffix="px" hint="预览按最终视频比例实时缩放" onChange={updateSubtitleSize}/></>}
        {selectedLayer==="title"&&<><RangeControl id="title-size" label="标题字号" value={titleSize} min={24} max={88} step={2} suffix="px" hint="标题会按区域宽度自适应换行" onChange={value=>{setTitleSize(value);markCustom()}}/><AlignmentControl value={titleAlignment} onChange={value=>{setTitleAlignment(value);markCustom()}}/><SwitchControl label="柔和文字阴影" checked={titleShadow} onChange={value=>{setTitleShadow(value);markCustom()}}/></>}
        {selectedLayer==="key_points"&&<><RangeControl id="key-points-size" label="板书整体字号" value={keyPointsSize} min={22} max={64} step={2} suffix="px" hint="小标题与描述按比例缩放，并随区域宽度换行" onChange={value=>{setKeyPointsSize(value);markCustom()}}/><SwitchControl label="柔和文字阴影" checked={keyPointsShadow} onChange={value=>{setKeyPointsShadow(value);markCustom()}}/></>}
        {selectedLayer==="page"&&<PageCountControl value={visiblePageCount} total={totalPages} onChange={value=>{setVisiblePageCount(value);markCustom()}}/>}
        {selectedLayer==="figures"&&<><RangeControl id="figure-duration" label="单图停留时间" value={figureDuration} min={1} max={12} step={0.5} suffix="s" hint="若剩余页面时间不足，图片会在页面切换前提前结束" onChange={value=>{setFigureDuration(value);markCustom()}}/><div className="property-note"><b>聚焦展示</b><p>图片从所属页面时长的 1/4 处淡入，等比填满图表区域；PDF 页面会同步压暗 20%。</p></div></>}
        {selectedLayer==="background"&&<BackgroundControl value={backgroundColor} onChange={value=>{setBackgroundColor(value);markCustom()}}/>}
        <div className="tip"><b>{selectedLayer==="subtitle"?"字幕安全区":"编辑提示"}</b><p>{selectedLayer==="subtitle"?"建议垂直位置保持在 40–180 px，避免播放器控件或平台裁切遮挡字幕。":"画布中的移动、缩放和样式变化都会保存到当前论文布局，也可另存为通用模板。"}</p></div><div className="layer-controls"><div><b>图层顺序</b><span>{selectedLayer==="subtitle"?"字幕固定在最上层":selectedLayer==="background"?"背景固定在最底层":regionLabels[selectedLayer]}</span></div><button disabled={selectedLayer==="subtitle"||selectedLayer==="background"||layerOrder.indexOf(selectedLayer)===layerOrder.length-1} onClick={()=>moveLayer(1)}>上移一层 ↑</button><button disabled={selectedLayer==="subtitle"||selectedLayer==="background"||layerOrder.indexOf(selectedLayer)===0} onClick={()=>moveLayer(-1)}>下移一层 ↓</button></div></aside>
    </div>}
    <><section className="video-result"><span className="section-kicker">VIDEO OUTPUT</span><div className="video-result-grid with-cover"><article className="video-preview-panel"><header><h2>视频预览</h2>{videoReady&&<a href={`${taskVideoUrl(task.id,format)}&download=1`}>下载视频 ↓</a>}</header><div className={`result-media-frame ${videoReady?"":"empty"}`}>{videoReady?<video controls preload="metadata" src={`${taskVideoUrl(task.id,format)}&v=${task.updated_at}`} />:<div className="media-placeholder"><i>▶</i><b>{isVideoGenerating?"视频正在生成":"暂无视频"}</b><span>{isVideoGenerating?"生成完成后将在这里自动显示":"请先在右上角生成视频"}</span></div>}</div></article><article className="cover-preview-panel"><header><h2>封面推荐</h2>{videoReady&&format==="16:9"&&<a href={`${taskCoverUrl(task.id,"3:4")}&download=1`}>下载封面 ↓</a>}</header><div className={`result-media-frame ${videoReady&&format==="16:9"?"":"empty"}`}>{videoReady&&format==="16:9"?<img src={`${taskCoverUrl(task.id,"3:4")}&v=${task.updated_at}`} alt="3:4 社交媒体封面"/>:<div className="media-placeholder"><i>◇</i><b>{isVideoGenerating?"封面正在生成":"暂无封面"}</b><span>{format==="16:9"?(isVideoGenerating?"将与视频一起生成":"请先生成横屏视频"):"封面推荐适用于横屏视频"}</span></div>}</div></article></div></section><section className="social-copy-assistant"><header><div><span className="section-kicker">COPY ASSISTANT</span><h2>文案助手</h2><p>根据论文导读与播客内容，生成适合新媒体发布的视频标题、正文和标签。</p></div><button className="social-copy-generate" disabled={socialCopyGenerating||!videoReady} onClick={generateSocialCopy}>{socialCopyGenerating?"正在生成…":!videoReady?"请先生成视频":socialCopy?"重新生成":"生成发布文案"} <b>→</b></button></header><label className="social-preference"><span>你的偏好 <small>选填</small></span><textarea value={socialPreference} onChange={event=>setSocialPreference(event.target.value)} maxLength={1000} placeholder="例如：更像小红书风格；突出实验数据；语气专业克制；面向刚入门的研究生……"/></label>{!videoReady&&<p className="social-copy-waiting">视频生成完成后即可使用文案助手。</p>}{socialCopyError&&<p className="social-copy-error">{socialCopyError}</p>}{(socialCopy||socialCopyGenerating)&&<div className="social-copy-output"><div><span>{socialCopyGenerating?"正在总结论文":"生成完成"}</span><button className="social-copy-copy" disabled={!socialCopy} onClick={copySocialCopy} aria-label={socialCopyCopied?"已复制":"复制文案"} title={socialCopyCopied?"已复制":"复制文案"}>{socialCopyCopied?<span className="copy-success">✓</span>:<i className="copy-icon" aria-hidden/>}</button></div><pre>{socialCopy}{socialCopyGenerating&&<i className="stream-caret" aria-hidden/>}</pre></div>}</section></>
  </div>;
}

function RangeControl({id,label,value,min,max,step,suffix,hint,onChange}:{id:string;label:string;value:number;min:number;max:number;step:number;suffix:string;hint:string;onChange:(value:number)=>void}) { return <div className="subtitle-control"><div><label htmlFor={id}>{label}</label><output>{value}{suffix}</output></div><input id={id} type="range" min={min} max={max} step={step} value={value} onChange={e=>onChange(Number(e.target.value))}/><p>{hint}</p></div> }

function SwitchControl({label,checked,onChange}:{label:string;checked:boolean;onChange:(value:boolean)=>void}) { return <label className="property-switch"><span><b>{label}</b><small>为文字增加轻微向下的虚化层次</small></span><input type="checkbox" checked={checked} onChange={e=>onChange(e.target.checked)}/><i aria-hidden/></label> }

function AlignmentControl({value,onChange}:{value:TitleAlignment;onChange:(value:TitleAlignment)=>void}) { return <div className="alignment-control"><b>文字对齐</b><div>{([{value:"left",label:"左对齐",icon:"≡"},{value:"center",label:"居中",icon:"≡"},{value:"right",label:"右对齐",icon:"≡"}] as const).map(item=><button key={item.value} className={`${item.value} ${value===item.value?"active":""}`} aria-label={item.label} title={item.label} onClick={()=>onChange(item.value)}><i>{item.icon}</i><span>{item.label}</span></button>)}</div></div> }

function PageCountControl({value,total,onChange}:{value:number;total:number;onChange:(value:number)=>void}) { return <div className="page-count-control"><div><b>页面播放范围</b><output>播放至第 {value} 页</output></div><input type="range" min="1" max={Math.max(1,total)} step="1" value={Math.min(value,total)} onChange={e=>onChange(Number(e.target.value))}/><div className="page-range-labels"><span>第 1 页</span><span>共 {total} 页</span></div><p>播客时长内依次展示前 {value} 页，并自动安排页面切换时间。</p></div> }

const backgroundPresets=["#ebe8df","#f7f5ef","#ffffff","#e8e5f3","#dde7e4","#202026"];
function BackgroundControl({value,onChange}:{value:string;onChange:(value:string)=>void}) { return <div className="background-control"><b>背景颜色</b><div>{backgroundPresets.map(color=><button key={color} className={value.toLowerCase()===color?"active":""} style={{backgroundColor:color}} aria-label={`背景颜色 ${color}`} onClick={()=>onChange(color)}/>)}</div><label><span>自定义</span><input type="color" value={value} onChange={e=>onChange(e.target.value)}/><output>{value.toUpperCase()}</output></label></div> }

function RegionContent({name,title,titleSize,keyPointsSize,titleShadow,keyPointsShadow,titleAlignment,regionWidth}:{name:RegionName;title:string;titleSize:number;keyPointsSize:number;titleShadow:boolean;keyPointsShadow:boolean;titleAlignment:TitleAlignment;regionWidth:number}) { const previewFont=(fontSize:number)=>`${fontSize/Math.max(regionWidth,1)*100}cqw`; if(name==="title")return <h3 className={titleShadow?"text-shadow-on":""} style={{fontFamily:'Inter,"PingFang SC","Noto Sans CJK SC",sans-serif',fontSize:previewFont(titleSize),lineHeight:1.12,margin:0,overflowWrap:"anywhere",wordBreak:"normal",textAlign:titleAlignment}}>{title}</h3>; if(name==="key_points")return <ul className={keyPointsShadow?"text-shadow-on":""} style={{fontSize:previewFont(keyPointsSize)}}><li><b>推理 ≠ 模仿</b><span>关键在于分布外迁移</span></li><li><b>过程级反馈</b><span>验证每一步思维路径</span></li><li><b>18.7% 提升</b><span>困难任务表现更稳定</span></li></ul>; if(name==="page")return <><div className="page-scroll-badge">↓ 自动滚动</div><div className="fake-page"><small>TEACHING LANGUAGE MODELS TO REASON</small><b>1. Introduction</b>{[1,2,3,4,5,6,7,8,9,10,11,12,13,14].map(i=><i key={i}/>)}</div></>; return <div className="fake-chart"><b>Reasoning Accuracy</b><div><i style={{height:"42%"}}/><i style={{height:"61%"}}/><i style={{height:"84%"}}/></div><small>Baseline　SFT　Ours</small></div> }

function PresetThumb({regions,empty=false}:{regions?:Record<RegionName,Region>;empty?:boolean}) { if(empty||!regions)return <i className="preset-thumb empty-thumb">＋</i>; return <i className="preset-thumb">{(["title","key_points","page","figures"] as RegionName[]).map(name=>{const r=regions[name];return <em key={name} className={`mini-${name}`} style={{left:`${r.x/19.2}%`,top:`${r.y/10.8}%`,width:`${r.w/19.2}%`,height:`${r.h/10.8}%`}}/>})}</i> }

function ImportModal({onClose,onCreated}:{onClose:()=>void;onCreated:(task:ApiTask)=>void}) {
  const [tab,setTab]=useState<"file"|"url">("file");
  const [file,setFile]=useState<File|null>(null);
  const [dragging,setDragging]=useState(false);
  const [url,setUrl]=useState("");
  const [level,setLevel]=useState<"low"|"mid"|"high">("mid");
  const [includeInKnowledgeGraph,setIncludeInKnowledgeGraph]=useState(true);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  const fileInputRef=useRef<HTMLInputElement|null>(null);
  const selectPdf=(candidate:File|null)=>{
    if(!candidate)return;
    const isPdf=candidate.type==="application/pdf"||candidate.name.toLowerCase().endsWith(".pdf");
    if(!isPdf){setMessage("请选择 PDF 文件");return}
    if(candidate.size>50*1024*1024){setMessage("PDF 文件不能超过 50 MB");return}
    setMessage("");
    setFile(candidate);
  };
  const handleDrop=(event:DragEvent<HTMLLabelElement>)=>{
    event.preventDefault();
    event.stopPropagation();
    setDragging(false);
    selectPdf(event.dataTransfer.files?.[0]||null);
  };
  const submit=async()=>{setBusy(true);setMessage("");try{const form=new FormData();form.append("condense_level",level);form.append("include_in_knowledge_graph",String(includeInKnowledgeGraph));let task:ApiTask;if(tab==="file"){if(!file)throw new Error("请选择 PDF 文件");form.append("file",file);task=await request<ApiTask>("/api/tasks",{method:"POST",body:form});task=await request<ApiTask>(`/api/tasks/${task.id}/generate`,{method:"POST"})}else{if(!url.trim())throw new Error("请输入论文链接");form.append("pdf_url",url.trim());task=await request<ApiTask>("/api/tasks/from-url",{method:"POST",body:form})}onCreated(task)}catch(cause){setMessage(cause instanceof Error?cause.message:"导入失败")}finally{setBusy(false)}};
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal import-modal" onMouseDown={e=>e.stopPropagation()}><header><div><span className="section-kicker">NEW PROJECT</span><h2>导入一篇论文</h2><p>添加论文后将自动提炼内容并生成播客。</p></div><button className="modal-close" onClick={onClose}>×</button></header><div className="import-tabs"><button className={tab==="file"?"active":""} onClick={()=>setTab("file")}>从电脑上传</button><button className={tab==="url"?"active":""} onClick={()=>setTab("url")}>使用论文链接</button></div>{tab==="file"?<label className={`dropzone ${file?"has-file":""} ${dragging?"is-dragging":""}`} role="button" tabIndex={0} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();fileInputRef.current?.click()}}} onDragEnter={e=>{e.preventDefault();e.stopPropagation();setDragging(true)}} onDragOver={e=>{e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect="copy";setDragging(true)}} onDragLeave={e=>{e.preventDefault();e.stopPropagation();setDragging(false)}} onDrop={handleDrop}><span>{file?"✓":"↥"}</span><h3>{file?.name||"拖入或选择 PDF"}</h3><p>{file?`${(file.size/1024/1024).toFixed(1)} MB · 已准备导入`:"支持 PDF，单个文件最大 50 MB"}</p><em>{file?"更换文件":"浏览文件"}</em><input ref={fileInputRef} className="file-input" type="file" accept=".pdf,application/pdf" onChange={e=>{selectPdf(e.target.files?.[0]||null);e.currentTarget.value=""}}/></label>:<div className="url-form"><label>PDF 或 arXiv 链接</label><input value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://arxiv.org/pdf/2607.12790"/><p>EssayPod 将自动下载 PDF 并读取论文信息。</p></div>}<div className="levels"><label>内容处理方式 <span>决定播客的信息密度</span></label><div><button className={level==="low"?"active":""} onClick={()=>setLevel("low")}><i>01</i><b>快速浏览</b><span>聚焦核心观点</span></button><button className={level==="mid"?"active":""} onClick={()=>setLevel("mid")}><i>02</i><b>标准解读</b><span>平衡细节与时长</span><em>推荐</em></button><button className={level==="high"?"active":""} onClick={()=>setLevel("high")}><i>03</i><b>完整内容</b><span>尽可能保留全文</span></button></div></div><label className={`knowledge-graph-option ${includeInKnowledgeGraph?"enabled":""}`}><span className="knowledge-graph-copy"><b>加入知识图谱</b><small>{includeInKnowledgeGraph?"解析概念、方法和证据关系，并增量更新全局图谱":"仅生成摘要、播客与翻译，不会编译 Wiki 或更新图谱"}</small></span><input type="checkbox" role="switch" aria-label="加入知识图谱" checked={includeInKnowledgeGraph} onChange={event=>setIncludeInKnowledgeGraph(event.target.checked)}/><i aria-hidden="true"/></label>{message&&<p className="form-error">{message}</p>}<footer><small>{includeInKnowledgeGraph?"创建后将立即处理并更新图谱":"创建后立即处理，但不更新知识图谱"}</small><button onClick={onClose}>取消</button><button className="primary" disabled={busy} onClick={submit}>{busy?"正在导入…":"导入并开始 →"}</button></footer></section></div>
}
