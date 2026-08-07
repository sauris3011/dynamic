import type { PriceOutcome } from '../lib/types';
import { moneyCompact, pct, ratio } from '../lib/format';

/**
 * Monte Carlo results render as distributions, never bare numbers (FR-119).
 *
 * A point estimate implies a precision the data does not have, and the whole
 * autonomy argument rests on the interval being real and visible: the variance
 * shown here is the same number the Escalate band's volatility trigger reads.
 * Showing the median alone would hide exactly the quantity that decides whether
 * a recommendation is safe to auto-approve.
 *
 * Both themes are handled by semantic tokens, so the chart is legible in each
 * without a second implementation.
 */

/** Interval bar: P5 — median — P95 against a shared scale. */
export function IntervalBar({
  low,
  mid,
  high,
  min,
  max,
  baseline,
  tone = 'info',
}: {
  low: number;
  mid: number;
  high: number;
  min: number;
  max: number;
  baseline?: number;
  tone?: 'info' | 'accent' | 'danger';
}) {
  const span = Math.max(max - min, 1e-9);
  const at = (v: number) => ((v - min) / span) * 100;
  const fill = { info: 'bg-info', accent: 'bg-accent', danger: 'bg-danger' }[tone];

  return (
    <div className="relative h-5" role="img"
         aria-label={`90% interval from ${moneyCompact(low)} to ${moneyCompact(high)}, median ${moneyCompact(mid)}`}>
      <div className="absolute inset-x-0 top-1/2 h-px bg-line" />
      <div
        className={`absolute top-1/2 -translate-y-1/2 h-2 rounded-sm opacity-30 ${fill}`}
        style={{ left: `${at(low)}%`, width: `${Math.max(at(high) - at(low), 0.5)}%` }}
      />
      <div
        className={`absolute top-1/2 -translate-y-1/2 w-[3px] h-4 rounded-sm ${fill}`}
        style={{ left: `${at(mid)}%` }}
      />
      {baseline !== undefined && (
        <div
          className="absolute top-1/2 -translate-y-1/2 w-px h-5 bg-faint"
          style={{ left: `${at(baseline)}%` }}
          title="Current price outcome"
        />
      )}
    </div>
  );
}

/**
 * Histogram approximated from the reported percentiles.
 *
 * The API returns P5/P50/P95 rather than the raw 20,000 draws — shipping the
 * full sample per SKU would be megabytes for a picture. The shape here is a
 * skew-aware reconstruction, and it is labelled as such rather than implying we
 * are drawing the sample itself.
 */
export function DistributionCurve({
  outcome,
  height = 140,
  bins = 26,
}: {
  outcome: PriceOutcome;
  height?: number;
  bins?: number;
}) {
  const { revenue_p5: p5, revenue_p50: p50, revenue_p95: p95 } = outcome;
  const lo = p5 - (p50 - p5) * 0.6;
  const hi = p95 + (p95 - p50) * 0.6;
  const span = Math.max(hi - lo, 1e-9);

  // Two half-Gaussians about the median, each scaled to its own tail, so a
  // right-skewed revenue distribution reads as right-skewed.
  const leftSd = Math.max((p50 - p5) / 1.645, 1e-6);
  const rightSd = Math.max((p95 - p50) / 1.645, 1e-6);

  const values = Array.from({ length: bins }, (_, i) => {
    const x = lo + (span * (i + 0.5)) / bins;
    const sd = x < p50 ? leftSd : rightSd;
    return Math.exp(-0.5 * ((x - p50) / sd) ** 2);
  });
  const peak = Math.max(...values, 1e-9);

  const inInterval = (i: number) => {
    const x = lo + (span * (i + 0.5)) / bins;
    return x >= p5 && x <= p95;
  };
  const medianBin = Math.round(((p50 - lo) / span) * bins - 0.5);

  return (
    <div>
      <div className="flex items-end gap-[2px]" style={{ height }}>
        {values.map((v, i) => (
          <div
            key={i}
            className={`flex-1 rounded-t-sm ${
              i === medianBin
                ? 'bg-accent'
                : inInterval(i)
                  ? 'bg-info/45'
                  : 'bg-line'
            }`}
            style={{ height: `${Math.max((v / peak) * 100, 2)}%` }}
          />
        ))}
      </div>
      <div className="flex justify-between text-tiny text-faint mt-1.5">
        <span>{moneyCompact(lo)}</span>
        <span>P5 {moneyCompact(p5)}</span>
        <span className="text-accent">median {moneyCompact(p50)}</span>
        <span>P95 {moneyCompact(p95)}</span>
        <span>{moneyCompact(hi)}</span>
      </div>
      <p className="text-tiny text-faint mt-2 leading-relaxed">
        Shape reconstructed from the reported P5/P50/P95 of the simulated sample.
        The percentiles, variance and floor probability are exact; the curve
        between them is illustrative.
      </p>
    </div>
  );
}

export function DistributionFacts({ outcome }: { outcome: PriceOutcome }) {
  return (
    <div className="grid grid-cols-3 gap-4 pt-3 mt-3 border-t border-hairline">
      <div>
        <div className="label">P(below margin floor)</div>
        <div
          className={`text-xl font-light mt-0.5 ${
            outcome.prob_below_margin_floor > 0.08 ? 'text-danger' : 'text-ink'
          }`}
        >
          {ratio(outcome.prob_below_margin_floor, 1)}
        </div>
      </div>
      <div>
        <div className="label">Outcome variance</div>
        <div className="text-xl font-light text-ink mt-0.5">
          {outcome.revenue_cv.toFixed(2)}
        </div>
      </div>
      <div>
        <div className="label">P(revenue gain)</div>
        <div className="text-xl font-light text-ink mt-0.5">
          {ratio(outcome.prob_revenue_gain, 1)}
        </div>
      </div>
    </div>
  );
}

/** Compact sparkline for a series of forecast errors over time. */
export function ErrorTrajectory({
  values,
  height = 90,
}: {
  values: number[];
  height?: number;
}) {
  if (values.length < 2) {
    return (
      <p className="text-xs text-faint py-4">
        Not enough measured outcomes yet to draw a trajectory.
      </p>
    );
  }
  const bound = Math.max(...values.map(Math.abs), 5);
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * 100;
      const y = 50 - (v / bound) * 45;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');

  return (
    <div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ height }}
           className="w-full" role="img" aria-label="Forecast error over successive outcomes">
        <line x1="0" y1="50" x2="100" y2="50" stroke="rgb(var(--line))" strokeWidth="0.5" />
        <polyline
          points={points}
          fill="none"
          stroke="rgb(var(--info))"
          strokeWidth="1.2"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="flex justify-between text-tiny text-faint mt-1">
        <span>oldest</span>
        <span>zero error</span>
        <span>newest</span>
      </div>
    </div>
  );
}

export function StressRow({ name, outcome, baseline }: {
  name: string;
  outcome: PriceOutcome;
  baseline: number;
}) {
  const delta = ((outcome.expected_revenue - baseline) / Math.max(baseline, 1e-9)) * 100;
  return (
    <div className="flex items-center gap-3 py-2 border-b border-hairline last:border-0">
      <span className="text-xs text-muted w-44">{name}</span>
      <span className="text-xs text-ink w-24 text-right">
        {moneyCompact(outcome.expected_revenue)}
      </span>
      <span className={`text-xs w-20 text-right ${delta < 0 ? 'text-danger' : 'text-ink'}`}>
        {pct(delta)}
      </span>
      <span className="text-tiny text-faint ml-auto">
        P(below floor) {ratio(outcome.prob_below_margin_floor, 1)}
      </span>
    </div>
  );
}
