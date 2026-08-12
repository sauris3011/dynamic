import { useState } from 'react';

import { api } from '../lib/api';
import { useActionState, useApi } from '../lib/hooks';
import { money, moneyCompact, num, pct, ratio, signedMoney, titleCase } from '../lib/format';
import { OBJECTIVES, OBJECTIVE_LABEL, TERMS } from '../lib/labels';
import {
  DistributionCurve,
  DistributionFacts,
  IntervalBar,
  StressRow,
} from '../components/Distribution';
import {
  Card,
  Disclosure,
  Empty,
  ErrorNote,
  Figure,
  Pill,
  SectionTitle,
  Spinner,
  Stat,
  Toast,
} from '../components/primitives';
import type { Objective, SimulationResult } from '../lib/types';

/**
 * What the system was worth.
 *
 * Two questions, in order: did it beat the way we price today, and what would a
 * different price do? Everything that answers "is the model behaving" —
 * convergence, the refinement ledger, run durations — lives on System, because
 * this is the screen a commercial audience is shown.
 *
 * Uplift is a comparison against the rule-based pricer on identical data, and
 * accuracy is deviation from the ground-truth optimum. Neither is self-reported
 * success, and where a number is not yet measurable the screen says so rather
 * than showing a zero that reads like a result.
 */
export function Impact() {
  const action = useActionState();

  const summary = useApi(() => api.metricsSummary(), [], 30000);
  const baseline = useApi(() => api.baseline(), [], 30000);
  const accuracy = useApi(() => api.accuracy(), [], 60000);

  const measure = () =>
    action.run(async () => {
      const result = await api.advanceMarket(14);
      const back = await api.readback();
      void summary.reload();
      void baseline.reload();
      void accuracy.reload();
      return `${result.note} Measured ${back.measured} outcome(s), improved ${back.refined} demand estimate(s).`;
    });

  const uplift = summary.data?.ai_uplift_pct;
  const acceptance = summary.data?.acceptance_rate;
  const acc = accuracy.data;

  return (
    <div className="p-7 space-y-5 max-w-6xl">
      <div className="flex items-start gap-6">
        <SectionTitle
          eyebrow="Impact"
          title="What it was worth"
          lede={`Every number here is a comparison against ${TERMS.baseline} on exactly the
                 same products, days and costs — not a claim the system makes about itself.`}
        />
        <button className="btn ml-auto shrink-0" onClick={measure} disabled={action.busy}>
          Advance 14 days &amp; measure
        </button>
      </div>

      {summary.error && <ErrorNote>{summary.error}</ErrorNote>}
      {summary.loading && !summary.data && <Spinner label="Loading" />}

      <div className="grid grid-cols-4 gap-3.5">
        <Stat
          label="Revenue uplift"
          value={uplift !== null && uplift !== undefined ? pct(uplift) : '—'}
          detail={`against ${TERMS.baseline} · target 5% or better`}
          tone="accent"
          accent="accent"
        />
        <Stat
          label="Recommendations accepted"
          value={
            acceptance !== null && acceptance !== undefined
              ? ratio(acceptance)
              : 'nothing decided yet'
          }
          detail="of the ones a person decided on · target 70%"
        />
        <Stat
          label="Decided without a person"
          value={ratio(summary.data?.band_shares?.auto_approve ?? 0)}
          detail="target 60% once out of Supervised"
        />
        <Stat
          label="How close to the best price"
          value={
            acc?.ai_mean_deviation_pct_high_confidence !== undefined
              ? pct(acc.ai_mean_deviation_pct_high_confidence)
              : '—'
          }
          detail="average distance from the ideal, where we can check"
        />
      </div>

      <Card
        title={`Against ${TERMS.baseline}`}
        right={<span className="label">identical products, days and costs</span>}
      >
        {baseline.data?.skus && baseline.data.agreement ? (
          <>
            <div className="grid grid-cols-3 gap-5">
              <Figure label="Priced higher than the rules" value={num(baseline.data.agreement.ai_higher)} />
              <Figure label="Priced lower than the rules" value={num(baseline.data.agreement.ai_lower)} />
              <Figure
                label="Same price"
                value={num(baseline.data.agreement.same_price)}
                detail={`${ratio(baseline.data.agreement.agreement_rate)} of the time they agree`}
              />
            </div>

            <div className="mt-4 pt-4 border-t border-hairline">
              <div className="label mb-2">Forecast revenue gain by category</div>
              {Object.entries(baseline.data.by_category ?? {})
                .slice(0, 6)
                .map(([name, bucket]) => (
                  <div
                    key={name}
                    className="flex items-center gap-3 py-1.5 border-b border-hairline last:border-0"
                  >
                    <span className="text-xs text-muted w-32">{name}</span>
                    <span className="text-tiny text-faint w-20">{num(bucket.skus)} products</span>
                    <span className="text-tiny text-faint">
                      {money(bucket.mean_ai_price)} average vs{' '}
                      {money(bucket.mean_baseline_price)} under the rules
                    </span>
                    <span className="ml-auto text-xs text-ink tabular-nums">
                      {moneyCompact(bucket.ai_revenue_delta)}
                    </span>
                  </div>
                ))}
            </div>

            <p className="text-tiny text-faint mt-3 leading-relaxed">{baseline.data.note}</p>
          </>
        ) : (
          <Empty>{baseline.data?.detail ?? 'Nothing to compare yet. Run a batch first.'}</Empty>
        )}
      </Card>

      <Card title="Against what actually happened" accent="info">
        {acc?.scored ? (
          <div className="grid grid-cols-4 gap-5">
            <Figure
              label="Products checked"
              value={num(acc.scored)}
              detail={`${num(acc.high_confidence_skus)} where we were confident`}
            />
            <Figure
              label="How far off we were"
              value={pct(acc.ai_mean_deviation_pct)}
              detail={`${pct(acc.ai_mean_deviation_pct_high_confidence)} on the confident ones`}
            />
            <Figure
              label={`How far off ${TERMS.baseline} was`}
              value={pct(acc.baseline_mean_deviation_pct)}
              detail="the conventional pricer, same data"
            />
            <Figure
              label="Within 10% of ideal"
              value={
                acc.within_10pct_rate !== null && acc.within_10pct_rate !== undefined
                  ? ratio(acc.within_10pct_rate)
                  : '—'
              }
              detail={acc.target}
            />
          </div>
        ) : (
          <Empty>{acc?.error ?? 'Run a batch, then measure, to score accuracy.'}</Empty>
        )}
        <p className="text-tiny text-faint mt-3 leading-relaxed">
          Scored against the true demand curve built into the dataset — which the pricing
          pipeline itself never reads. This is a mark, not a self-assessment.
        </p>
      </Card>

      <WhatIf action={action} />

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}

/**
 * The simulator, collapsed by default.
 *
 * Same Monte Carlo engine as the pricing pipeline, so a what-if here and a
 * recommendation forecast are the same computation rather than two numbers that
 * happen to agree.
 */
function WhatIf({ action }: { action: ReturnType<typeof useActionState> }) {
  const [sku, setSku] = useState('');
  const [horizon, setHorizon] = useState(28);
  const [objective, setObjective] = useState<Objective>('balanced');
  const [stress, setStress] = useState(true);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [selectedPrice, setSelectedPrice] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const candidates = useApi(() => api.recommendations({ limit: 400 }), []);
  const scenarios = useApi(() => api.scenarios(), []);

  const run = (target: string) =>
    action.run(async () => {
      if (!target) throw new Error('Pick a product first.');
      setBusy(true);
      try {
        const response = await api.simulate({
          skus: [target],
          horizon_days: horizon,
          objective,
          include_stress: stress,
        });
        const first = response.results[0];
        setResult(first ?? null);
        setSelectedPrice(first?.ai_recommendation.price ?? null);
        return first
          ? `Projected ${target} over the next ${horizon} days.`
          : `No data for ${target}.`;
      } finally {
        setBusy(false);
      }
    });

  const save = () =>
    action.run(async () => {
      if (!result) throw new Error('Run a projection first.');
      const saved = await api.saveScenario({
        name: `${result.sku} · ${OBJECTIVE_LABEL[objective]} · ${horizon}d`,
        skus: [result.sku],
        horizon_days: horizon,
        objective,
        include_stress: stress,
      });
      await scenarios.reload();
      return `Saved as ${saved.name}.`;
    });

  const outcome =
    result?.candidates.find((c) => c.price === selectedPrice) ?? result?.candidates[0];
  const revenues = result?.candidates.flatMap((c) => [c.revenue_p5, c.revenue_p95]) ?? [];
  const min = Math.min(...revenues, 0);
  const max = Math.max(...revenues, 1);

  return (
    <Disclosure
      title="What if we priced it differently?"
      hint="project one product before committing"
    >
      <div className="flex items-end gap-2.5 flex-wrap">
        <div>
          <label className="label block mb-1" htmlFor="sim-sku">
            Product
          </label>
          <input
            id="sim-sku"
            list="sku-options"
            className="input w-48 font-mono"
            value={sku}
            onChange={(e) => setSku(e.target.value)}
            placeholder="BEV-0001-1"
          />
          <datalist id="sku-options">
            {(candidates.data ?? []).slice(0, 200).map((r) => (
              <option key={r.rec_id} value={r.sku}>
                {r.product_name ?? ''}
              </option>
            ))}
          </datalist>
        </div>

        <div>
          <label className="label block mb-1" htmlFor="horizon">
            Look ahead
          </label>
          <select
            id="horizon"
            className="input w-28"
            value={horizon}
            onChange={(e) => setHorizon(Number(e.target.value))}
          >
            {[7, 14, 28, 90].map((d) => (
              <option key={d} value={d}>
                {d} days
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="label block mb-1" htmlFor="sim-objective">
            Goal
          </label>
          <select
            id="sim-objective"
            className="input w-36"
            value={objective}
            onChange={(e) => setObjective(e.target.value as Objective)}
          >
            {OBJECTIVES.map((o) => (
              <option key={o} value={o}>
                {OBJECTIVE_LABEL[o]}
              </option>
            ))}
          </select>
        </div>

        <label className="flex items-center gap-2 text-xs text-muted h-[34px]">
          <input
            type="checkbox"
            checked={stress}
            onChange={(e) => setStress(e.target.checked)}
          />
          Include a bad-case check
        </label>

        <button
          className="btn-primary h-[34px]"
          onClick={() => run(sku || candidates.data?.[0]?.sku || '')}
          disabled={action.busy}
        >
          Project
        </button>
      </div>

      {busy && <Spinner label="Simulating" />}

      {!result && !busy && (
        <div className="mt-4">
          <Empty>
            Pick a product and project it. Results come back as a range with the
            assumptions written out, never a single number.
          </Empty>
        </div>
      )}

      {result && (
        <div className="mt-5 space-y-5">
          {result.degraded && <ErrorNote>{result.note}</ErrorNote>}

          <div className="grid grid-cols-[1fr_20rem] gap-5">
            <Card
              title={`${result.product_name ?? result.sku} · ${money(
                result.current_price,
              )} today`}
              right={
                <span className="label">
                  {result.horizon_days} days · {result.category}
                </span>
              }
            >
              <div className="space-y-1">
                <div className="grid grid-cols-[5rem_1fr_6rem_5rem_5rem] gap-3 label pb-1.5 border-b border-line">
                  <span>Price</span>
                  <span>Likely revenue range</span>
                  <span>Middle</span>
                  <span>Risk</span>
                  <span>Allowed</span>
                </div>
                {result.candidates.map((candidate) => {
                  const compliant =
                    result.compliance[candidate.price.toFixed(2)]?.passed ?? true;
                  const isAi = candidate.price === result.ai_recommendation.price;
                  const isCurrent = candidate.price === result.current_price;
                  return (
                    <button
                      key={candidate.price}
                      onClick={() => setSelectedPrice(candidate.price)}
                      className={`w-full grid grid-cols-[5rem_1fr_6rem_5rem_5rem] gap-3
                                  items-center py-2 text-left border-b border-hairline
                                  last:border-0 hover:bg-line/20 ${
                                    selectedPrice === candidate.price ? 'bg-line/25' : ''
                                  }`}
                    >
                      <span className="text-xs">
                        <span className={isAi ? 'text-accent font-semibold' : 'text-ink'}>
                          {money(candidate.price)}
                        </span>
                        {isCurrent && <span className="text-micro text-faint block">today</span>}
                        {isAi && (
                          <span className="text-micro text-accent block">what it picked</span>
                        )}
                      </span>
                      <IntervalBar
                        low={candidate.revenue_p5}
                        mid={candidate.revenue_p50}
                        high={candidate.revenue_p95}
                        min={min}
                        max={max}
                        baseline={result.baseline.revenue}
                        tone={isAi ? 'accent' : 'info'}
                      />
                      <span className="text-xs text-muted tabular-nums">
                        {moneyCompact(candidate.revenue_p50)}
                      </span>
                      <span
                        className={`text-xs tabular-nums ${
                          candidate.prob_below_margin_floor > 0.08 ? 'text-danger' : 'text-muted'
                        }`}
                      >
                        {ratio(candidate.prob_below_margin_floor, 1)}
                      </span>
                      <span className={`text-tiny ${compliant ? 'text-muted' : 'text-danger'}`}>
                        {compliant ? 'yes' : 'blocked'}
                      </span>
                    </button>
                  );
                })}
              </div>
              <p className="text-tiny text-faint mt-3 leading-relaxed">
                The grey tick is what today's price would earn. Bars show the likely range;
                the heavy mark is the middle. "Risk" is the chance of dropping below the
                margin floor. A price marked blocked cannot ship however good it looks.
              </p>
            </Card>

            <div className="space-y-4">
              <Card title="Three ways to price it" accent="accent">
                <Compare
                  label="What the system picked"
                  price={result.ai_recommendation.price}
                  detail={`${signedMoney(
                    result.ai_recommendation.expected_revenue_delta,
                  )} over ${result.horizon_days} days · likely ${moneyCompact(
                    result.ai_recommendation.revenue_ci_low,
                  )} to ${moneyCompact(result.ai_recommendation.revenue_ci_high)}`}
                  tone="accent"
                />
                <Compare
                  label={TERMS.baselineTitle}
                  price={result.rule_based_baseline.price}
                  detail={result.rule_based_baseline.rules_fired.join(' · ')}
                />
                <Compare
                  label="Leave it alone"
                  price={result.current_price}
                  detail="today's price"
                />
                <p className="text-tiny text-faint mt-3 leading-relaxed">
                  {result.rule_based_baseline.note}
                </p>
              </Card>

              <button className="btn w-full" onClick={save} disabled={action.busy}>
                Save this scenario
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-5">
            {outcome && (
              <Card title={`Range of outcomes at ${money(outcome.price)}`}>
                <DistributionCurve outcome={outcome} />
                <DistributionFacts outcome={outcome} />
              </Card>
            )}

            <div className="space-y-4">
              {result.stress && (
                <Card
                  title="If things go badly"
                  right={<span className="label">at {money(result.stress.price_tested)}</span>}
                >
                  {Object.entries(result.stress.scenarios).map(([name, scenario]) => (
                    <StressRow
                      key={name}
                      name={titleCase(name)}
                      outcome={scenario}
                      baseline={result.baseline.revenue}
                    />
                  ))}
                  <p className="text-tiny text-faint mt-3 leading-relaxed">
                    Demand collapsing, a competitor undercutting, and costs spiking — each
                    re-run on the same engine. Visible before committing rather than
                    discovered afterwards.
                  </p>
                </Card>
              )}

              <Card title="What these numbers assume">
                <ul className="space-y-2">
                  {result.assumptions.map((assumption, i) => (
                    <li key={i} className="text-tiny text-muted leading-relaxed flex gap-2.5">
                      <span className="text-faint shrink-0">{String(i + 1).padStart(2, '0')}</span>
                      {assumption}
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          </div>

          {scenarios.data?.length ? (
            <div>
              <div className="label mb-1.5">Saved scenarios</div>
              <div className="flex flex-wrap gap-2">
                {scenarios.data.slice(0, 12).map((scenario) => (
                  <Pill key={scenario.scenario_id}>
                    {scenario.name} · {scenario.horizon_days}d
                  </Pill>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </Disclosure>
  );
}

function Compare({
  label,
  price,
  detail,
  tone,
}: {
  label: string;
  price: number;
  detail: string;
  tone?: 'accent';
}) {
  return (
    <div className="flex items-baseline gap-3 pb-2.5 mb-2.5 border-b border-hairline last:border-0 last:mb-0">
      <div className="min-w-0 flex-1">
        <div className="text-xs text-muted">{label}</div>
        <div className="text-micro text-faint truncate" title={detail}>
          {detail}
        </div>
      </div>
      <span className={`text-lg font-light ${tone === 'accent' ? 'text-accent' : 'text-ink'}`}>
        {money(price)}
      </span>
    </div>
  );
}
