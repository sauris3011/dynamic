import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api } from '../lib/api';
import { useActionState, useApi } from '../lib/hooks';
import { money, moneyCompact, num, ratio, signedPct } from '../lib/format';
import { BandGlyph, BandLegend } from '../components/BandIndicator';
import { Card, Empty, ErrorNote, Meter, SectionTitle, Spinner, Toast } from '../components/primitives';
import type { Band, Recommendation } from '../lib/types';

/**
 * Review queue (W2, FR-024, FR-026).
 *
 * The purpose is to show the exceptions, not the catalog. Bulk approve exists
 * but is deliberately bounded: Review band only, above the confidence
 * threshold, clean compliance. A bulk control that could sweep escalations
 * would turn the band model into decoration.
 */

const BULK_CONFIDENCE_FLOOR = 0.75;

type SortKey = 'impact' | 'confidence' | 'delta' | 'sku';

export function ReviewQueue({ onChanged }: { onChanged: () => void }) {
  const navigate = useNavigate();
  const action = useActionState();
  const [band, setBand] = useState<Band | 'all'>('all');
  const [sort, setSort] = useState<SortKey>('impact');
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const recs = useApi(
    () => api.recommendations({ status: 'pending', limit: 3000 }),
    [],
    20000,
  );

  const rows = useMemo(() => {
    const all = recs.data ?? [];
    const filtered = band === 'all' ? all : all.filter((r) => r.band === band);
    const sorters: Record<SortKey, (a: Recommendation, b: Recommendation) => number> = {
      impact: (a, b) =>
        Math.abs(b.expected_revenue_delta ?? 0) - Math.abs(a.expected_revenue_delta ?? 0),
      confidence: (a, b) => a.confidence - b.confidence,
      delta: (a, b) => Math.abs(b.delta_pct) - Math.abs(a.delta_pct),
      sku: (a, b) => a.sku.localeCompare(b.sku),
    };
    return [...filtered].sort(sorters[sort]);
  }, [recs.data, band, sort]);

  const counts = useMemo(() => {
    const all = recs.data ?? [];
    return {
      all: all.length,
      auto_approve: all.filter((r) => r.band === 'auto_approve').length,
      review: all.filter((r) => r.band === 'review').length,
      escalate: all.filter((r) => r.band === 'escalate').length,
    };
  }, [recs.data]);

  // FR-024: eligibility is computed, not assumed, and shown before the click.
  const bulkEligible = useMemo(
    () =>
      (recs.data ?? []).filter(
        (r) =>
          r.band === 'review' &&
          r.compliance_status === 'pass' &&
          r.confidence >= BULK_CONFIDENCE_FLOOR,
      ),
    [recs.data],
  );

  const toggle = (recId: string) => {
    const next = new Set(selected);
    if (next.has(recId)) next.delete(recId);
    else next.add(recId);
    setSelected(next);
  };

  const approveSelected = () =>
    action.run(async () => {
      const ids = [...selected];
      for (const id of ids) {
        await api.approve(id, 'Approved from the review queue.');
      }
      setSelected(new Set());
      await recs.reload();
      onChanged();
      return `${ids.length} recommendation(s) approved. Push them when ready.`;
    });

  const pushSelected = () =>
    action.run(async () => {
      const ids = [...selected];
      for (const id of ids) {
        await api.approve(id, 'Approved and pushed from the review queue.');
      }
      const result = await api.push(ids);
      setSelected(new Set());
      await recs.reload();
      onChanged();
      const blocked = result.blocked?.length
        ? ` ${result.blocked.length} blocked: ${result.blocked[0].reason}`
        : '';
      return `Pushed ${result.pushed}, rejected ${result.rejected}, skipped ${result.skipped}.${blocked}`;
    });

  const bulkApprove = () =>
    action.run(async () => {
      for (const rec of bulkEligible) {
        await api.approve(rec.rec_id, 'Bulk approved within the Review band.');
      }
      await recs.reload();
      onChanged();
      return `${bulkEligible.length} Review-band item(s) approved in bulk.`;
    });

  return (
    <div className="p-7 space-y-5">
      <div className="flex items-start">
        <SectionTitle
          eyebrow="Governance · banded autonomy"
          title="Review the exceptions, not the catalog"
          lede={`${num(counts.review + counts.escalate)} of ${num(counts.all)} pending recommendations need a human. Every band assignment states the condition that produced it.`}
        />
        <div className="ml-auto flex gap-2.5">
          <button className="btn" onClick={bulkApprove}
                  disabled={action.busy || !bulkEligible.length}>
            Bulk approve {bulkEligible.length} eligible
          </button>
          <button className="btn" onClick={approveSelected}
                  disabled={action.busy || !selected.size}>
            Approve {selected.size || ''}
          </button>
          <button className="btn-primary" onClick={pushSelected}
                  disabled={action.busy || !selected.size}>
            Approve &amp; push {selected.size || ''}
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2.5 flex-wrap">
        {(['all', 'escalate', 'review', 'auto_approve'] as const).map((key) => (
          <button
            key={key}
            onClick={() => setBand(key)}
            aria-pressed={band === key}
            className={`flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg border
                        transition-colors ${
                          band === key
                            ? 'border-accent text-accent bg-accent-wash'
                            : 'border-line text-faint hover:text-muted'
                        }`}
          >
            {key !== 'all' && <BandGlyph band={key} size={8} />}
            {key === 'all' ? 'All bands' : key.replace('_', '-')} {counts[key]}
          </button>
        ))}
        <label className="label ml-auto" htmlFor="sort">
          Sort
        </label>
        <select
          id="sort"
          className="input w-40"
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
        >
          <option value="impact">Forecast impact</option>
          <option value="confidence">Lowest confidence</option>
          <option value="delta">Largest price change</option>
          <option value="sku">SKU</option>
        </select>
      </div>

      {recs.error && <ErrorNote>{recs.error}</ErrorNote>}
      {recs.loading && !recs.data && <Spinner label="Loading queue" />}

      {rows.length ? (
        <div className="card overflow-hidden">
          <div
            className="grid grid-cols-[2rem_15rem_5.5rem_5.5rem_5rem_6.5rem_9rem_1fr_5rem]
                       gap-2.5 px-4 py-2.5 border-b border-line label"
          >
            <span />
            <span>SKU</span>
            <span>Current</span>
            <span>Proposed</span>
            <span>Delta</span>
            <span>Confidence</span>
            <span>Forecast impact</span>
            <span>Band · reason</span>
            <span />
          </div>
          {rows.slice(0, 300).map((rec) => (
            <div
              key={rec.rec_id}
              className="grid grid-cols-[2rem_15rem_5.5rem_5.5rem_5rem_6.5rem_9rem_1fr_5rem]
                         gap-2.5 px-4 py-3 border-b border-hairline last:border-0
                         items-center hover:bg-line/15"
            >
              <input
                type="checkbox"
                checked={selected.has(rec.rec_id)}
                onChange={() => toggle(rec.rec_id)}
                aria-label={`Select ${rec.sku}`}
                className="w-3.5 h-3.5 accent-current"
              />
              <div className="min-w-0">
                <div className="text-xs font-semibold text-ink truncate">
                  {rec.product_name ?? rec.sku}
                </div>
                <div className="text-tiny text-faint font-mono">{rec.sku}</div>
              </div>
              <span className="text-xs text-muted">{money(rec.current_price)}</span>
              <span className="text-xs font-semibold text-ink">
                {money(rec.recommended_price)}
              </span>
              <span className="text-xs text-muted">{signedPct(rec.delta_pct)}</span>
              <div>
                <span className="text-xs text-muted">{ratio(rec.confidence)}</span>
                <Meter
                  value={rec.confidence}
                  tone={rec.confidence >= 0.75 ? 'accent' : 'info'}
                />
              </div>
              <div>
                <span className="text-xs text-muted">
                  {moneyCompact(rec.expected_revenue_delta)}
                </span>
                <div className="text-tiny text-faint">
                  {rec.revenue_ci_low !== null
                    ? `CI ${moneyCompact(rec.revenue_ci_low)} — ${moneyCompact(rec.revenue_ci_high)}`
                    : 'no interval'}
                </div>
              </div>
              <div className="flex items-start gap-2.5 min-w-0">
                <span className="pt-1">
                  <BandGlyph band={rec.band} />
                </span>
                <div className="min-w-0">
                  <div
                    className={`text-tiny ${
                      rec.band === 'escalate'
                        ? 'text-danger'
                        : rec.band === 'review'
                          ? 'text-info'
                          : 'text-accent'
                    }`}
                  >
                    {rec.band.replace('_', '-')}
                  </div>
                  <div className="text-tiny text-faint truncate" title={rec.band_reason}>
                    {rec.band_reason}
                  </div>
                </div>
              </div>
              <button
                className="btn py-1 px-2.5 text-tiny"
                onClick={() => navigate(`/recommendation/${rec.rec_id}`)}
              >
                Open
              </button>
            </div>
          ))}
        </div>
      ) : (
        !recs.loading && (
          <Empty>
            Nothing pending in this band. Trigger a run from the console, or clear the
            band filter.
          </Empty>
        )
      )}

      <div className="grid grid-cols-3 gap-3.5">
        <Card title="Bulk approve is bounded" accent="accent">
          <p className="text-xs text-faint leading-relaxed">
            Permitted only inside the Review band, above {ratio(BULK_CONFIDENCE_FLOOR)}{' '}
            confidence, with a clean compliance verdict. {bulkEligible.length} of{' '}
            {counts.review} review items qualify; the rest need individual judgement.
          </p>
        </Card>
        <Card title="Override is re-validated">
          <p className="text-xs text-faint leading-relaxed">
            A manual price requires a written reason and passes back through the rule
            engine before acceptance. A compliance violation cannot be overridden in any
            mode.
          </p>
        </Card>
        <Card title="Automatic is not invisible">
          <p className="text-xs text-faint leading-relaxed">
            Auto-approved items stay in the audit trail with the system named as actor,
            and any push reverts to its prior price in one action.
          </p>
          <div className="mt-3">
            <BandLegend />
          </div>
        </Card>
      </div>

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}
