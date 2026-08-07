import { api } from '../lib/api';
import { useActionState, useApi } from '../lib/hooks';
import { money, moneyCompact, num, pct, ratio } from '../lib/format';
import { BandGlyph } from '../components/BandIndicator';
import { ErrorTrajectory } from '../components/Distribution';
import {
  Card,
  Empty,
  ErrorNote,
  SectionTitle,
  Spinner,
  Stat,
  Toast,
} from '../components/primitives';
import type { Band } from '../lib/types';

/**
 * Metrics (FR-056, FR-057, FR-061, FR-112, FR-113, PRD 9.1/9.3).
 *
 * Uplift is presented as a *comparison against the rule-based baseline on
 * identical data*, and accuracy as a deviation from the ground-truth optimum —
 * not as self-reported success. Where a number is not yet measurable, the
 * dashboard says so rather than showing a zero that reads like a result.
 */
export function Metrics() {
  const action = useActionState();
  const summary = useApi(() => api.metricsSummary(), [], 20000);
  const performance = useApi(() => api.performance(), [], 20000);
  const baseline = useApi(() => api.baseline(), [], 30000);
  const stability = useApi(() => api.stability(), [], 30000);
  const autonomy = useApi(() => api.autonomy(), [], 30000);
  const accuracy = useApi(() => api.accuracy(), [], 60000);
  const runs = useApi(() => api.runs(20), [], 30000);

  const reloadAll = () => {
    void summary.reload();
    void performance.reload();
    void stability.reload();
    void baseline.reload();
  };

  const advance = () =>
    action.run(async () => {
      const result = await api.advanceMarket(14);
      const back = await api.readback();
      reloadAll();
      return `${result.note} Measured ${back.measured} outcome(s), refined ${back.refined} elasticity estimate(s).`;
    });

  const trajectory = (stability.data?.error_trajectory ?? []).map(
    (row: { error_pct: number }) => row.error_pct,
  );

  return (
    <div className="p-7 space-y-5">
      <div className="flex items-start">
        <SectionTitle
          eyebrow={`Metrics · ${num(runs.data?.length ?? 0)} runs`}
          title="Measured against a rule-based baseline on identical data"
          lede="Uplift is a comparison, not an assertion. Accuracy is scored against the ground-truth elasticity embedded in the dataset, which the pipeline never reads."
        />
        <div className="ml-auto flex gap-2.5">
          <button className="btn" onClick={advance} disabled={action.busy}>
            Advance market 14d &amp; measure
          </button>
        </div>
      </div>

      {summary.error && <ErrorNote>{summary.error}</ErrorNote>}
      {summary.loading && !summary.data && <Spinner label="Loading metrics" />}

      <div className="grid grid-cols-6 gap-3">
        <Stat
          label="Revenue uplift vs baseline"
          value={summary.data?.ai_uplift_pct !== null && summary.data?.ai_uplift_pct !== undefined
            ? pct(summary.data.ai_uplift_pct)
            : '—'}
          detail="target ≥ 5%"
          tone="accent"
          accent="accent"
        />
        <Stat
          label="Accuracy vs ground truth"
          value={
            accuracy.data?.ai_mean_deviation_pct_high_confidence !== undefined
              ? pct(accuracy.data.ai_mean_deviation_pct_high_confidence)
              : '—'
          }
          detail="within 10% · high-confidence SKUs"
        />
        <Stat
          label="Acceptance rate"
          value={summary.data?.acceptance_rate !== null && summary.data?.acceptance_rate !== undefined
            ? ratio(summary.data.acceptance_rate)
            : 'no decisions'}
          detail="target ≥ 70%"
        />
        <Stat
          label="Auto-approve share"
          value={ratio(summary.data?.band_shares?.auto_approve ?? 0)}
          detail="target ≥ 60% in Assisted"
        />
        <Stat
          label="Forecast error"
          value={pct(summary.data?.mean_abs_forecast_error_pct ?? 0)}
          detail={`${num(summary.data?.outcomes_measured ?? 0)} outcomes measured`}
        />
        <Stat
          label="SKUs oscillating"
          value={ratio(summary.data?.oscillation_rate ?? 0, 1)}
          detail="target < 2%"
          tone={(summary.data?.oscillation_rate ?? 0) > 0.02 ? 'danger' : 'ink'}
        />
      </div>

      <div className="grid grid-cols-2 gap-5">
        <Card
          title="AI vs rule-based baseline"
          right={<span className="label">identical SKUs, days, costs</span>}
        >
          {baseline.data?.skus ? (
            <>
              <div className="grid grid-cols-3 gap-4">
                <Figure
                  label="AI priced higher"
                  value={num(baseline.data.agreement.ai_higher)}
                />
                <Figure
                  label="AI priced lower"
                  value={num(baseline.data.agreement.ai_lower)}
                />
                <Figure
                  label="Same price"
                  value={num(baseline.data.agreement.same_price)}
                  detail={`${ratio(baseline.data.agreement.agreement_rate)} agreement`}
                />
              </div>
              <div className="mt-4 pt-4 border-t border-hairline">
                <div className="label mb-2">Forecast revenue delta by category</div>
                {Object.entries(baseline.data.by_category ?? {})
                  .slice(0, 6)
                  .map(([name, value]) => {
                    const bucket = value as {
                      skus: number;
                      ai_revenue_delta: number;
                      mean_ai_price: number;
                      mean_baseline_price: number;
                    };
                    return (
                      <div
                        key={name}
                        className="flex items-center gap-3 py-1.5 border-b
                                   border-hairline last:border-0"
                      >
                        <span className="text-xs text-muted w-32">{name}</span>
                        <span className="text-tiny text-faint">
                          {num(bucket.skus)} SKUs
                        </span>
                        <span className="text-tiny text-faint">
                          AI {money(bucket.mean_ai_price)} vs baseline{' '}
                          {money(bucket.mean_baseline_price)}
                        </span>
                        <span className="ml-auto text-xs text-ink">
                          {moneyCompact(bucket.ai_revenue_delta)}
                        </span>
                      </div>
                    );
                  })}
              </div>
              <p className="text-tiny text-faint mt-3 leading-relaxed">
                {baseline.data.note}
              </p>
            </>
          ) : (
            <Empty>No completed run to compare yet.</Empty>
          )}
        </Card>

        <Card
          title="Convergence · forecast vs realized error"
          right={
            <span
              className={`text-tiny tracking-wide border rounded px-2 py-0.5 ${
                stability.data?.convergence?.converging
                  ? 'border-info/40 text-info'
                  : 'border-accent/40 text-accent'
              }`}
            >
              {stability.data?.convergence?.samples
                ? stability.data.convergence.converging
                  ? 'NARROWING'
                  : 'WIDENING — REVIEW'
                : 'NOT YET MEASURABLE'}
            </span>
          }
        >
          <ErrorTrajectory values={trajectory} />
          <p className="text-xs text-muted mt-3 leading-relaxed">
            {stability.data?.convergence?.detail ??
              'No realized outcomes yet. Push some prices, then advance the market to measure them.'}
          </p>
          <p className="text-tiny text-faint mt-2 leading-relaxed">
            Realized outcomes refine the elasticity estimates and the Monte Carlo input
            distributions each run. Every adjustment is capped and logged —{' '}
            {num(stability.data?.capped_adjustments ?? 0)} have hit the cap so far. A
            widening band raises the warning above rather than being absorbed.
          </p>
        </Card>
      </div>

      <div className="grid grid-cols-[1.1fr_1fr_1fr] gap-5">
        <Card title="Band distribution per run">
          {autonomy.data?.per_run?.length ? (
            <>
              <div className="flex items-end gap-2 h-32">
                {autonomy.data.per_run.slice(-14).map(
                  (
                    run: {
                      run_id: string;
                      auto_approve: number;
                      review: number;
                      escalate: number;
                    },
                    i: number,
                  ) => {
                    const total =
                      run.auto_approve + run.review + run.escalate || 1;
                    return (
                      <div
                        key={`${run.run_id}-${i}`}
                        className="flex-1 flex flex-col justify-end gap-0.5 h-full"
                        title={`${run.run_id}: ${run.auto_approve} auto, ${run.review} review, ${run.escalate} escalate`}
                      >
                        <div
                          className="bg-danger rounded-t-sm"
                          style={{ height: `${(run.escalate / total) * 100}%` }}
                        />
                        <div
                          className="bg-info"
                          style={{ height: `${(run.review / total) * 100}%` }}
                        />
                        <div
                          className="bg-accent rounded-b-sm"
                          style={{ height: `${(run.auto_approve / total) * 100}%` }}
                        />
                      </div>
                    );
                  },
                )}
              </div>
              <div className="flex gap-4 mt-3 flex-wrap">
                {(['auto_approve', 'review', 'escalate'] as Band[]).map((band) => (
                  <span key={band} className="flex items-center gap-2 text-tiny text-muted">
                    <BandGlyph band={band} />
                    {band.replace('_', '-')}{' '}
                    {ratio(autonomy.data?.band_shares?.[band] ?? 0)}
                  </span>
                ))}
              </div>
            </>
          ) : (
            <Empty>No runs to chart.</Empty>
          )}
        </Card>

        <Card title="Refinement ledger">
          {stability.data?.refinements?.length ? (
            <div className="space-y-1.5 max-h-44 overflow-y-auto">
              {stability.data.refinements.slice(0, 12).map(
                (row: {
                  sku: string;
                  elasticity: number;
                  refinement_count: number;
                  total_adjustment: number;
                }) => (
                  <div key={row.sku} className="flex items-center gap-3 text-tiny">
                    <span className="font-mono text-faint w-24 truncate">{row.sku}</span>
                    <span className="text-muted">{row.elasticity.toFixed(2)}</span>
                    <span className="text-faint">×{row.refinement_count}</span>
                    <span
                      className={`ml-auto ${
                        Math.abs(row.total_adjustment) > 0.5 ? 'text-accent' : 'text-muted'
                      }`}
                    >
                      {row.total_adjustment >= 0 ? '+' : ''}
                      {row.total_adjustment.toFixed(3)}
                    </span>
                  </div>
                ),
              )}
            </div>
          ) : (
            <Empty>No elasticity refinements yet.</Empty>
          )}
          <p className="text-tiny text-faint mt-3 leading-relaxed">
            Cumulative drift from the original regression is capped at 0.75, so a bad
            fortnight cannot walk an estimate away. Every step is in the audit log.
          </p>
        </Card>

        <Card title="Run history">
          {runs.data?.length ? (
            <div className="space-y-1.5 max-h-44 overflow-y-auto">
              {runs.data.map((run) => (
                <div key={run.run_id} className="flex items-center gap-2.5 text-tiny">
                  <span className="font-mono text-faint w-24 truncate">{run.run_id}</span>
                  <span className="text-muted">{num(run.sku_count)}</span>
                  <span className="text-faint">{run.mode}</span>
                  <span
                    className={`ml-auto ${
                      run.status === 'completed' ? 'text-muted' : 'text-danger'
                    }`}
                  >
                    {run.status}
                  </span>
                  <span className="text-faint w-12 text-right">
                    {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : '—'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Empty>No runs recorded.</Empty>
          )}
        </Card>
      </div>

      <Card title="Pricing accuracy against ground truth" accent="info">
        {accuracy.data?.scored ? (
          <div className="grid grid-cols-4 gap-5">
            <Figure
              label="SKUs scored"
              value={num(accuracy.data.scored)}
              detail={`${num(accuracy.data.high_confidence_skus)} high-confidence`}
            />
            <Figure
              label="AI mean deviation"
              value={pct(accuracy.data.ai_mean_deviation_pct)}
              detail={`high-confidence ${pct(accuracy.data.ai_mean_deviation_pct_high_confidence)}`}
            />
            <Figure
              label="Baseline mean deviation"
              value={pct(accuracy.data.baseline_mean_deviation_pct)}
              detail="the conventional pricer, same data"
            />
            <Figure
              label="Within 10%"
              value={
                accuracy.data.within_10pct_rate !== null
                  ? ratio(accuracy.data.within_10pct_rate)
                  : '—'
              }
              detail={accuracy.data.target}
            />
          </div>
        ) : (
          <Empty>{accuracy.data?.error ?? 'Run the pipeline to score accuracy.'}</Empty>
        )}
      </Card>

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}

function Figure({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="text-xl font-light text-ink mt-0.5">{value}</div>
      {detail && <div className="text-micro text-faint mt-0.5">{detail}</div>}
    </div>
  );
}
