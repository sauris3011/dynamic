import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { api } from '../lib/api';
import { useActionState, useApi } from '../lib/hooks';
import { money, moneyCompact, num, ratio, signedPct } from '../lib/format';
import { BAND_LABEL, BAND_SHORT, BANDS } from '../lib/labels';
import { BandGlyph } from '../components/BandIndicator';
import { RecommendationPanel } from '../components/RecommendationPanel';
import {
  DataTable,
  Empty,
  ErrorNote,
  SearchBox,
  SectionTitle,
  Spinner,
  Toast,
} from '../components/primitives';
import type { Band, Recommendation } from '../lib/types';

/**
 * Decide on each recommendation.
 *
 * List on the left, the selected record on the right. Previously these were two
 * routes, so working through a queue of twenty meant forty navigations; the
 * decision surface now stays put while the selection moves.
 *
 * Bulk approve is deliberately bounded: Review band only, above the confidence
 * floor, clean policy checks. A bulk control that could sweep blocked items
 * would turn the whole band model into decoration.
 */

const BULK_CONFIDENCE_FLOOR = 0.75;
const MAX_ROWS = 300;

/** Approvals go out in chunks rather than one-at-a-time-awaited. */
const CHUNK = 10;

type SortKey = 'impact' | 'confidence' | 'delta' | 'sku';

export function Review({ onChanged }: { onChanged: () => void }) {
  const { recId } = useParams<{ recId: string }>();
  const navigate = useNavigate();
  const action = useActionState();

  const [band, setBand] = useState<Band | 'all'>('all');
  const [sort, setSort] = useState<SortKey>('impact');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const recs = useApi(
    () => api.recommendations({ status: 'pending', limit: 3000 }),
    [],
    20000,
  );

  const counts = useMemo(() => {
    const all = recs.data ?? [];
    return {
      all: all.length,
      auto_approve: all.filter((r) => r.band === 'auto_approve').length,
      review: all.filter((r) => r.band === 'review').length,
      escalate: all.filter((r) => r.band === 'escalate').length,
    };
  }, [recs.data]);

  const matched = useMemo(() => {
    const all = recs.data ?? [];
    const term = search.trim().toLowerCase();

    const filtered = all.filter((r) => {
      if (band !== 'all' && r.band !== band) return false;
      if (!term) return true;
      return (
        r.sku.toLowerCase().includes(term) ||
        (r.product_name ?? '').toLowerCase().includes(term) ||
        (r.category ?? '').toLowerCase().includes(term)
      );
    });

    const sorters: Record<SortKey, (a: Recommendation, b: Recommendation) => number> = {
      impact: (a, b) =>
        Math.abs(b.expected_revenue_delta ?? 0) - Math.abs(a.expected_revenue_delta ?? 0),
      confidence: (a, b) => a.confidence - b.confidence,
      delta: (a, b) => Math.abs(b.delta_pct) - Math.abs(a.delta_pct),
      sku: (a, b) => a.sku.localeCompare(b.sku),
    };
    return [...filtered].sort(sorters[sort]);
  }, [recs.data, band, sort, search]);

  const rows = matched.slice(0, MAX_ROWS);

  // Eligibility is computed, not assumed, and shown before the click.
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

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const approveMany = async (ids: string[], why: string) => {
    // Chunked rather than one serialized round trip per row: a 200-item bulk
    // approve used to be 200 sequential requests. `allSettled` means one
    // failure does not abandon the rest, and the count is reported honestly.
    let failed = 0;
    for (let i = 0; i < ids.length; i += CHUNK) {
      const results = await Promise.allSettled(
        ids.slice(i, i + CHUNK).map((id) => api.approve(id, why)),
      );
      failed += results.filter((r) => r.status === 'rejected').length;
    }
    return failed;
  };

  const approveSelected = () =>
    action.run(async () => {
      const ids = [...selected];
      const failed = await approveMany(ids, 'Approved from the review screen.');
      setSelected(new Set());
      await recs.reload();
      onChanged();
      return failed
        ? `${ids.length - failed} approved, ${failed} failed.`
        : `${ids.length} approved. Send them to the store when ready.`;
    });

  const pushSelected = () =>
    action.run(async () => {
      const ids = [...selected];
      const failed = await approveMany(ids, 'Approved and sent from the review screen.');
      const result = await api.push(ids);
      setSelected(new Set());
      await recs.reload();
      onChanged();
      const blocked = result.blocked?.length
        ? ` ${result.blocked.length} blocked: ${result.blocked[0].reason}`
        : '';
      const failures = failed ? ` ${failed} could not be approved.` : '';
      return `Sent ${result.pushed} to the store, rejected ${result.rejected}, skipped ${result.skipped}.${blocked}${failures}`;
    });

  const bulkApprove = () =>
    action.run(async () => {
      const ids = bulkEligible.map((r) => r.rec_id);
      const failed = await approveMany(ids, 'Bulk approved within the review band.');
      await recs.reload();
      onChanged();
      return failed
        ? `${ids.length - failed} approved, ${failed} failed.`
        : `${ids.length} approved in bulk.`;
    });

  const select = (id: string) => navigate(`/review/${id}`);

  return (
    <div className="h-full flex flex-col">
      <div className="px-7 pt-6 pb-4 space-y-4 shrink-0">
        <div className="flex items-start gap-6">
          <SectionTitle
            eyebrow="Review"
            title="Decide on each recommendation"
            lede={`${num(counts.review + counts.escalate)} of ${num(
              counts.all,
            )} proposed prices need a person. Pick one to see the price, the reasoning and the actions.`}
          />
          <div className="ml-auto flex gap-2.5 shrink-0">
            <button
              className="btn"
              onClick={bulkApprove}
              disabled={action.busy || !bulkEligible.length}
              title={`Only ${BAND_LABEL.review.toLowerCase()} items above ${ratio(
                BULK_CONFIDENCE_FLOOR,
              )} confidence with clean policy checks.`}
            >
              Approve {bulkEligible.length} safe ones
            </button>
            <button
              className="btn"
              onClick={approveSelected}
              disabled={action.busy || !selected.size}
            >
              Approve {selected.size || ''}
            </button>
            <button
              className="btn-primary"
              onClick={pushSelected}
              disabled={action.busy || !selected.size}
            >
              Approve &amp; send {selected.size || ''}
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2.5 flex-wrap">
          <SearchBox
            value={search}
            onChange={setSearch}
            placeholder="Search by product or code"
            className="w-72"
          />

          <button
            onClick={() => setBand('all')}
            aria-pressed={band === 'all'}
            className={chipClass(band === 'all')}
          >
            Everything {counts.all}
          </button>
          {BANDS.map((key) => (
            <button
              key={key}
              onClick={() => setBand(key)}
              aria-pressed={band === key}
              className={chipClass(band === key)}
            >
              <BandGlyph band={key} size={8} />
              {BAND_SHORT[key]} {counts[key]}
            </button>
          ))}

          <label className="label ml-auto" htmlFor="sort">
            Sort by
          </label>
          <select
            id="sort"
            className="input w-44"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
          >
            <option value="impact">Biggest impact</option>
            <option value="confidence">Least confident</option>
            <option value="delta">Biggest price change</option>
            <option value="sku">Product code</option>
          </select>
        </div>

        {recs.error && <ErrorNote>{recs.error}</ErrorNote>}
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-[1fr_28rem] gap-5 px-7 pb-7">
        <div className="card overflow-hidden flex flex-col min-h-0">
          {recs.loading && !recs.data ? (
            <div className="p-4">
              <Spinner label="Loading" />
            </div>
          ) : rows.length ? (
            <div className="flex-1 min-h-0 p-4 flex flex-col">
              <DataTable
                shown={rows.length}
                total={matched.length}
                narrowHint="search or filter to see the rest"
                head={
                  <>
                    <th className="w-8 py-2" />
                    <th className="py-2">Product</th>
                    <th className="text-right">Now</th>
                    <th className="text-right">Proposed</th>
                    <th className="text-right">Change</th>
                    <th className="text-right">Forecast impact</th>
                    <th className="pl-3">Decision</th>
                  </>
                }
              >
                {rows.map((rec) => {
                  const active = rec.rec_id === recId;
                  return (
                    <tr
                      key={rec.rec_id}
                      onClick={() => select(rec.rec_id)}
                      className={`border-b border-hairline last:border-0 cursor-pointer
                                  ${active ? 'bg-info-wash' : 'hover:bg-line/15'}`}
                    >
                      <td className="py-2.5" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selected.has(rec.rec_id)}
                          onChange={() => toggle(rec.rec_id)}
                          aria-label={`Select ${rec.product_name ?? rec.sku}`}
                          className="w-3.5 h-3.5 accent-current"
                        />
                      </td>
                      <td className="py-2.5 min-w-0 max-w-0">
                        <div className="text-xs font-semibold text-ink truncate">
                          {rec.product_name ?? rec.sku}
                        </div>
                        <div className="text-tiny text-faint font-mono">{rec.sku}</div>
                      </td>
                      <td className="text-right text-muted tabular-nums">
                        {money(rec.current_price)}
                      </td>
                      <td className="text-right font-semibold text-ink tabular-nums">
                        {money(rec.recommended_price)}
                      </td>
                      <td className="text-right text-muted tabular-nums">
                        {signedPct(rec.delta_pct)}
                      </td>
                      <td className="text-right text-muted tabular-nums">
                        {moneyCompact(rec.expected_revenue_delta)}
                      </td>
                      <td className="pl-3">
                        <span className="flex items-center gap-2">
                          <BandGlyph band={rec.band} />
                          <span className="text-tiny text-muted">
                            {BAND_SHORT[rec.band]}
                          </span>
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </DataTable>
            </div>
          ) : (
            <div className="p-4">
              <Empty>
                {search
                  ? `Nothing matches "${search}".`
                  : 'Nothing waiting here. Run a batch, or clear the filter.'}
              </Empty>
            </div>
          )}
        </div>

        <div className="card overflow-y-auto min-h-0">
          {recId ? (
            <RecommendationPanel
              key={recId}
              recId={recId}
              onChanged={() => {
                void recs.reload();
                onChanged();
              }}
            />
          ) : (
            <div className="p-5">
              <Empty>Pick a product on the left to see its price and decide on it.</Empty>
            </div>
          )}
        </div>
      </div>

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}

const chipClass = (active: boolean) =>
  `flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg border transition-colors ${
    active
      ? 'border-accent text-accent bg-accent-wash'
      : 'border-line text-faint hover:text-muted'
  }`;
