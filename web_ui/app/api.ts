export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8010";

export type ApiTask = {
  id: string;
  title: string;
  display_title?: string;
  filename: string;
  source_url?: string;
  status: "downloading" | "uploaded" | "generating" | "pausing" | "paused" | "ready" | "failed";
  error?: string;
  podcast_stage?: "queued" | "processing" | "paused" | "ready" | "failed";
  import_stage?: "queued" | "resolving" | "downloading" | "processing" | "failed";
  import_message?: string;
  import_active?: boolean;
  requested_url?: string;
  created_at: number;
  updated_at: number;
  /** Optional only for legacy task files; the backend normalizes it to "mid". */
  condense_level?: "low" | "mid" | "high";
  /** Legacy tasks default to true; new imports persist the user's choice. */
  include_in_knowledge_graph?: boolean;
  condensed_key_info?: string;
  audio_ready: boolean;
  partial_ready: boolean;
  buffered_seconds?: number;
  stream_ready?: boolean;
  live_dvr?: {
    ready: boolean;
    live: boolean;
    ended: boolean;
    duration: number;
    segment_count: number;
    target_duration: number;
    status: string;
    error?: string;
    updated_at?: number;
  };
  pdf_ready: boolean;
  transcript_ready: boolean;
  video?: Record<"16:9" | "9:16", { status: string; ready: boolean; error?: string; progress?: number; stage?: string; message?: string }>;
  brief?: { status: "idle" | "generating" | "ready" | "failed"; ready: boolean; error?: string };
  translation?: { status: "idle" | "generating" | "ready" | "failed" | "interrupted"; ready: boolean; source_ready: boolean; target_ready: boolean; pdf_status?: "idle" | "generating" | "ready" | "failed" | "interrupted"; pdf_ready?: boolean; pdf_error?: string; reading_pdf_status?: "idle" | "generating" | "ready" | "failed" | "interrupted"; reading_pdf_ready?: boolean; reading_pdf_error?: string; deepl_pdf_status?: "idle" | "queued" | "generating" | "ready" | "failed" | "interrupted"; deepl_pdf_ready?: boolean; deepl_pdf_active?: boolean; deepl_pdf_error?: string; deepl_pdf_message?: string; deepl_character_count?: number | null; deepl_character_limit?: number | null; error?: string; message?: string };
  chatbot?: { status: "idle" | "building" | "ready" | "failed"; ready: boolean; error?: string; message?: string; chunk_count?: number; block_count?: number };
  llm_wiki?: {
    status: "idle" | "queued" | "running" | "interrupted" | "completed" | "failed";
    stage?: string;
    message?: string;
    progress?: number;
    heartbeat_at?: string;
    retry_count?: number;
    page?: string;
    error?: string;
  };
};

export type VideoLayout = {
  canvas_width: number;
  canvas_height: number;
  regions: Record<"title" | "key_points" | "page" | "figures", { x:number; y:number; w:number; h:number }>;
  subtitle?: { margin_v: number; font_size: number };
  typography?: { title_font_size: number; key_points_font_size: number; title_shadow?: boolean; key_points_shadow?: boolean; title_alignment?: "left" | "center" | "right" };
  background?: { color: string };
  pdf?: { visible_page_count: number };
  figures?: { display_duration: number };
  z_order?: Array<"title" | "key_points" | "page" | "figures">;
  layout_source?: "recommended" | "template1" | "template2" | "template3" | "custom";
};

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...init, cache: "no-store" });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const taskAudioUrl = (id: string) => `${API_BASE}/api/tasks/${id}/audio`;
export const taskLiveAudioUrl = (id: string) => `${API_BASE}/api/tasks/${id}/audio-live`;
export const taskDvrPlaylistUrl = (id: string) => `${API_BASE}/api/tasks/${id}/live-dvr/stream.m3u8`;
export const taskPdfUrl = (id: string) => `${API_BASE}/api/tasks/${id}/pdf`;
export const taskFigureUrl = (id: string, figureId: string) => `${API_BASE}/api/tasks/${id}/figures/${encodeURIComponent(figureId)}`;
export const taskTranslationAssetUrl = (id: string, assetPath: string) => `${API_BASE}/api/tasks/${id}/translation/assets/${assetPath.split("/").map(encodeURIComponent).join("/")}`;
export const taskTranslationPdfUrl = (id: string) => `${API_BASE}/api/tasks/${id}/translation/pdf`;
export const taskTranslationReadingPdfUrl = (id: string) => `${API_BASE}/api/tasks/${id}/translation/reading-pdf`;
export const taskTranslationDeeplPdfUrl = (id: string) => `${API_BASE}/api/tasks/${id}/translation/pdf?provider=deepl`;
export const taskVideoUrl = (id: string, format: "16:9" | "9:16") => `${API_BASE}/api/tasks/${id}/video?video_format=${encodeURIComponent(format)}`;
export const taskCoverUrl = (id: string, ratio: "16:9" | "3:4") => `${API_BASE}/api/tasks/${id}/video/cover?ratio=${encodeURIComponent(ratio)}`;
