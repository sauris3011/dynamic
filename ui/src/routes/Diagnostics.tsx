import { useEffect, useRef, useState } from 'react';
import { Upload } from 'lucide-react';

import { api } from '../lib/api';
import { useActionState, useApi } from '../lib/hooks';
import { datetime, duration, money, num, pct, ratio } from '../lib/format';
import { STAGES } from '../lib/labels';
import { Card, Disclosure, Empty, KeyValue, SectionTitle, Toast } from '../components/primitives';
import { ErrorTrajectory } from '../components/Distribution';
import type { PlatformConfig } from '../lib/types';

/**
 * How the system is behaving: what it costs, how long it takes, what it reads
 * from, and whether it is getting better.
 *
 * Deliberately not a settings screen. Where the gateway is and which model each
 * agent uses are connection settings, and they live behind the gear in the
 * header where they are reachable from any screen — putting credentials on a
 * tab meant walking a non-technical audience straight through them. What is
 * left here is a readout plus the two knobs that are genuinely operational:
 * which documents the system reads, and how sure it must be before deciding
 * alone.
 */

const COLLECTIONS = ['pricing_policy', 'market_intel', 'product_kb', 'user_uploads'];

export function Diagnostics({
  config,
  onChanged,
}: {
  config: PlatformConfig | null;
  onChanged: () => void;
}) {
  const action = useActionState();

  return (
    <div className="p-7 space-y-5 max-w-5xl">
      <SectionTitle
        eyebrow="Diagnostics"
        title="How the system is behaving"
        lede="Cost, speed, the documents it reads from, and whether its forecasts are
              improving. Nothing here changes what the platform decides."
      />

      <CostCard action={action} />
      <TimingsCard />
      <RetrievalCard action={action} />
      <LearningCard />
      <ThresholdsCard config={config} action={action} onChanged={onChanged} />

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}

type Action = ReturnType<typeof useActionState>;

// --- Cost, cache, tracing ---------------------------------------------------

function CostCard({ action }: { action: Action }) {
  const telemetry = useApi(() => api.telemetry(), [], 5000);
  const cache = useApi(() => api.cacheStats(), [], 5000);
  const tracing = useApi(() => api.telemetryStatus(), []);

  const t = telemetry.data;
  const c = cache.data;

  return (
    <Card title="Cost, cache and tracing">
      <div className="grid grid-cols-4 gap-x-6 gap-y-4">
        <Metric label="Active LLM calls" value={num(t?.active_llm_calls ?? 0)} />
        <Metric label="Tokens in" value={num(t?.tokens_in ?? 0)} />
        <Metric label="Tokens out" value={num(t?.tokens_out ?? 0)} />
        <Metric
          label="Estimated spend"
          value={money(t?.estimated_cost_usd ?? 0)}
          detail="Computed server-side. The browser never contacts the gateway."
        />
      </div>

      <div className="grid grid-cols-2 gap-6 mt-5">
        <div>
          <KeyValue
            rows={[
              ['Exact hits', num(c?.exact_hits)],
              ['Semantic hits', num(c?.semantic_hits)],
              ['Misses', num(c?.misses)],
              ['Hit rate', ratio(c?.hit_rate ?? 0, 1)],
              ['Tokens saved', num(c?.tokens_saved)],
              ['Entries', num(c?.entries)],
            ]}
          />
          <button
            className="btn mt-3 w-full"
            onClick={() =>
              action.run(async () => {
                await api.clearCache();
                await cache.reload();
                return 'Cache cleared.';
              })
            }
          >
            Clear cache
          </button>
        </div>

        <div>
          <KeyValue
            rows={[
              ['Local JSONL sink', `${tracing.data?.local_sink.events ?? 0} events`],
              [
                'Langfuse',
                tracing.data?.langfuse.configured
                  ? tracing.data.langfuse.active
                    ? 'active'
                    : 'configured, unreachable'
                  : 'not configured',
              ],
            ]}
          />
          <p className="text-micro text-faint mt-2 leading-snug">
            The local sink is always on, so traces survive blocked egress. Telemetry never
            fails a pricing run.
          </p>
        </div>
      </div>
    </Card>
  );
}

// --- Pipeline timings -------------------------------------------------------

function TimingsCard() {
  const runs = useApi(() => api.runs(12), [], 30000);
  const latest = runs.data?.[0] ?? null;
  const progress = useApi(
    () => (latest ? api.runProgress(latest.run_id) : Promise.resolve(null)),
    [latest?.run_id],
  );

  const stages = progress.data?.stages ?? {};

  return (
    <Card title="Pipeline timings">
      <p className="text-xs text-faint mb-3">
        Per-stage seconds and the model behind each stage, for the most recent run
        {latest && <span className="font-mono text-muted"> {latest.run_id}</span>}.
      </p>

      <div className="grid grid-cols-5 gap-3">
        {STAGES.map((stage, i) => (
          <div key={stage.key} className="border border-hairline rounded-lg p-3">
            <div className="text-micro text-faint">Stage {i + 1}</div>
            <div className="text-xs text-ink font-semibold mt-0.5">{stage.title}</div>
            <div className="text-lg text-info tabular-nums mt-1">
              {stages[stage.key] !== undefined ? `${stages[stage.key].toFixed(1)}s` : '—'}
            </div>
            <div className="text-micro text-faint mt-1 leading-snug">{stage.technical}</div>
          </div>
        ))}
      </div>

      <Disclosure title="Run history" hint="duration and token cost per run">
        {runs.data && runs.data.length > 0 ? (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left label border-b border-line">
                <th className="py-1.5">Run</th>
                <th>Started</th>
                <th className="text-right">SKUs</th>
                <th className="text-right">Duration</th>
                <th className="text-right">Tokens</th>
                <th className="text-right">Cost</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.run_id} className="border-b border-hairline last:border-0">
                  <td className="py-1.5 font-mono text-tiny text-faint">
                    {r.run_id.slice(0, 8)}
                  </td>
                  <td className="text-tiny text-faint">{datetime(r.started_at)}</td>
                  <td className="text-right tabular-nums">{num(r.sku_count)}</td>
                  <td className="text-right tabular-nums">{duration(r.duration_ms)}</td>
                  <td className="text-right tabular-nums">
                    {num(r.tokens_in + r.tokens_out)}
                  </td>
                  <td className="text-right tabular-nums">{money(r.cost_usd)}</td>
                  <td className="text-tiny text-muted">{r.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No runs recorded yet.</Empty>
        )}
      </Disclosure>
    </Card>
  );
}

// --- Retrieval --------------------------------------------------------------

/**
 * The active embedding function is displayed rather than assumed. Three
 * strategies are possible — gateway, local MiniLM, and a hashed-n-gram fallback
 * — and only the first two are semantic. The fallback retrieves on shared
 * vocabulary rather than meaning, a real downgrade the operator needs to be told
 * about rather than infer from poor results.
 */
function RetrievalCard({ action }: { action: Action }) {
  const stats = useApi(() => api.ragStats(), [], 30000);
  const [target, setTarget] = useState('user_uploads');
  const fileRef = useRef<HTMLInputElement>(null);

  const lexical = stats.data?.semantic === false;
  const mismatch = stats.data?.mismatch ?? null;

  const upload = (file: File) =>
    action.run(async () => {
      const result = await api.ragIngestFile(file, target);
      await stats.reload();
      return `${file.name}: ${result.ingested} chunk(s) embedded.`;
    });

  const reEmbed = () =>
    action.run(async () => {
      const result = await api.ragReset();
      await stats.reload();
      return result.detail;
    });

  return (
    <Card title="Reference documents">
      <p className="text-xs text-faint mb-3">
        Documents uploaded here become context for every agent immediately, with no
        restart.
      </p>

      <div className="grid grid-cols-2 gap-6">
        <div>
          <KeyValue
            rows={COLLECTIONS.map((name) => [
              name,
              num(stats.data?.collections?.[name]?.chunks ?? 0),
            ])}
          />

          <label className="sr-only" htmlFor="rag-collection">
            Target collection
          </label>
          <select
            id="rag-collection"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="input mt-3"
          >
            {COLLECTIONS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>

          <button
            onClick={() => fileRef.current?.click()}
            disabled={!stats.data?.available}
            className="mt-2 w-full border border-dashed border-line rounded-lg py-2
                       text-tiny text-faint hover:text-muted hover:border-info
                       transition-colors disabled:opacity-40 flex items-center
                       justify-center gap-2"
          >
            <Upload size={12} aria-hidden />
            Upload policy documents
          </button>
          <input
            ref={fileRef}
            type="file"
            accept={(stats.data?.supported_uploads ?? ['.txt', '.md', '.csv']).join(',')}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void upload(file);
              e.target.value = '';
            }}
          />
        </div>

        <div>
          <p className="text-micro text-faint leading-snug">
            {stats.data?.available
              ? `Embeddings: ${stats.data.embedding_model}`
              : 'Vector store unavailable — recommendations will carry no citations.'}
          </p>
          {stats.data?.chunking && (
            <p className="text-micro text-faint mt-1 leading-snug">
              Chunking: {stats.data.chunking.strategy} · {stats.data.chunking.chunk_size}/
              {stats.data.chunking.chunk_overlap}
            </p>
          )}
          {lexical && (
            <p className="text-micro text-accent mt-1.5 leading-snug">
              Lexical fallback in use: retrieval matches shared vocabulary, not meaning. Set
              MODEL_EMBEDDING for gateway embeddings, or CA_BUNDLE_PATH to unblock the local
              model.
            </p>
          )}
          {lexical && stats.data?.degraded_reason && (
            <p className="text-micro text-faint mt-1 leading-snug break-words">
              {stats.data.degraded_reason}
            </p>
          )}

          {mismatch && (
            // Silent failure here would present as merely poor retrieval, or as
            // an opaque dimensionality error on the operator's next query.
            <div
              className={`mt-3 border rounded-lg p-2.5 ${
                mismatch.compatible
                  ? 'border-accent/40 bg-accent-wash'
                  : 'border-danger/40 bg-danger-wash'
              }`}
            >
              <p
                className={`text-micro leading-snug ${
                  mismatch.compatible ? 'text-accent' : 'text-danger'
                }`}
              >
                {mismatch.detail}
              </p>
              <button
                onClick={reEmbed}
                className="mt-2 w-full border border-line rounded-md py-1 text-micro
                           text-muted hover:text-ink transition-colors"
              >
                Drop collections and re-embed
              </button>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

// --- The learning loop ------------------------------------------------------

function LearningCard() {
  const stability = useApi(() => api.stability(), [], 60000);
  const s = stability.data;
  const errors = (s?.error_trajectory ?? []).map((p) => p.error_pct);

  const verdict = !s?.convergence.samples
    ? { text: 'Not measurable yet', tone: 'text-faint' }
    : s.convergence.converging
      ? { text: 'Narrowing', tone: 'text-accent' }
      : { text: 'Widening — review', tone: 'text-danger' };

  return (
    <Card title="Learning loop">
      <div className="flex items-baseline gap-3 mb-3">
        <span className={`text-sm font-semibold ${verdict.tone}`}>{verdict.text}</span>
        <span className="text-tiny text-faint">
          Forecast error against what actually happened, over successive measured outcomes.
        </span>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div>
          <ErrorTrajectory values={errors} />
          <p className="text-tiny text-faint mt-2 leading-snug">
            {s?.convergence.detail}
          </p>
        </div>

        <div>
          <KeyValue
            rows={[
              ['Recommendations', num(s?.recommendations)],
              ['Oscillating', `${num(s?.oscillating)} (${ratio(s?.oscillation_rate ?? 0, 1)})`],
              ['Damped', num(s?.damped)],
              ['Mean absolute error', pct(s?.convergence.mean_abs_error_pct)],
              ['Capped adjustments', num(s?.capped_adjustments)],
            ]}
          />
          <p className="text-micro text-faint mt-2 leading-snug">{s?.capped_note}</p>
        </div>
      </div>

      <Disclosure title="Elasticity refinements" hint="what the loop has learned per product">
        {s?.refinements.length ? (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left label border-b border-line">
                <th className="py-1.5">SKU</th>
                <th className="text-right">Elasticity</th>
                <th className="text-right">95% interval</th>
                <th className="text-right">Refinements</th>
                <th className="text-right">Total adjustment</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {s.refinements.map((r) => (
                <tr key={r.sku} className="border-b border-hairline last:border-0">
                  <td className="py-1.5 font-mono text-tiny">{r.sku}</td>
                  <td className="text-right tabular-nums">{r.elasticity.toFixed(3)}</td>
                  <td className="text-right tabular-nums text-faint">
                    {r.ci_low.toFixed(2)} – {r.ci_high.toFixed(2)}
                  </td>
                  <td className="text-right tabular-nums">{r.refinement_count}</td>
                  <td className="text-right tabular-nums">{r.total_adjustment.toFixed(3)}</td>
                  <td className="text-tiny text-faint">{datetime(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No elasticity has been refined yet. Measure some outcomes on Impact.</Empty>
        )}
      </Disclosure>
    </Card>
  );
}

// --- Thresholds -------------------------------------------------------------

function ThresholdsCard({
  config,
  action,
  onChanged,
}: {
  config: PlatformConfig | null;
  action: Action;
  onChanged: () => void;
}) {
  const [thresholds, setThresholds] = useState({
    min_confidence: 0.75,
    max_delta_pct: 10,
    max_variance: 0.25,
    margin_buffer_pct: 3,
  });

  // Seeded once, for the same reason the gateway form is.
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current || !config) return;
    seeded.current = true;
    setThresholds(config.thresholds);
  }, [config]);

  const save = () =>
    action.run(async () => {
      await api.setThresholds(thresholds);
      onChanged();
      return 'Thresholds updated. They apply from the next run.';
    });

  return (
    <Card title="When the system may decide on its own">
      <p className="text-xs text-faint mb-3">
        A recommendation goes through automatically only if it clears every one of these
        and passes all policy checks. Tightening them moves work back to a person.
      </p>

      <div className="grid grid-cols-4 gap-4">
        {(
          [
            ['min_confidence', 'Minimum confidence', 0, 1, 0.01],
            ['max_delta_pct', 'Maximum price change %', 0.5, 50, 0.5],
            ['max_variance', 'Maximum variance', 0.05, 2, 0.01],
            ['margin_buffer_pct', 'Margin buffer (points)', 0, 20, 0.5],
          ] as const
        ).map(([key, label, min, max, step]) => (
          <div key={key}>
            <label className="block text-tiny text-faint mb-1" htmlFor={`th-${key}`}>
              {label}
            </label>
            <input
              id={`th-${key}`}
              type="number"
              min={min}
              max={max}
              step={step}
              value={thresholds[key]}
              onChange={(e) => setThresholds({ ...thresholds, [key]: Number(e.target.value) })}
              className="input text-right font-mono"
            />
          </div>
        ))}
      </div>

      <button className="btn mt-4" onClick={save} disabled={action.busy}>
        Apply thresholds
      </button>
    </Card>
  );
}

// --- Local bits -------------------------------------------------------------

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="text-lg font-semibold text-ink tabular-nums mt-0.5">{value}</div>
      {detail && <div className="text-micro text-faint mt-0.5 leading-snug">{detail}</div>}
    </div>
  );
}
