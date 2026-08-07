/**
 * The only network surface the client has.
 *
 * Everything goes to the Pricing Platform on :8000 through the Vite proxy. The
 * client never contacts the Commerce Service and never contacts the LLM gateway
 * — it holds no credentials of any kind (FR-072, NFR-011). If a fetch in this
 * file ever names an external host, that guarantee has been broken.
 */

import type {
  AuditEvent,
  CacheStats,
  ChatReply,
  ChatSuggestions,
  Convergence,
  Health,
  LoopStatus,
  MetricsSummary,
  Mode,
  Objective,
  PlatformConfig,
  Product,
  ProductDetail,
  RagStats,
  Recommendation,
  RunProgress,
  RunRecord,
  SimulationResult,
  Telemetry,
} from './types';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  });

  const text = await response.text();
  const body = text ? safeParse(text) : null;

  if (!response.ok) {
    // FastAPI puts the message under `detail`, sometimes as a nested object —
    // surface whatever is actually useful rather than "Request failed".
    const detail = (body as { detail?: unknown })?.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : typeof (detail as { message?: string })?.message === 'string'
          ? (detail as { message: string }).message
          : `${response.status} ${response.statusText}`;
    throw new ApiError(message, response.status, detail);
  }
  return body as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) });

const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) });

const query = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : '';
};

export const api = {
  health: () => request<Health>('/health'),

  // --- Products -----------------------------------------------------------
  products: (params: { search?: string; category?: string; limit?: number } = {}) =>
    request<Product[]>(`/api/products${query(params)}`),
  product: (sku: string) =>
    request<ProductDetail>(`/api/products/${encodeURIComponent(sku)}`),

  // --- Runs ---------------------------------------------------------------
  startRun: (body: {
    scope_kind: 'all' | 'category' | 'skus';
    scope_value?: string | null;
    skus?: string[];
    objective: Objective;
  }) => post<{ run_id: string; status: string; scope: string; mode: Mode }>('/api/runs', body),
  runProgress: (runId: string) => request<RunProgress>(`/api/runs/progress/${runId}`),
  runs: (limit = 50) => request<RunRecord[]>(`/api/runs${query({ limit })}`),
  run: (runId: string) => request<RunRecord>(`/api/runs/${runId}`),
  topology: () =>
    request<{
      available: boolean;
      engine: string;
      nodes: string[];
      edges: { from: string; to: string; condition?: string }[];
      checkpointed: boolean;
    }>('/api/runs/topology'),

  // --- Recommendations ----------------------------------------------------
  recommendations: (params: {
    run_id?: string;
    band?: string;
    status?: string;
    category?: string;
    limit?: number;
  }) => request<Recommendation[]>(`/api/recommendations${query(params)}`),
  recommendation: (recId: string) =>
    request<Recommendation>(`/api/recommendations/${recId}`),
  approve: (recId: string, reason: string) =>
    post(`/api/recommendations/${recId}/approve`, { actor: 'operator', reason }),
  reject: (recId: string, reason: string) =>
    post(`/api/recommendations/${recId}/reject`, { actor: 'operator', reason }),
  override: (recId: string, price: number, reason: string) =>
    post(`/api/recommendations/${recId}/override`, { actor: 'operator', price, reason }),
  revert: (recId: string, reason: string) =>
    post(`/api/recommendations/${recId}/revert`, { actor: 'operator', reason }),
  push: (recIds: string[]) =>
    post<{
      pushed: number;
      rejected: number;
      skipped: number;
      blocked: { rec_id: string; sku: string; reason: string }[];
      batch_key?: string;
    }>('/api/recommendations/push', { rec_ids: recIds, actor: 'operator' }),
  skuAudit: (sku: string) =>
    request<{ sku: string; recommendations: Recommendation[]; audit_events: AuditEvent[] }>(
      `/api/recommendations/audit/${sku}`,
    ),

  // --- Config, telemetry, ops --------------------------------------------
  config: () => request<PlatformConfig>('/api/config'),
  setMode: (mode: Mode) => put('/api/config/mode', { mode, actor: 'operator' }),
  setThresholds: (body: Record<string, number>) =>
    put('/api/config/thresholds', { ...body, actor: 'operator' }),
  setGateway: (body: {
    gateway_url?: string;
    api_key?: string;
    allow_insecure_tls?: boolean;
  }) => put('/api/config/gateway', { ...body, actor: 'operator' }),
  telemetry: () => request<Telemetry>('/api/telemetry/live'),
  telemetryStatus: () =>
    request<{
      local_sink: { path: string; active: boolean; events: number };
      langfuse: { configured: boolean; active: boolean; host: string | null };
    }>('/api/telemetry/status'),
  cacheStats: () => request<CacheStats>('/api/cache/stats'),
  clearCache: () => request('/api/cache', { method: 'DELETE' }),
  audit: (params: { limit?: number; automatic_only?: boolean }) =>
    request<AuditEvent[]>(`/api/audit${query(params)}`),

  // --- Loop ---------------------------------------------------------------
  loopStatus: () => request<LoopStatus>('/api/loop/status'),
  startLoop: (body: {
    interval_seconds: number;
    scope_kind: 'all' | 'category' | 'skus';
    scope_value?: string | null;
    objective: Objective;
  }) => post('/api/loop/start', { ...body, actor: 'operator' }),
  stopLoop: () => post('/api/loop/stop', { actor: 'operator' }),
  killSwitch: (reason: string) =>
    post<{
      halted: boolean;
      loop_stopped: boolean;
      cancelled_auto_approvals: number;
      mode: Mode;
    }>('/api/loop/kill-switch', { actor: 'operator', reason }),

  // --- RAG ----------------------------------------------------------------
  ragStats: () => request<RagStats>('/api/rag/stats'),
  ragReset: () =>
    request<{ reset: string[]; embedding_model: string; detail: string }>(
      '/api/rag/collections',
      { method: 'DELETE' },
    ),
  ragIngestText: (body: { collection: string; source: string; text: string }) =>
    post<{ ingested: number }>('/api/rag/ingest/text', { ...body, actor: 'operator' }),
  ragIngestFile: (file: File, collection: string) => {
    const form = new FormData();
    form.append('file', file);
    form.append('collection', collection);
    form.append('actor', 'operator');
    return request<{ ingested: number }>('/api/rag/ingest/file', {
      method: 'POST',
      body: form,
    });
  },
  ragRetrieve: (queryText: string, k = 4) =>
    post<{ query: string; results: { id: string; source: string; collection: string; excerpt: string; distance: number }[] }>(
      '/api/rag/retrieve',
      { query: queryText, k },
    ),

  // --- Simulation ---------------------------------------------------------
  simulate: (body: {
    skus: string[];
    prices?: Record<string, number[]>;
    horizon_days: number;
    objective: Objective;
    include_stress: boolean;
  }) =>
    post<{ horizon_days: number; results: SimulationResult[]; missing_skus: string[] }>(
      '/api/simulate',
      body,
    ),
  scenarios: () =>
    request<{ scenario_id: string; name: string; created_at: string; horizon_days: number }[]>(
      '/api/simulate/scenarios',
    ),
  saveScenario: (body: {
    name: string;
    skus: string[];
    horizon_days: number;
    objective: Objective;
    include_stress: boolean;
  }) => post<{ scenario_id: string; name: string }>('/api/simulate/scenarios', body),

  // --- Feedback loop ------------------------------------------------------
  readback: (runId?: string) =>
    post<{
      measured: number;
      refined: number;
      skipped: number;
      detail: string;
      outcomes?: { sku: string; forecast_revenue: number; realized_revenue: number; forecast_error_pct: number }[];
      refinements?: { sku: string; prior_elasticity: number; posterior_elasticity: number; step: number; capped: boolean; reason: string }[];
    }>('/api/feedback/readback', { run_id: runId }),
  advanceMarket: (days: number) =>
    post<{ days_generated: number; rows_written: number; note: string }>(
      '/api/feedback/advance-market',
      { days, actor: 'operator' },
    ),
  marketClock: () =>
    request<{ first_date: string; last_date: string; sales_rows: number; days_behind_today: number }>(
      '/api/feedback/clock',
    ),
  convergence: () => request<Convergence>('/api/feedback/convergence'),
  estimates: () =>
    request<
      {
        sku: string;
        elasticity: number;
        ci_low: number;
        ci_high: number;
        refinement_count: number;
        total_adjustment: number;
        updated_at: string;
      }[]
    >('/api/feedback/estimates'),

  // --- Analyst assistant --------------------------------------------------
  chat: (body: {
    message: string;
    history: { role: 'analyst' | 'assistant'; text: string }[];
    context?: { view?: string; run_id?: string; rec_id?: string; sku?: string };
  }) => post<ChatReply>('/api/chat', body),
  chatSuggestions: () => request<ChatSuggestions>('/api/chat/suggestions'),

  // --- Metrics ------------------------------------------------------------
  metricsSummary: () => request<MetricsSummary>('/api/metrics/summary'),
  performance: () => request<Record<string, any>>('/api/metrics/performance'),
  baseline: () => request<Record<string, any>>('/api/metrics/baseline'),
  stability: () => request<Record<string, any>>('/api/metrics/stability'),
  autonomy: () => request<Record<string, any>>('/api/metrics/autonomy'),
  accuracy: () => request<Record<string, any>>('/api/metrics/accuracy'),
};

export const categories = [
  'Beverages',
  'Snacks',
  'Coffee & Tea',
  'Household',
  'Personal Care',
];
