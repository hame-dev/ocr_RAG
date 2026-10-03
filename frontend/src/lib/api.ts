export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public body?: unknown) {
    super(detail);
  }
}

// ---- Session + CSRF ----------------------------------------------------------
//
// Auth is Django's HttpOnly session cookie, sent with `credentials: "include"`.
// The API is a different origin, so the csrftoken cookie cannot be relied on
// from JS; the backend returns the token in JSON and it is kept in memory here.

export const UNAUTHORIZED_EVENT = "auth:unauthorized";

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export async function ensureCsrf(force = false): Promise<string> {
  if (csrfToken && !force) return csrfToken;
  const res = await fetch(`${API_BASE}/api/auth/csrf/`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, "could not obtain a CSRF token");
  csrfToken = ((await res.json()) as { csrfToken: string }).csrfToken;
  return csrfToken;
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** Headers every state-changing request needs. Also used by the SSE client. */
export async function authHeaders(method = "GET", force = false): Promise<Record<string, string>> {
  if (SAFE_METHODS.has(method.toUpperCase())) return {};
  return { "X-CSRFToken": await ensureCsrf(force) };
}

export function notifyUnauthorized() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
}

async function errorFrom(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  let body: unknown;
  try {
    body = await res.json();
    const record = body as Record<string, unknown>;
    if (typeof record?.detail === "string") {
      detail = record.detail;
    } else if (record && typeof record === "object") {
      // DRF field errors: {"field": ["message", ...]}
      const first = Object.values(record).flat()[0];
      if (typeof first === "string") detail = first;
    }
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, detail, body);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";

  const send = async (forceCsrf: boolean) =>
    fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...(await authHeaders(method, forceCsrf)),
        ...init?.headers,
      },
    });

  let res = await send(false);
  // A 403 on a write is usually a stale CSRF token (it rotates on login and
  // logout); refresh it once and retry before surfacing the error.
  if (res.status === 403 && !SAFE_METHODS.has(method.toUpperCase())) {
    res = await send(true);
  }

  if (!res.ok) {
    if (res.status === 401) notifyUnauthorized();
    throw await errorFrom(res);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---- Types -----------------------------------------------------------------

export type DocStatus =
  | "uploaded" | "preprocessing" | "preprocessed" | "ocr_running" | "ocr_partial"
  | "ocr_done" | "ocr_failed" | "text_finalized" | "enriching" | "enriched"
  | "indexing" | "indexed" | "ready" | "failed";

export interface Engine {
  name: string;
  display_name_en: string;
  display_name_ar: string;
  description_en: string;
  description_ar: string;
  tier: "native" | "classical" | "neural" | "vlm";
  supported_languages: string[];
  supports_boxes: boolean;
  supports_confidence: boolean;
  est_seconds_per_page: number;
  available: boolean;
  detail: string;
  version: string | null;
  model_id: string | null;
}

export interface DocumentSummary {
  id: string;
  display_title: string;
  title: string;
  original_filename: string;
  page_count: number | null;
  status: DocStatus;
  status_detail: string;
  is_digital_pdf: boolean | null;
  detected_languages: string[];
  doc_type: string;
  summary_short: string;
  /** .xlsx / .csv: no OCR; columns are reviewed instead. */
  is_spreadsheet: boolean;
  created_at: string;
}

export interface PageInfo {
  page_number: number;
  width_px: number | null;
  height_px: number | null;
  has_raster: boolean;
}

export interface DocumentDetail extends DocumentSummary {
  digital_text_report: { reason?: string } | null;
  error_code: string;
  error_message: string;
  metadata_mode: string;
  pages: PageInfo[];
  current_revision_no: number | null;
  revision_count: number;
}

export interface Revision {
  id: string;
  revision_no: number;
  source: string;
  char_count: number;
  word_count: number;
  note: string;
  is_final: boolean;
  created_at: string;
  text: string;
}

export interface ComparisonColumn {
  run_id: string;
  engine: string;
  text: string;
  confidence: number | null;
  gibberish_score: number | null;
  char_count: number;
  duration_ms: number | null;
  warnings: string[];
}

export interface Comparison {
  batch_id: string;
  baseline_run_id: string | null;
  ranking: string[];
  pages: {
    page_number: number;
    columns: ComparisonColumn[];
    consensus: { locked_spans: number[][]; agreement: number };
  }[];
}

export interface OcrRun {
  run_id?: string;
  id?: string;
  engine: string;
  engine_name?: string;
  status: string;
  duration_ms: number | null;
  mean_confidence: number | null;
  gibberish_score: number | null;
  char_count: number;
  warnings: string[];
  error_code?: string;
  error_message?: string;
  preview: string;
}

export interface Citation {
  n: number;
  chunk_id: string;
  document_id: string;
  title: string;
  page_start: number | null;
  page_end: number | null;
  quote: string;
  /** Set for a spreadsheet row: shown as "<sheet> · Row <n>" instead of a page. */
  sheet?: string;
  row?: number;
}

export type ResearchMode = "fast" | "balanced" | "deep";
export type ChatMode = "documents" | "general";
export type ThinkingMode = "instant" | "think" | "deep";

export interface ChatAttachment {
  id: string;
  filename: string;
  kind: "image" | "pdf" | "text";
  size: number;
  pages: number | null;
  has_preview: boolean;
  preview_url: string | null;
}

/** A file General-mode code produced: a chart, spreadsheet, Word or PowerPoint file. */
export interface GeneratedFile {
  id: string;
  filename: string;
  kind: "image" | "document" | "spreadsheet" | "presentation" | "data";
  mime: string;
  size: number;
  /** Raster images render in the page; everything else is a download. */
  inline: boolean;
  url: string;
}

/**
 * One run_python call (the code, what it printed, the files it saved), or one
 * query_spreadsheet call (the SQL as `code`, the result table as `stdout`).
 */
export interface CodeRun {
  name: "run_python" | "query_spreadsheet";
  code: string;
  ok: boolean;
  stdout: string;
  stderr: string;
  duration_ms: number | null;
  file_ids: string[];
}

/** What a finished deep-think / deep-research run did, stored with the answer. */
export interface PhaseSummary {
  steps: string[];
  queries: string[];
  sources: number | null;
}

export interface SelectedDocument {
  id: string;
  display_title: string;
  original_filename: string;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  scope: "all" | "selected";
  document_ids: string[];
  selected_documents: SelectedDocument[];
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  seq: number;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  citations: Citation[];
  citation_mode: string;
  chat_mode: ChatMode;
  thinking: ThinkingMode;
  reasoning: string;
  thinking_ms: number | null;
  follow_ups: string[];
  phases: PhaseSummary | null;
  attachments: ChatAttachment[];
  files: GeneratedFile[];
  tool_calls: CodeRun[];
  is_partial: boolean;
  error: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

export interface User {
  id: number;
  username: string;
  is_staff: boolean;
}

// ---- Auth ------------------------------------------------------------------

/** The current user, or null when there is no session. Never fires UNAUTHORIZED_EVENT. */
export async function getMe(): Promise<User | null> {
  const res = await fetch(`${API_BASE}/api/auth/me/`, { credentials: "include" });
  if (res.status === 401) return null;
  if (!res.ok) throw await errorFrom(res);
  const { user, csrfToken } = (await res.json()) as { user: User; csrfToken: string };
  setCsrfToken(csrfToken);
  return user;
}

export async function login(username: string, password: string): Promise<User> {
  const { user, csrfToken } = await api<{ user: User; csrfToken: string }>(
    "/api/auth/login/",
    { method: "POST", body: JSON.stringify({ username, password }) },
  );
  setCsrfToken(csrfToken);
  return user;
}

export async function logout(): Promise<void> {
  try {
    await api<void>("/api/auth/logout/", { method: "POST" });
  } finally {
    setCsrfToken(null);
  }
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const { csrfToken } = await api<{ csrfToken: string }>("/api/auth/password/", {
    method: "POST",
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  setCsrfToken(csrfToken);
}

// ---- Calls -----------------------------------------------------------------

export const listDocuments = () =>
  api<{ results: DocumentSummary[] }>("/api/documents/");

export const getDocument = (id: string) => api<DocumentDetail>(`/api/documents/${id}/`);

export const getRevision = (documentId: string, revisionNo: number) =>
  api<Revision>(`/api/documents/${documentId}/revisions/${revisionNo}/`);

export const retryDocument = (documentId: string, stage: "preprocess" | "enrich" | "index") =>
  api<{ status: string }>(`/api/documents/${documentId}/retry/`, {
    method: "POST",
    body: JSON.stringify({ stage }),
  });

export const listConversations = () =>
  api<{ results: ConversationSummary[] }>("/api/conversations/");

export const listEngines = (refresh = false) =>
  api<{ engines: Engine[]; default_engine: string }>(
    `/api/ocr/engines/${refresh ? "?refresh=1" : ""}`,
  );

export const uploadDocument = (file: File, title?: string) => {
  const form = new FormData();
  form.append("file", file);
  if (title) form.append("title", title);
  return api<DocumentSummary>("/api/documents/", { method: "POST", body: form });
};

export const startOcr = (documentId: string, engines: string[], languages: string[]) =>
  api<any>(`/api/documents/${documentId}/ocr/`, {
    method: "POST",
    body: JSON.stringify({ engines, languages }),
  });

export const getComparison = (batchId: string) =>
  api<Comparison>(`/api/ocr/batches/${batchId}/compare/`);

export const selectRun = (documentId: string, runId: string) =>
  api<Revision>(`/api/documents/${documentId}/text/select/`, {
    method: "POST",
    body: JSON.stringify({ run_id: runId }),
  });

export const saveRevision = (documentId: string, text: string, note?: string) =>
  api<Revision>(`/api/documents/${documentId}/revisions/`, {
    method: "POST",
    body: JSON.stringify({ text, note }),
  });

export const finalizeText = (documentId: string, revisionNo?: number) =>
  api<any>(`/api/documents/${documentId}/text/finalize/`, {
    method: "POST",
    body: JSON.stringify(revisionNo ? { revision_no: revisionNo } : {}),
  });

export const getExtractionPlan = (documentId: string) =>
  api<any>(`/api/documents/${documentId}/extraction-plan/`);

export const enrich = (documentId: string, mode = "auto", requiredFields: unknown[] = []) =>
  api<any>(`/api/documents/${documentId}/enrich/`, {
    method: "POST",
    body: JSON.stringify({ mode, required_fields: requiredFields }),
  });

export const getMetadata = (documentId: string) =>
  api<any>(`/api/documents/${documentId}/metadata/`);

/** Deletes the chat with its messages, files and the agent's memory of it. */
export const deleteConversation = (conversationId: string) =>
  api<void>(`/api/conversations/${conversationId}/`, { method: "DELETE" });

export const createConversation = (scope: "all" | "selected", documentIds: string[] = []) =>
  api<ConversationDetail>("/api/conversations/", {
    method: "POST",
    body: JSON.stringify({ scope, document_ids: documentIds }),
  });

export const updateConversationScope = (
  conversationId: string,
  documentIds: string[],
) =>
  api<ConversationDetail>(`/api/conversations/${conversationId}/`, {
    method: "PATCH",
    body: JSON.stringify({
      scope: documentIds.length ? "selected" : "all",
      document_ids: documentIds,
    }),
  });

export const pageImageUrl = (documentId: string, page: number, profile = "neural") =>
  `${API_BASE}/api/documents/${documentId}/pages/${page}/image/?profile=${profile}`;

// ---- Chat attachments ---------------------------------------------------------

/**
 * Upload one file for the chat. XHR rather than fetch, because fetch cannot
 * report upload progress.
 */
export async function uploadAttachment(
  file: File,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<ChatAttachment> {
  try {
    return await sendAttachment(file, false, onProgress, signal);
  } catch (error) {
    // Stale CSRF token (rotated by a login elsewhere): refresh once and retry,
    // as api() and streamChat do.
    if (error instanceof ApiError && error.status === 403 && !signal?.aborted) {
      return sendAttachment(file, true, onProgress, signal);
    }
    throw error;
  }
}

async function sendAttachment(
  file: File,
  forceCsrf: boolean,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<ChatAttachment> {
  const headers = await authHeaders("POST", forceCsrf);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/chat/attachments/`);
    xhr.withCredentials = true;
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as ChatAttachment);
      if (xhr.status === 401) notifyUnauthorized();
      const detail = (body as { detail?: string } | null)?.detail ?? xhr.statusText;
      reject(new ApiError(xhr.status, detail, body));
    };
    // No detail: the caller shows its own translated message for status 0.
    xhr.onerror = () => reject(new ApiError(0, ""));
    xhr.onabort = () => reject(new ApiError(0, "aborted"));
    signal?.addEventListener("abort", () => xhr.abort());
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

export const deleteAttachment = (id: string) =>
  api<void>(`/api/chat/attachments/${id}/`, { method: "DELETE" });

export const attachmentPreviewUrl = (attachment: ChatAttachment) =>
  attachment.preview_url ? `${API_BASE}${attachment.preview_url}` : null;

export const generatedFileUrl = (file: GeneratedFile) => `${API_BASE}${file.url}`;

// ---- Spreadsheets: the Columns step -----------------------------------------

export type ColumnType =
  | "text" | "integer" | "number" | "date" | "boolean" | "category" | "id" | "email" | "phone";
export type ColumnRole = "identifier" | "title" | "attribute" | "ignore";

export const COLUMN_TYPES: ColumnType[] = [
  "text", "integer", "number", "date", "boolean", "category", "id", "email", "phone",
];
export const COLUMN_ROLES: ColumnRole[] = ["identifier", "title", "attribute", "ignore"];

export interface ColumnProfile {
  header: string;
  type: ColumnType;
  null_ratio: number;
  distinct: number;
  samples: string[];
  options?: string[];
  min?: number | string;
  max?: number | string;
  mean?: number;
  fill_down_suggested: boolean;
}

export interface SchemaColumn {
  source: string;
  key: string;
  label: string;
  type: ColumnType;
  role: ColumnRole;
  description: string;
  fill_down: boolean;
}

export interface SchemaCombine {
  key: string;
  label: string;
  from: string[];
  sep: string;
}

export interface SheetSchema {
  entity: string;
  entity_ar: string;
  description: string;
  title_template: string;
  columns: SchemaColumn[];
  combine: SchemaCombine[];
}

export interface SheetCard {
  row: number;
  text: string;
}

export interface SheetInfo {
  index: number;
  name: string;
  table_name: string;
  header_row: number | null;
  header_rows: number;
  row_count: number;
  col_count: number;
  profile: ColumnProfile[];
  /** Empty ({}) until proposed / confirmed. */
  proposed_schema: Partial<SheetSchema>;
  schema: Partial<SheetSchema>;
  schema_status: "pending" | "proposing" | "proposed" | "confirmed";
  schema_source: "" | "llm" | "heuristic";
  proposal_error: string;
  confirmed_at: string | null;
  first_rows: { row: number; values: (string | number | boolean | null)[] }[];
  preview: SheetCard[];
}

export const getSheets = (documentId: string) =>
  api<{ status: DocStatus; sheets: SheetInfo[] }>(`/api/documents/${documentId}/sheets/`);

export const previewSheet = (documentId: string, sheet: number, schema: SheetSchema) =>
  api<{ schema: SheetSchema; preview: SheetCard[] }>(`/api/documents/${documentId}/sheets/preview/`, {
    method: "POST",
    body: JSON.stringify({ sheet, schema }),
  });

export const reprofileSheet = (documentId: string, sheet: number, headerRow: number | null) =>
  api<SheetInfo>(`/api/documents/${documentId}/sheets/reprofile/`, {
    method: "POST",
    body: JSON.stringify({ sheet, header_row: headerRow }),
  });

export const confirmSheets = (documentId: string, sheets: { index: number; schema: SheetSchema }[]) =>
  api<{ status: DocStatus; sheets: SheetInfo[] }>(`/api/documents/${documentId}/sheets/confirm/`, {
    method: "POST",
    body: JSON.stringify({ sheets }),
  });
