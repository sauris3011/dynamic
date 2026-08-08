/** Wire types for the Pricing Platform API. */

export type Band = 'auto_approve' | 'review' | 'escalate';
export type Mode = 'supervised' | 'assisted' | 'autonomous';
export type Objective = 'revenue' | 'margin' | 'balanced';

export interface Product {
  sku: string;
  name: string;
  category: string;
  subcategory: string;
  brand: string;
  family_id: string;
  size_value: number;
  size_unit: string;
  unit_cost: number;
  map_price: number | null;
  list_price: number;
  current_price: number;
  launched_on: string;
}

export interface ProductDetail {
  product: Product;
  inventory: {
    sku: string;
    on_hand: number;
    on_order: number;
    weekly_velocity: number;
    cover_days: number;
    updated_at: string;
  };
  price_history: {
    sku: string;
    old_price: number;
    new_price: number;
    changed_at: string;
    source: string;
    reason: string | null;
  }[];
  recent_sales: {
    sku: string;
    sale_date: string;
    units: number;
    unit_price: number;
    revenue: number;
    on_promo: boolean;
  }[];
}

export interface Recommendation {
  rec_id: string;
  run_id: string;
  sku: string;
  product_name: string | null;
  category: string | null;
  current_price: number;
  recommended_price: number;
  delta_abs: number;
  delta_pct: number;
  unit_cost: number;
  expected_revenue_delta: number | null;
  expected_margin_delta: number | null;
  revenue_ci_low: number | null;
  revenue_ci_high: number | null;
  prob_below_margin: number | null;
  variance: number | null;
  confidence: number;
  elasticity: number | null;
  elasticity_ci_low: number | null;
  elasticity_ci_high: number | null;
  elasticity_samples: number | null;
  baseline_price: number | null;
  band: Band;
  band_reason: string;
  compliance_status: 'pass' | 'violation';
  damped: number;
  oscillating: number;
  rationale: string | null;
  citations_json: string;
  /**
   * 1 when a model wrote the rationale, 0 when it is the computed fallback.
   * Only the highest-impact products of a run get a written explanation, so a
   * reader has to be able to tell which kind they are looking at.
   */
  narrated?: number;
  status: string;
  final_price: number | null;
  created_at: string;
  compliance_evals?: ComplianceEval[];
  approvals?: Approval[];
}

export interface ComplianceEval {
  rule_code: string;
  passed: number;
  actual_value: number | null;
  threshold: number | null;
  detail: string;
}

export interface Approval {
  actor: string;
  action: string;
  reason: string | null;
  mode: string;
  price: number | null;
  created_at: string;
}

export interface RunProgress {
  run_id: string;
  status?: 'queued' | 'running' | 'completed' | 'failed' | 'halted';
  stage?: string;
  stages?: Record<string, number>;
  sku_count?: number;
  bands?: Partial<Record<Band, number>>;
  errors?: string[];
  quality?: string;

  /**
   * What the autonomy policy actually did once the run was saved — as opposed
   * to `bands`, which is only how each product was *classified*. In Supervised
   * mode every field here is zero however many products landed in the
   * auto-approve band.
   */
  autonomy?: {
    auto_approved: number;
    /** Approved but not sent: the proposed price equals the current one. */
    held_no_change?: number;
    pushed: number;
    batch_key?: string | null;
    mode?: string;
    detail?: string;
  };

  /** When the run was accepted — the client ticks its own clock from this. */
  started_at?: string;
  /** Items finished / to do **within the current stage**. */
  processed?: number;
  total?: number;
  /** Weighted fraction of the whole run, 0–1. Caps at 0.99 until it finishes. */
  overall?: number;
  /** What it is doing right now, in plain language. */
  detail?: string;
  /**
   * How many products got a model-written explanation. Absent when narration
   * never ran — which is not the same as zero written explanations out of many,
   * and must not be displayed as though it were.
   */
  narrated?: number;
}

export interface RunRecord {
  run_id: string;
  started_at: string;
  completed_at: string | null;
  status: string;
  trigger: string;
  scope_kind: string;
  scope_value: string | null;
  objective: string;
  mode: string;
  sku_count: number;
  duration_ms: number | null;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  error: string | null;
  escalated?: number;
  quality_json?: string | null;
  band_counts?: Record<string, number>;
}

export interface Telemetry {
  active_llm_calls: number;
  tokens_in: number;
  tokens_out: number;
  tokens_total: number;
  estimated_cost_usd: number;
  runs: number;
  cache: CacheStats;
}

export interface CacheStats {
  exact_hits: number;
  semantic_hits: number;
  misses: number;
  tokens_saved: number;
  entries: number;
  hit_rate: number;
}

export interface PlatformConfig {
  mode: Mode;
  modes_available: Mode[];
  thresholds: {
    min_confidence: number;
    max_delta_pct: number;
    max_variance: number;
    margin_buffer_pct: number;
  };
  gateway_url: string;
  api_key_set: boolean;
  /** Short hash identifying which key is loaded. Never the key itself. */
  api_key_fingerprint: string;
  /** True when the key survives a restart, rather than living only in memory. */
  api_key_persisted: boolean;
  /** Settings fields whose value came from the database, not from .env. */
  persisted_overrides: string[];
  tls: { mode: string; secure: boolean; detail: string };
  monte_carlo: { iterations: number; seed: number };
  models: Record<string, string>;
  /** Every assignable role including `embeddings`, which `models` omits. */
  model_roles: Record<AgentRole, string>;
  ports: Record<string, number>;
}

/** Roles that get their own model dropdown in the settings drawer (D4). */
export type AgentRole = 'router' | 'narrator' | 'analyst' | 'strategist' | 'embeddings';

export interface GatewayModels {
  gateway_url: string;
  reachable: boolean;
  models: string[];
  endpoint: string;
  error: string;
  latency_ms: number;
  roles: Record<AgentRole, string>;
  /** Assigned aliases the gateway does not list — a misconfiguration (D4). */
  unknown: Partial<Record<AgentRole, string>>;
}

export interface ConnectionTest {
  reachable: boolean;
  models: string[];
  latency_ms: number;
  detail: string;
  error: string;
}

export interface ModelTest {
  ok: boolean;
  role: string;
  model: string;
  latency_ms: number;
  detail: string;
}

export interface LoopStatus {
  running: boolean;
  interval_seconds: number;
  iterations: number;
  started_at: string | null;
  stopped_at: string | null;
  last_run_id: string | null;
  last_error: string | null;
  scope: string;
}

export interface RagStats {
  available: boolean;
  collections: Record<string, { chunks: number; description: string }>;
  total_chunks: number;
  embedding_model: string;
  embedding_strategy?: 'gateway' | 'minilm' | 'hashed' | string;
  dimensions?: number;
  /** False means lexical matching, not semantic search — a real downgrade. */
  semantic?: boolean;
  degraded_reason?: string;
  chunking?: {
    strategy: string;
    chunk_size: number;
    chunk_overlap: number;
    separators: string[];
  };
  supported_uploads?: string[];
  /** Set when the collections were built by a different embedding function. */
  mismatch?: {
    stored: string;
    current: string;
    compatible: boolean;
    detail: string;
  } | null;
  gateway?: {
    reachable: boolean;
    usable: boolean;
    summary: string;
    configured: Record<string, string>;
    missing: Record<string, string>;
  };
}

export interface AuditEvent {
  ts: string;
  actor: string;
  event_type: string;
  entity_type: string | null;
  entity_id: string | null;
  detail_json: string;
}

export interface PriceOutcome {
  price: number;
  expected_units: number;
  expected_revenue: number;
  expected_margin: number;
  revenue_p5: number;
  revenue_p50: number;
  revenue_p95: number;
  margin_p5: number;
  margin_p50: number;
  revenue_cv: number;
  prob_below_margin_floor: number;
  prob_revenue_gain: number;
}

export interface SimulationResult {
  sku: string;
  product_name: string | null;
  category: string | null;
  current_price: number;
  unit_cost: number;
  horizon_days: number;
  baseline: { revenue: number; margin: number; units: number; cover_days: number | null };
  candidates: PriceOutcome[];
  compliance: Record<string, { passed: boolean; violations: string[]; summary: string }>;
  ai_recommendation: {
    price: number;
    hold: boolean;
    objective: string;
    expected_revenue_delta: number;
    revenue_ci_low: number;
    revenue_ci_high: number;
    reason: string;
  };
  rule_based_baseline: {
    price: number;
    delta_pct: number;
    rules_fired: string[];
    note: string;
  };
  elasticity: {
    value: number | null;
    ci_low: number | null;
    ci_high: number | null;
    sample_size: number;
    usable: boolean;
    reason: string;
  };
  assumptions: string[];
  degraded: boolean;
  note: string;
  stress?: { price_tested: number; scenarios: Record<string, PriceOutcome> };
}

export interface Convergence {
  samples: number;
  mean_abs_error_pct: number;
  recent_error_pct: number;
  earlier_error_pct: number;
  converging: boolean;
  detail: string;
  dispersion_scale: number;
}

export interface MetricsSummary {
  acceptance_rate: number | null;
  auto_approve_rate: number | null;
  band_shares: Partial<Record<Band, number>>;
  oscillation_rate: number;
  converging: boolean;
  mean_abs_forecast_error_pct: number;
  ai_uplift_pct: number | null;
  recommendations: number;
  outcomes_measured: number;
}

/**
 * The five metrics endpoints used to return `Record<string, any>`, which meant
 * every consumer re-declared the shape inline at its call site. These mirror
 * `pricing/services/metrics.py` and `pricing/services/scoring.py`.
 *
 * Fields the aggregations omit when there is nothing to report (`error` on an
 * empty score, `agreement` before any baseline exists) are optional here rather
 * than nullable — an absent key and a null one mean the same thing to the UI.
 */
export interface PerformanceMetrics {
  recommendations: number;
  price_changes_proposed: number;
  forecast_revenue_delta: number;
  forecast_margin_delta: number;
  mean_confidence: number;
  by_status: Record<string, number>;
  decided: number;
  acceptance_rate: number | null;
  acceptance_note: string;
  realized: {
    measured: number;
    forecast_revenue: number;
    realized_revenue: number;
    mean_abs_error_pct: number;
    within_20pct: number;
  };
}

export interface BaselineCategory {
  skus: number;
  ai_revenue_delta: number;
  mean_ai_price: number;
  mean_baseline_price: number;
}

export interface BaselineMetrics {
  skus: number;
  /** Present only when `skus` is 0. */
  detail?: string;
  agreement?: {
    same_price: number;
    ai_higher: number;
    ai_lower: number;
    agreement_rate: number;
  };
  ai_forecast_revenue_delta?: number;
  ai_uplift_pct?: number;
  by_category?: Record<string, BaselineCategory>;
  note?: string;
}

export interface Refinement {
  sku: string;
  elasticity: number;
  ci_low: number;
  ci_high: number;
  refinement_count: number;
  total_adjustment: number;
  updated_at: string;
}

export interface StabilityMetrics {
  recommendations: number;
  oscillating: number;
  oscillation_rate: number;
  damped: number;
  convergence: Convergence;
  error_trajectory: { measured_at: string; sku: string; error_pct: number }[];
  refinements: Refinement[];
  capped_adjustments: number;
  capped_note: string;
}

export interface AutonomyMetrics {
  band_totals: Partial<Record<Band, number>>;
  band_shares: Partial<Record<Band, number>>;
  per_run: {
    run_id: string;
    started_at: string;
    auto_approve: number;
    review: number;
    escalate: number;
  }[];
  auto_approved: number;
  human_approved: number;
  auto_approve_rate: number | null;
  escalation_reasons: { reason: string; count: number }[];
  mode_history: {
    ts: string;
    actor: string;
    event_type: string;
    detail: Record<string, unknown>;
  }[];
}

export interface ScoredSku {
  sku: string;
  category: string;
  true_elasticity: number;
  estimated_elasticity: number | null;
  optimum_price: number;
  recommended_price: number;
  baseline_price: number | null;
  ai_deviation_pct: number;
  baseline_deviation_pct: number | null;
  confidence: number;
  band: Band;
}

export interface AccuracyMetrics {
  scored: number;
  /** Present only when `scored` is 0. */
  error?: string;
  high_confidence_skus?: number;
  ai_mean_deviation_pct?: number;
  ai_mean_deviation_pct_high_confidence?: number;
  baseline_mean_deviation_pct?: number;
  within_10pct_high_confidence?: number;
  within_10pct_rate?: number | null;
  target?: string;
  worst?: ScoredSku[];
  best?: ScoredSku[];
}

export type ChatIntent =
  | 'product'
  | 'history'
  | 'what_if'
  | 'analysis'
  | 'platform'
  | 'unsupported';

export interface ChatCitation {
  id: string;
  source: string;
  collection: string;
  excerpt: string;
  locator: string | null;
}

/**
 * `facts` is the computed evidence the answer was written from, and the UI
 * shows it alongside the prose. `unsupported_figures` lists anything in the
 * prose the backend could not trace back to that evidence — displayed, not
 * hidden, because a figure the platform cannot vouch for is exactly the thing
 * an analyst needs to see.
 */
export interface ChatReply {
  question: string;
  intent: ChatIntent;
  router: 'model' | 'keyword';
  headline: string;
  answer: string;
  key_points: string[];
  caveats: string[];
  facts: string[];
  data: Record<string, unknown>;
  sources: string[];
  citations: ChatCitation[];
  shortfall: string;
  unsupported_figures: string[];
  suggestions: string[];
  narrated: boolean;
  model: string;
  cache_hit: string;
  tokens: number;
  cost_usd: number;
  latency_ms: number;
  scope: {
    skus: string[];
    category: string;
    horizon_days: number;
    days_back: number;
  };
}

export interface ChatStarter {
  label: string;
  question: string;
  kind: ChatIntent;
}

export interface ChatSuggestions {
  suggestions: ChatStarter[];
  catalog_available: boolean;
  categories: string[];
  detail: string;
}

export interface Health {
  status: string;
  service: string;
  version: string;
  commerce: Record<string, unknown>;
  tls: { mode: string; secure: boolean; detail: string };
  mode: Mode;
}
