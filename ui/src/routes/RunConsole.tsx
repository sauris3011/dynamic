import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, categories } from '../lib/api';
import { useApi, useActionState } from '../lib/hooks';
import { duration, money, num, signedPct, titleCase } from '../lib/format';
import { BandGlyph } from '../components/BandIndicator';
import {
  Card,
  ErrorNote,
  Empty,
  Meter,
  SectionTitle,
  Stat,
  Toast,
} from '../components/primitives';
import type { Band, Objective, PlatformConfig, RunProgress } from '../lib/types';

/** Run console: trigger a run and watch the pipeline stream (W1, FR-069). */

const STAGES = [
  {
    key: 'data_context',
    n: '01',
    name: 'Data & Context',
    model: 'narrator + analyst',
    detail:
      'Four ingestion paths run concurrently, then the quality gate decides whether pricing may proceed at all.',
  },
  {
    key: 'quantitative',
    n: '02',
    name: 'Quantitative',
    model: 'analyst',
    detail:
      'Elasticity regression per SKU, then Monte Carlo over that interval — the causal layer before the uncertainty layer, never the other way round.',
  },
  {
    key: 'strategy',
    n: '03',
    name: 'Strategy & Reasoning',
    model: 'strategist',
    detail:
      'Constrained optimizer against the simulated distributions, with the rule-based baseline computed alongside for comparison.',
  },
  {
    key: 'validation',
    n: '04',
    name: 'Validation & Compliance',
    model: 'strategist · explain only',
    detail:
      'Rule engine veto, oscillation and convergence checks, deterministic band assignment. No model decides anything here.',
  },
  {
    key: 'narration',
    n: '05',
    name: 'Narration & Execution',
    model: 'no LLM in the push path',
    detail:
      'Grounded rationale where a gateway is available, then idempotent push and the audit write. Narration is additive — it never changes a price.',
  },
];

export function RunConsole({
  config,
  onChanged,
}: {
  config: PlatformConfig | null;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const action = useActionState();
  const [runId, setRunId] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem('pricing_active_run_id');
    } catch {
      return null;
    }
  });
  const [scopeKind, setScopeKind] = useState<'all' | 'category'>('category');
  const [category, setCategory] = useState(categories[0]);
  const [objective, setObjective] = useState<Objective>('balanced');

  const runs = useApi(() => api.runs(12), [], 10000);
  const active = runs.data?.[0];
  const watching = runId ?? active?.run_id ?? null;

  const progress = useApi<RunProgress | null>(
    () => (watching ? api.runProgress(watching) : Promise.resolve(null)),
    [watching],
    2000,
  );

  const running = progress.data?.status === 'running' || progress.data?.status === 'queued';
  useEffect(() => {
    if (!running && runId) {
      try {
        sessionStorage.removeItem('pricing_active_run_id');
      } catch {
        // ignore
      }
      void runs.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  const start = () =>
    action.run(async () => {
      const result = await api.startRun({
        scope_kind: scopeKind,
        scope_value: scopeKind === 'category' ? category : null,
        objective,
      });
      try {
        sessionStorage.setItem('pricing_active_run_id', result.run_id);
      } catch {
        // ignore
      }
      setRunId(result.run_id);
      onChanged();
      return `Run ${result.run_id} started over ${result.scope} in ${result.mode} mode.`;
    });

  const bands = progress.data?.bands ?? {};
  const totalBands = Object.values(bands).reduce((a, b) => a + (b ?? 0), 0);

  return (
    <div className="p-7 space-y-6">
      <div className="flex items-start gap-4">
        <SectionTitle
          eyebrow={`Run console${watching ? ` · ${watching}` : ''}`}
          title={
            progress.data?.sku_count
              ? `${progress.data.sku_count} SKUs priced · ${objective} objective`
              : 'Trigger a pricing run'
          }
          lede={
            config?.mode === 'supervised'
              ? 'Supervised mode is in force. Nothing pushes without a human decision.'
              : `${titleCase(config?.mode ?? '')} mode: auto-approve items push without human action. Escalations never do.`
          }
        />
        <div className="ml-auto flex items-end gap-2.5">
          <div>
            <label className="label block mb-1" htmlFor="scope">
              Scope
            </label>
            <select
              id="scope"
              className="input w-44"
              value={scopeKind === 'all' ? 'all' : category}
              onChange={(e) => {
                if (e.target.value === 'all') setScopeKind('all');
                else {
                  setScopeKind('category');
                  setCategory(e.target.value);
                }
              }}
            >
              <option value="all">Full catalog</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label block mb-1" htmlFor="objective">
              Objective
            </label>
            <select
              id="objective"
              className="input w-32"
              value={objective}
              onChange={(e) => setObjective(e.target.value as Objective)}
            >
              <option value="balanced">Balanced</option>
              <option value="revenue">Revenue</option>
              <option value="margin">Margin</option>
            </select>
          </div>
          <button className="btn-primary h-[34px]" onClick={start} disabled={action.busy || running}>
            {running ? 'RUNNING…' : 'RUN PRICING'}
          </button>
        </div>
      </div>

      {progress.data?.errors?.length ? (
        <ErrorNote>
          <strong>Run {progress.data.status}.</strong> {progress.data.errors.join(' · ')}
        </ErrorNote>
      ) : null}

      <div className="grid grid-cols-4 gap-3.5">
        <Stat
          label="Elapsed"
          value={duration(active?.duration_ms)}
          detail={`budget 5:00 · Monte Carlo ${config?.monte_carlo.iterations ?? '—'} iters`}
          tone="accent"
          accent="accent"
        />
        <Stat
          label="SKUs priced"
          value={num(progress.data?.sku_count ?? active?.sku_count ?? 0)}
          detail={
            progress.data?.quality
              ? `data quality verdict: ${progress.data.quality}`
              : 'awaiting quality gate'
          }
        />
        <Stat
          label="Escalations"
          value={num(bands.escalate ?? active?.escalated ?? 0)}
          detail="blocked pending human judgement"
          tone={bands.escalate ? 'danger' : 'ink'}
          accent={bands.escalate ? 'danger' : 'none'}
        />
        <Stat
          label="Token spend"
          value={money(active?.cost_usd ?? 0)}
          detail={`${num((active?.tokens_in ?? 0) + (active?.tokens_out ?? 0))} tokens this run`}
        />
      </div>

      <div className="grid grid-cols-[1fr_22rem] gap-5">
        <div className="space-y-2.5">
          <div className="label">Agent pipeline</div>
          {STAGES.map((stage) => {
            const seconds = progress.data?.stages?.[stage.key];
            const done = seconds !== undefined;
            const isCurrent =
              running && progress.data?.stage === stage.key && !done;
            return (
              <div
                key={stage.key}
                className={`card p-3.5 border-l-2 ${
                  isCurrent
                    ? 'border-l-accent'
                    : done
                      ? 'border-l-line'
                      : 'border-l-hairline'
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className="text-tiny text-faint w-5">{stage.n}</span>
                  <span
                    className={`text-sm font-bold ${done || isCurrent ? 'text-ink' : 'text-faint'}`}
                  >
                    {stage.name}
                  </span>
                  <span className="text-tiny text-faint border border-line rounded px-1.5 py-0.5">
                    {stage.model}
                  </span>
                  <span className="ml-auto text-tiny text-faint">
                    {done ? `${seconds.toFixed(2)}s` : '—'}
                  </span>
                  <span
                    className={`text-tiny tracking-wide ${
                      isCurrent ? 'text-info' : done ? 'text-muted' : 'text-faint'
                    }`}
                  >
                    {isCurrent ? 'RUNNING' : done ? 'COMPLETE' : 'QUEUED'}
                  </span>
                </div>
                <p className="text-xs text-faint mt-1.5 pl-8 leading-relaxed">
                  {stage.detail}
                </p>
              </div>
            );
          })}
        </div>

        <div className="space-y-4">
          <Card title="Band outcome">
            {totalBands ? (
              <>
                {(['auto_approve', 'review', 'escalate'] as Band[]).map((band) => (
                  <div
                    key={band}
                    className="flex items-center gap-2.5 py-2.5 border-b border-hairline last:border-0"
                  >
                    <BandGlyph band={band} size={10} />
                    <span className="text-xs text-ink tracking-wide">
                      {titleCase(band)}
                    </span>
                    <span className="ml-auto text-lg font-light text-ink">
                      {num(bands[band] ?? 0)}
                    </span>
                  </div>
                ))}
                <Meter value={(bands.auto_approve ?? 0) / totalBands} tone="accent" />
                <p className="text-tiny text-faint mt-2.5 leading-relaxed">
                  {config?.mode === 'supervised'
                    ? `In Supervised mode all ${num(bands.auto_approve ?? 0)} auto-approve-eligible items are still routed to review. Switching to Assisted would push them.`
                    : 'Auto-approve items push without human action; escalations never do, in any mode.'}
                </p>
              </>
            ) : (
              <Empty>No completed run yet. Trigger one above.</Empty>
            )}
          </Card>

          <Card title="Recent runs">
            {runs.data?.length ? (
              <div className="space-y-1.5">
                {runs.data.slice(0, 8).map((run) => (
                  <button
                    key={run.run_id}
                    onClick={() => setRunId(run.run_id)}
                    className="w-full flex items-center gap-2.5 py-1.5 text-left
                               border-b border-hairline last:border-0 hover:bg-line/20"
                  >
                    <span className="font-mono text-tiny text-faint truncate w-32">
                      {run.run_id}
                    </span>
                    <span className="text-tiny text-muted">
                      {run.scope_value ?? 'catalog'}
                    </span>
                    <span
                      className={`ml-auto text-tiny ${
                        run.status === 'completed'
                          ? 'text-muted'
                          : run.status === 'halted'
                            ? 'text-accent'
                            : 'text-danger'
                      }`}
                    >
                      {run.status}
                    </span>
                    <span className="text-tiny text-faint w-14 text-right">
                      {num(run.sku_count)} SKU
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <Empty>No runs recorded.</Empty>
            )}
          </Card>

          <button
            className="btn w-full"
            onClick={() => navigate('/queue')}
            disabled={!totalBands}
          >
            Open the review queue
          </button>
        </div>
      </div>

      <QualityGate progress={progress.data} />
      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}

function QualityGate({ progress }: { progress: RunProgress | null }) {
  const verdict = progress?.quality;
  if (!verdict) return null;
  const halted = progress?.status === 'halted';
  return (
    <Card
      title="Data quality gate"
      accent={halted ? 'danger' : verdict === 'warn' ? 'accent' : 'info'}
      right={
        <span
          className={`text-tiny tracking-wide border rounded px-2 py-0.5 ${
            halted
              ? 'border-danger/40 text-danger'
              : verdict === 'warn'
                ? 'border-accent/40 text-accent'
                : 'border-info/40 text-info'
          }`}
        >
          {halted ? 'FAIL — RUN HALTED' : verdict === 'warn' ? 'WARN' : 'PASS'}
        </span>
      }
    >
      <p className="text-xs text-muted leading-relaxed">
        {halted
          ? 'The gate failed, so the run stopped before pricing anything. The system does not price on data it has judged unfit — a confident recommendation built on broken inputs is more dangerous than no recommendation.'
          : verdict === 'warn'
            ? 'Warnings were raised. The run proceeded, but automatic approval is withheld for affected items and their confidence is reduced.'
            : 'Freshness, completeness, schema conformance, referential integrity and outlier checks all passed.'}
      </p>
      {progress?.stages && (
        <p className="text-tiny text-faint mt-2">
          Stage timings:{' '}
          {Object.entries(progress.stages)
            .map(([k, v]) => `${titleCase(k)} ${v.toFixed(2)}s`)
            .join(' · ')}
        </p>
      )}
      {progress?.bands && (
        <p className="text-tiny text-faint mt-1">
          Forecast movement across the run:{' '}
          {signedPct(
            ((progress.bands.auto_approve ?? 0) /
              Math.max(
                Object.values(progress.bands).reduce((a, b) => a + (b ?? 0), 0),
                1,
              )) *
              100,
          )}{' '}
          of items cleared every auto-approve condition.
        </p>
      )}
    </Card>
  );
}
