import { useNavigate } from 'react-router-dom';

import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { num, pct, ratio } from '../lib/format';
import { BAND_LABEL, MODE_LABEL, MODE_MEANING, TERMS } from '../lib/labels';
import { Card, SectionTitle, Stat } from '../components/primitives';
import type { PlatformConfig } from '../lib/types';

/**
 * The opening screen: what this does, where things stand, and where to go next.
 *
 * Deliberately has no controls of its own beyond two links. It exists so the
 * first thing a viewer sees is an explanation rather than a console, and so the
 * rest of the tabs have somewhere to be introduced from.
 */

const STEPS = [
  {
    title: 'It reads the facts',
    body: 'Your catalog, recent sales, stock levels, supplier agreements and pricing policy documents — every time, before it proposes anything.',
  },
  {
    title: 'It proposes a price',
    body: 'For each product, one price, with a forecast of what it would earn, a range around that forecast, and how confident it is.',
  },
  {
    title: 'You decide',
    body: 'Approve, reject, or set your own price. Only then does anything reach the store — and every step is recorded permanently.',
  },
];

const BOUNDARIES = [
  [
    'It never prices to a person',
    'Prices are set per product and per segment. The system refuses to produce a price aimed at an individual shopper — a permanent boundary, not a feature we have yet to build.',
  ],
  [
    'It never sees personal data',
    'Consumer-level data does not enter the platform at all, and secrets are stripped before anything reaches a language model.',
  ],
  [
    'It cannot overrule policy',
    'A price that fails a policy rule is blocked, and cannot be approved or overridden by anyone, in any mode.',
  ],
  [
    'Nothing is one-way',
    'Every price sent to the store records the one it replaced and reverts in a single action. Re-sending the same batch does nothing rather than applying twice.',
  ],
] as const;

export function Overview({
  counts,
  config,
}: {
  counts: { review: number; escalate: number; autoApprove: number; total: number };
  config: PlatformConfig | null;
}) {
  const navigate = useNavigate();
  const summary = useApi(() => api.metricsSummary(), [], 30000);

  const uplift = summary.data?.ai_uplift_pct;

  return (
    <div className="p-7 space-y-6 max-w-5xl">
      <SectionTitle
        eyebrow="Overview"
        title="Pricing AI"
        lede="It proposes a price for every product in your catalog, explains why, and waits
              for you on anything it is not sure about."
      />

      <div className="grid grid-cols-3 gap-4">
        {STEPS.map((step, i) => (
          <Card key={step.title}>
            <div className="flex items-baseline gap-2.5">
              <span className="text-2xl font-light text-accent leading-none">{i + 1}</span>
              <h3 className="text-sm font-bold text-ink">{step.title}</h3>
            </div>
            <p className="text-xs text-faint leading-relaxed mt-2">{step.body}</p>
          </Card>
        ))}
      </div>

      <div>
        <div className="label mb-2.5">Where things stand</div>
        <div className="grid grid-cols-4 gap-3.5">
          <Stat
            label="Waiting for you"
            value={num(counts.review)}
            detail={counts.review ? 'ready to review now' : 'nothing outstanding'}
            tone={counts.review ? 'info' : 'ink'}
            accent={counts.review ? 'info' : 'none'}
          />
          <Stat
            label={BAND_LABEL.auto_approve}
            value={num(counts.autoApprove)}
            detail="the system was confident enough to decide"
          />
          <Stat
            label={BAND_LABEL.escalate}
            value={num(counts.escalate)}
            detail="a policy rule stopped these"
            tone={counts.escalate ? 'danger' : 'ink'}
            accent={counts.escalate ? 'danger' : 'none'}
          />
          <Stat
            label="Revenue uplift"
            value={uplift !== null && uplift !== undefined ? pct(uplift) : '—'}
            detail={`against ${TERMS.baseline}`}
            tone="accent"
            accent="accent"
          />
        </div>
      </div>

      <div className="flex gap-3">
        <button className="btn-primary" onClick={() => navigate('/run')}>
          Price a batch of products
        </button>
        <button className="btn" onClick={() => navigate('/review')} disabled={!counts.total}>
          Review {counts.review ? `${num(counts.review)} waiting` : 'recommendations'} →
        </button>
        <button className="btn ml-auto" onClick={() => navigate('/impact')}>
          See what it was worth
        </button>
      </div>

      {config && (
        <Card title={`Right now: ${MODE_LABEL[config.mode]}`} accent="info">
          <p className="text-xs text-muted leading-relaxed">{MODE_MEANING[config.mode]}</p>
          <p className="text-tiny text-faint leading-relaxed mt-2">
            Change this in the top bar at any time. A price only goes through on its own if
            the system is at least {ratio(config.thresholds.min_confidence)} confident, the
            change is under {config.thresholds.max_delta_pct}%, the outcome is predictable
            enough, and every policy check passes. "Stop everything" is always one click
            away.
          </p>
        </Card>
      )}

      <div>
        <div className="label mb-2.5">What it will never do</div>
        <div className="grid grid-cols-2 gap-4">
          {BOUNDARIES.map(([title, body]) => (
            <Card key={title} title={title}>
              <p className="text-tiny text-faint leading-relaxed">{body}</p>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
