export type TaskStatus =
  | "invalid" | "queued" | "running" | "waiting_human" | "succeeded" | "failed" | "rejected" | "expired" | "cancelled";

export interface Batch {
  id: string; name: string; status: string; created_at: string; completed_at: string | null;
  source_filename: string | null; approval_threshold: number | null;
  total: number; succeeded: number; failed: number; invalid: number; waiting: number; active: number; cost_usd: number;
}

export interface TaskRow {
  id: string; row_number: number; status: TaskStatus; current_stage: string | null; idempotency_key: string;
  confirmation_no: string | null; error: string | null; cost_usd: number; payload: Record<string, unknown>;
  validation_errors: { field: string; message: string }[]; attempts: number; updated_at: string;
}

export interface TierAttempt {
  tier: string; found: boolean; x: number | null; y: number | null; verified: boolean | null;
  reason: string; latency_ms: number; cost_usd: number;
}

export interface Step {
  id: number; seq: number; stage: string; action: string; target: string | null; value: string | null;
  status: "started" | "succeeded" | "failed" | "skipped"; actor: string; grounder: string | null;
  grounder_trace: TierAttempt[]; point_x: number | null; point_y: number | null;
  bbox: { x: number; y: number; w: number; h: number } | null; verified: boolean | null; latency_ms: number | null;
  cost_usd: number; screenshot_before: string | null; screenshot_after: string | null; error: string | null;
  created_at: string; width: number | null; height: number | null;
}

export interface Intervention {
  id: string; task_id: string; kind: "approval" | "mfa_code" | "takeover" | "grounding_failure" | "unexpected_page";
  status: "open" | "resolved" | "expired" | "cancelled"; stage: string | null; prompt: string; screenshot: string | null;
  context: Record<string, unknown>; requested_at: string; resolved_at: string | null; resolved_by: string | null;
  resolution: Record<string, unknown> | null; row_number?: number; batch_id?: string; idempotency_key?: string;
}

export interface LiveState {
  status: string; stage: string | null; takeover: boolean; takeover_by: string | null; waiting_for: string | null;
  last_screenshot: string | null; session_worker: string | null; cost_usd: number;
  history: { t: string; stage: string; event: string; detail: string }[];
}

export interface TaskDetail {
  task: TaskRow & { batch_id: string; workflow_id: string | null };
  steps: Step[];
  interventions: Intervention[];
  ledger: { state: string; clicks: number; confirmation_no: string | null; intent_at: string } | null;
  live: LiveState | null;
}

export interface PreviewRow {
  row_number: number; valid: boolean; payload: Record<string, unknown>;
  errors: { field: string; message: string }[]; warnings: string[]; idempotency_key: string;
}
export interface Mapping { source: string; target: string | null; method: string; confidence: number }

export const auth = {
  get token() { return safeGet("pp_token") ?? "dev-operator-token"; },
  set token(v: string) { safeSet("pp_token", v); },
  get operator() { return safeGet("pp_operator") ?? "operator"; },
  set operator(v: string) { safeSet("pp_operator", v); },
};

function safeGet(k: string): string | null {
  try { return localStorage.getItem(k); } catch { return null; }
}
function safeSet(k: string, v: string) {
  try { localStorage.setItem(k, v); } catch { /* storage blocked: keep defaults */ }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${auth.token}`);
  headers.set("X-Operator", auth.operator);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const r = await fetch(`/api${path}`, { ...init, headers });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 300)}`);
  return r.json() as Promise<T>;
}

export const api = {
  batches: () => req<Batch[]>("/batches"),
  batch: (id: string) => req<{ batch: Batch & Record<string, unknown>; tasks: TaskRow[] }>(`/batches/${id}`),
  preview: (fd: FormData) => req<{ mapping: Mapping[]; rows: PreviewRow[]; summary: { total: number; valid: number } }>(
    "/batches/preview", { method: "POST", body: fd }),
  createBatch: (fd: FormData) => req<{ id: string; runnable: number; invalid: number; skipped: unknown[] }>(
    "/batches", { method: "POST", body: fd }),
  credentials: () => req<{ id: string; label: string; username: string; portal: string }[]>("/credentials"),
  createCredential: (b: { label: string; username: string; password: string }) =>
    req<{ id: string }>("/credentials", { method: "POST", body: JSON.stringify(b) }),
  task: (id: string) => req<TaskDetail>(`/tasks/${id}`),
  takeover: (id: string) => req("/tasks/" + id + "/takeover", { method: "POST" }),
  release: (id: string, mode: "retry" | "skip") =>
    req(`/tasks/${id}/release`, { method: "POST", body: JSON.stringify({ mode }) }),
  operatorAction: (id: string, a: { kind: string; x?: number; y?: number; text?: string }) =>
    req(`/tasks/${id}/operator-action`, { method: "POST", body: JSON.stringify(a) }),
  interventions: (status = "open") => req<Intervention[]>(`/interventions?status=${status}`),
  resolve: (iid: string, b: { approved?: boolean; value?: string; note?: string }) =>
    req(`/interventions/${iid}/resolve`, { method: "POST", body: JSON.stringify(b) }),
  metrics: () => req<Metrics>("/metrics"),
};

export interface Metrics {
  grounders: { grounder: string; steps: number; avg_ms: number; p95_ms: number; cost_usd: number }[];
  escalations: { qwen_rejected: number; claude_rescues: number; unresolved: number };
  tasks: Record<string, number>;
  ledger: { submitted: number; clicks: number; recovered_unknown_outcomes: number };
}

export const shot = (sha: string | null | undefined) =>
  sha ? `/api/screenshots/${sha}?token=${encodeURIComponent(auth.token)}` : undefined;
