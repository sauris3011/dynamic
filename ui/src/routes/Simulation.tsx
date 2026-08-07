import { useState } from 'react';

import { api } from '../lib/api';
import { useActionState, useApi } from '../lib/hooks';
import { money, moneyCompact, num, ratio, signedMoney, titleCase } from '../lib/format';
import {
  DistributionCurve,
  DistributionFacts,
  IntervalBar,
  StressRow,
} from '../components/Distribution';
import {
  Card,
  Empty,
  ErrorNote,
  Pill,
  SectionTitle,
  Spinner,
  Toast,
} from '../components/primitives';
import type { Objective, SimulationResult } from '../lib/types';

/**
 * Scenario simulation (W3, FR-045 .. FR-050, FR-105, FR-106).
 *
 * Results are distributions with their assumptions written out, never bare
 * point estimates — and they come from the *same* Monte Carlo engine the
 * pricing pipeline uses, so a what-if here and a recommendation forecast are
 * directly comparable rather than two numbers that happen to agree.
 */
export function Simulation() {
  const action = useActionState();
  const [sku, setSku] = useState('');
  const [horizon, setHorizon] = useState(28);
  const [objective, setObjective] = useState<Objective>('balanced');
  const [stress, setStress] = useState(true);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [selectedPrice, setSelectedPrice] = useState<number | null>(null);

  const pending = useApi(() => api.recommendations({ limit: 400 }), []);
  const scenarios = useApi(() => api.scenarios(), []);

  const run = (target: string) =>
    action.run(async () => {
      const response = await api.simulate({
        skus: [target],
        horizon_days: horizon,
        objective,
        include_stress: stress,
      });
      const first = response.results[0];
      setResult(first ?? null);
      setSelectedPrice(first?.ai_recommendation.price ?? null);
      return `Simulated ${target} over ${horizon} days.`;
    });

  const save = () =>
    action.run(async () => {
      if (!result) throw new Error('Run a simulation first.');
      const saved = await api.saveScenario({
        name: `${result.sku} · ${objective} · ${horizon}d`,
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
    <div className="p-7 space-y-5">
      <div className="flex items-start">
        <SectionTitle
          eyebrow="Scenario simulation"
          title="Project the outcome, and its uncertainty, before committing"
          lede="Same Monte Carlo engine as the pricing pipeline. A simulated outcome and a recommendation forecast are the same computation, so they are directly comparable."
        />
        <div className="ml-auto flex items-end gap-2.5">
          <div>
            <label className="label block mb-1" htmlFor="sku">
              SKU
            </label>
            <input
              id="sku"
              list="sku-options"
              className="input w-40 font-mono"
              value={sku}
              onChange={(e) => setSku(e.target.value)}
              placeholder="BEV-0001-1"
            />
            <datalist id="sku-options">
              {(pending.data ?? []).slice(0, 200).map((r) => (
                <option key={r.rec_id} value={r.sku}>
                  {r.product_name ?? ''}
                </option>
              ))}
            </datalist>
          </div>
          <div>
            <label className="label block mb-1" htmlFor="horizon">
              Horizon
            </label>
            <select
              id="horizon"
              className="input w-24"
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
              Objective
            </label>
            <select
              id="sim-objective"
              className="input w-28"
              value={objective}
              onChange={(e) => setObjective(e.target.value as Objective)}
            >
              <option value="balanced">Balanced</option>
              <option value="revenue">Revenue</option>
              <option value="margin">Margin</option>
            </select>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted h-[34px]">
            <input
              type="checkbox"
              checked={stress}
              onChange={(e) => setStress(e.target.checked)}
            />
            Stress-test
          </label>
          <button
            className="btn-primary h-[34px]"
            onClick={() => run(sku || pending.data?.[0]?.sku || '')}
            disabled={action.busy}
          >
            Simulate
          </button>
        </div>
      </div>

      {action.busy && <Spinner label="Running Monte Carlo" />}

      {result ? (
        <>
          {result.degraded && <ErrorNote>{result.note}</ErrorNote>}

          <div className="grid grid-cols-[1fr_22rem] gap-5">
            <Card
              title={`${result.product_name ?? result.sku} · ${money(result.current_price)} today`}
              right={
                <span className="label">
                  {result.horizon_days}-day horizon · {result.category}
                </span>
              }
            >
              <div className="space-y-1">
                <div className="grid grid-cols-[5rem_1fr_6rem_5rem_5rem] gap-3 label pb-1.5
                                border-b border-line">
                  <span>Price</span>
                  <span>90% revenue interval</span>
                  <span>Median</span>
                  <span>P(&lt;floor)</span>
                  <span>Compliant</span>
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
                        {isCurrent && (
                          <span className="text-micro text-faint block">current</span>
                        )}
                        {isAi && <span className="text-micro text-accent block">AI pick</span>}
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
                      <span className="text-xs text-muted">
                        {moneyCompact(candidate.revenue_p50)}
                      </span>
                      <span
                        className={`text-xs ${
                          candidate.prob_below_margin_floor > 0.08
                            ? 'text-danger'
                            : 'text-muted'
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
                The grey tick marks the outcome at today's price. Bars are the 90%
                interval; the heavy mark is the median. A price shown as blocked
                cannot ship regardless of how attractive its projection looks.
              </p>
            </Card>

            <div className="space-y-4">
              <Card title="Comparison" accent="accent">
                <div className="space-y-3">
                  <Compare
                    label="AI recommendation"
                    price={result.ai_recommendation.price}
                    detail={`${signedMoney(result.ai_recommendation.expected_revenue_delta)} over ${result.horizon_days}d · CI ${moneyCompact(result.ai_recommendation.revenue_ci_low)} — ${moneyCompact(result.ai_recommendation.revenue_ci_high)}`}
                    tone="accent"
                  />
                  <Compare
                    label="Rule-based baseline"
                    price={result.rule_based_baseline.price}
                    detail={result.rule_based_baseline.rules_fired.join(' · ')}
                  />
                  <Compare label="Current price" price={result.current_price} detail="do nothing" />
                </div>
                <p className="text-tiny text-faint mt-3 leading-relaxed">
                  {result.rule_based_baseline.note}
                </p>
              </Card>

              <Card title="Elasticity">
                {result.elasticity.usable ? (
                  <p className="text-xs text-muted leading-relaxed">
                    {result.elasticity.value?.toFixed(2)} (95% CI{' '}
                    {result.elasticity.ci_low?.toFixed(2)} to{' '}
                    {result.elasticity.ci_high?.toFixed(2)}, n=
                    {num(result.elasticity.sample_size)}). The simulation samples across
                    that interval, not the point estimate.
                  </p>
                ) : (
                  <p className="text-xs text-accent leading-relaxed">
                    No usable estimate: {result.elasticity.reason} Simulated against a
                    wide prior — treat the interval as indicative only.
                  </p>
                )}
              </Card>

              <button className="btn w-full" onClick={save} disabled={action.busy}>
                Save this scenario
              </button>
            </div>
          </div>

          <div className="grid grid-cols-[1fr_1fr] gap-5">
            {outcome && (
              <Card title={`Distribution at ${money(outcome.price)}`}>
                <DistributionCurve outcome={outcome} />
                <DistributionFacts outcome={outcome} />
              </Card>
            )}

            <div className="space-y-4">
              {result.stress && (
                <Card
                  title="Downside under stress"
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
                    Demand collapse, aggressive competitor undercut, and cost spike, each
                    re-simulated on the same engine. Visible before committing rather than
                    discovered afterwards.
                  </p>
                </Card>
              )}

              <Card title="Assumptions behind these numbers">
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
            <Card title="Saved scenarios">
              <div className="flex flex-wrap gap-2">
                {scenarios.data.slice(0, 12).map((scenario) => (
                  <Pill key={scenario.scenario_id}>
                    {scenario.name} · {scenario.horizon_days}d
                  </Pill>
                ))}
              </div>
            </Card>
          ) : null}
        </>
      ) : (
        !action.busy && (
          <Empty>
            Pick a SKU and simulate. Results are distributions with their assumptions
            stated — never a bare point estimate.
          </Empty>
        )
      )}

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
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
    <div className="flex items-baseline gap-3 pb-2.5 border-b border-hairline last:border-0">
      <div className="min-w-0 flex-1">
        <div className="text-xs text-muted">{label}</div>
        <div className="text-micro text-faint truncate" title={detail}>
          {detail}
        </div>
      </div>
      <span
        className={`text-lg font-light ${tone === 'accent' ? 'text-accent' : 'text-ink'}`}
      >
        {money(price)}
      </span>
    </div>
  );
}
