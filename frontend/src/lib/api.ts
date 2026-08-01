export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public body?: unknown) {
    super(detail);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    let body: unknown;
    try {
      body = await res.json();
      detail = (body as { detail?: string })?.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, body);
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
  created_at: string;
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
}

// ---- Calls -----------------------------------------------------------------

export const listDocuments = () =>
  api<{ results: DocumentSummary[] }>("/api/documents/");

export const getDocument = (id: string) => api<any>(`/api/documents/${id}/`);

export const listEngines = (refresh = false) =>
  api<{ engines: Engine[] }>(`/api/ocr/engines/${refresh ? "?refresh=1" : ""}`);

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
  api<any>(`/api/ocr/batches/${batchId}/compare/`);

export const selectRun = (documentId: string, runId: string) =>
  api<any>(`/api/documents/${documentId}/text/select/`, {
    method: "POST",
    body: JSON.stringify({ run_id: runId }),
  });

export const saveRevision = (documentId: string, text: string, note?: string) =>
  api<any>(`/api/documents/${documentId}/revisions/`, {
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

export const createConversation = (scope: "all" | "selected", documentIds: string[] = []) =>
  api<any>("/api/conversations/", {
    method: "POST",
    body: JSON.stringify({ scope, document_ids: documentIds }),
  });

export const pageImageUrl = (documentId: string, page: number, profile = "neural") =>
  `${API_BASE}/api/documents/${documentId}/pages/${page}/image/?profile=${profile}`;
