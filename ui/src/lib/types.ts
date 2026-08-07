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
  autonomy?: Record<string, unknown>;
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
  tls: { mode: string; secure: boolean; detail: string };
  monte_carlo: { iterations: number; seed: number };
  models: Record<string, string>;
  ports: Record<string, number>;
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
