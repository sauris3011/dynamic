import { useState } from 'react';
import { ArrowRight } from 'lucide-react';

import { api } from '../lib/api';
import { useActionState, useApi } from '../lib/hooks';
import {
  datetime,
  money,
  moneyCompact,
  num,
  ratio,
  signedMoney,
  signedPct,
} from '../lib/format';
import {
  BAND_LABEL,
  BAND_MEANING,
  RULE_GUIDE,
  TERMS,
  formatRuleValue,
  ruleTitle,
  statusLabel,
} from '../lib/labels';
import { BandGlyph } from './BandIndicator';
import { InfoTip } from './InfoTip';
import {
  Disclosure,
  Empty,
  ErrorNote,
  Figure,
  KeyValue,
  Pill,
  Spinner,
  Toast,
} from './primitives';

/**
 * One recommendation, and every action you can take on it.
 *
 * Reorganised from a full-page view into a panel beside the list, so deciding
 * on twenty products no longer means twenty page changes. The information is
 * unchanged; what changed is that only the decision is on screen by default.
 * Everything that justifies the decision — the rationale, the rule grid, the
 * history, the model internals — is one click away in a disclosure, present
 * when someone asks and invisible when nobody does.
 */
export function RecommendationPanel({
  recId,
  onChanged,
}: {
  recId: string;
  onChanged: () => void;
}) {
  const action = useActionState();
  const [overridePrice, setOverridePrice] = useState('');
  const [reason, setReason] = useState('');

  const rec = useApi(() => api.recommendation(recId), [recId]);
  const audit = useApi(
    () => (rec.data ? api.skuAudit(rec.data.sku) : Promise.resolve(null)),
    [rec.data?.sku],
  );

  if (rec.loading && !rec.data) return <Spinner label="Loading" />;
  if (rec.error || !rec.data)
    return (
      <div className="p-4">
        <ErrorNote>{rec.error ?? 'Not found.'}</ErrorNote>
      </div>
    );

  const r = rec.data;
  const effectivePrice = r.final_price ?? r.recommended_price;
  const effectiveDeltaPct =
    r.current_price === 0 ? 0 : ((effectivePrice - r.current_price) / r.current_price) * 100;
  const blocked = r.compliance_status !== 'pass';
  const citations = safeCitations(r.citations_json);
  const pushable = !blocked && ['approved', 'overridden'].includes(r.status);

  const act = (verb: 'approve' | 'reject' | 'revert') =>
    action.run(async () => {
      const text = reason || `${verb} from the review screen.`;
      if (verb === 'approve') await api.approve(r.rec_id, text);
      if (verb === 'reject') await api.reject(r.rec_id, text);
      if (verb === 'revert') await api.revert(r.rec_id, text);
      await rec.reload();
      onChanged();
      const past = { approve: 'approved', reject: 'rejected', revert: 'reverted' }[verb];
      return `${r.sku} ${past}.`;
    });

  const doOverride = () =>
    action.run(async () => {
      const price = Number(overridePrice);
      if (!price || price <= 0) throw new Error('Enter a positive price.');
      if (reason.trim().length < 3)
        throw new Error(
          'An override needs a written reason — it is a decision, not an exemption.',
        );
      await api.override(r.rec_id, price, reason);
      setOverridePrice('');
      await rec.reload();
      onChanged();
      return `Override to ${money(price)} accepted — it passed re-validation.`;
    });

  const pushIt = () =>
    action.run(async () => {
      const result = await api.push([r.rec_id]);
      await rec.reload();
      onChanged();
      if (result.blocked?.length) return `Blocked: ${result.blocked[0].reason}`;
      return `Sent ${result.pushed} to the store, rejected ${result.rejected}.`;
    });

  return (
    <div className="p-5 space-y-4">
      <div>
        <div className="text-sm font-bold text-ink">{r.product_name ?? r.sku}</div>
        <div className="text-tiny text-faint font-mono mt-0.5">
          {r.sku} · {r.category ?? 'uncategorised'} · {statusLabel(r.status)}
        </div>
      </div>

      {blocked && (
        <ErrorNote>
          <strong>{BAND_LABEL.escalate}.</strong> {r.band_reason} This cannot be approved,
          overridden, or forced through in any mode.
        </ErrorNote>
      )}

      {/* --- The decision, always visible --- */}
      <div className="card p-4 border-l-2 border-l-accent">
        <div className="flex items-end gap-5">
          <div>
            <div className="label">Now</div>
            <div className="text-xl font-light text-faint mt-1">{money(r.current_price)}</div>
          </div>
          <ArrowRight size={16} className="text-faint mb-2" aria-hidden />
          <div>
            <div className="label">Proposed</div>
            <div className="text-3xl font-light text-accent leading-none mt-1">
              {money(effectivePrice)}
            </div>
          </div>
          <div className="ml-auto text-right">
            <div className="label">Change</div>
            <div className="text-lg font-light text-ink mt-1">{signedPct(effectiveDeltaPct)}</div>
            {r.damped ? (
              <div className="text-tiny text-accent mt-0.5">held back for stability</div>
            ) : null}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 mt-4 pt-3.5 border-t border-hairline">
          <Figure
            label="Forecast revenue"
            value={signedMoney(r.expected_revenue_delta)}
            detail={
              r.revenue_ci_low !== null
                ? `likely between ${moneyCompact(r.revenue_ci_low)} and ${moneyCompact(
                    r.revenue_ci_high,
                  )}`
                : 'no range available'
            }
          />
          <Figure
            label="Forecast margin"
            value={signedMoney(r.expected_margin_delta)}
            detail="per day at the modelled volume"
          />
        </div>
      </div>

      <div className="flex items-start gap-2.5">
        <span className="pt-1">
          <BandGlyph band={r.band} size={10} />
        </span>
        <div className="min-w-0">
          <div className="text-xs font-semibold text-ink">{BAND_LABEL[r.band]}</div>
          <p className="text-tiny text-faint leading-snug mt-0.5">{r.band_reason}</p>
        </div>
      </div>

      {r.status === 'overridden' && (
        <div className="text-tiny text-accent">
          Manual price recorded. It will be sent if you choose to send it to the store.
        </div>
      )}

      {/* --- Actions --- */}
      <div className="space-y-2.5">
        <input
          className="input w-full"
          placeholder="Reason (required for an override, recorded for everything else)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          aria-label="Reason"
        />

        <div className="flex gap-2">
          <button className="btn flex-1" onClick={() => act('reject')} disabled={action.busy}>
            Reject
          </button>
          {r.status === 'pushed' ? (
            <button
              className="btn flex-1"
              onClick={() => act('revert')}
              disabled={action.busy}
            >
              Revert to {money(r.current_price)}
            </button>
          ) : r.status === 'overridden' ? (
            <div className="btn flex-1 text-center cursor-default text-accent" aria-live="polite">
              Manual price saved
            </div>
          ) : (
            <button
              className="btn-primary flex-1"
              onClick={() => act('approve')}
              disabled={action.busy || blocked}
              title={blocked ? 'A policy rule blocks this in every mode.' : undefined}
            >
              Approve
            </button>
          )}
          <button
            className="btn flex-1"
            onClick={pushIt}
            disabled={action.busy || !pushable}
            title={
              pushable
                ? undefined
                : 'Approve or override this first — only a decided price can go to the store.'
            }
          >
            {TERMS.push}
          </button>
        </div>

        <div className="flex gap-2">
          <input
            className="input w-28 font-mono"
            placeholder="0.00"
            value={overridePrice}
            onChange={(e) => setOverridePrice(e.target.value)}
            aria-label="Override price"
            disabled={blocked}
          />
          <button
            className="btn flex-1"
            onClick={doOverride}
            disabled={action.busy || blocked}
          >
            Use my price instead
          </button>
        </div>
        <p className="text-micro text-faint leading-snug">
          Your price goes back through the same policy checks. One that breaks a rule is
          refused here exactly as the system's would be.
        </p>
      </div>

      {/* --- Everything that justifies it, collapsed --- */}
      <Disclosure
        title="Why this price"
        hint={
          r.narrated
            ? citations.length
              ? `written · ${citations.length} source${citations.length === 1 ? '' : 's'}`
              : 'written'
            : 'computed'
        }
      >
        {/* Which kind of explanation this is, stated before the prose rather
            than inferred from it. Only the highest-impact products of a run get
            a model-written rationale; the rest get a computed one built from the
            same figures. A reader cannot judge an explanation without knowing
            which they are reading. */}
        <div className="flex items-center gap-2 mb-2">
          <Pill tone={r.narrated ? 'info' : 'muted'}>
            {r.narrated ? 'Written explanation' : 'Computed explanation'}
          </Pill>
          {r.narrated && citations.length > 0 && (
            <span className="text-micro text-faint">
              drawn from {citations.length} reference document
              {citations.length === 1 ? '' : 's'}
            </span>
          )}
        </div>

        <p className="text-xs text-muted leading-relaxed">
          {r.rationale ?? 'No explanation was recorded for this one.'}
        </p>

        {citations.length > 0 ? (
          <div className="mt-3">
            <div className="label mb-1.5">Sources</div>
            <div className="flex flex-wrap gap-2">
              {citations.map((c) => (
                <Pill key={c.id} tone="info">
                  {c.source ? `${c.id} · ${c.source}` : c.id}
                </Pill>
              ))}
            </div>
            <p className="text-micro text-faint mt-2 leading-snug">
              Documents from your reference collections. Upload or review them under
              Diagnostics.
            </p>
          </div>
        ) : r.narrated ? (
          <p className="text-tiny text-faint mt-2.5">
            No sources cited — no reference document matched this product closely enough,
            so the explanation rests on the computed figures alone.
          </p>
        ) : (
          <p className="text-tiny text-faint mt-2.5">
            Built from the figures below rather than written up. Only the highest-impact
            products of each run get a written explanation.
          </p>
        )}

        <div className="mt-4">
          <div className="label mb-1.5">Policy checks</div>
          {r.compliance_evals?.length ? (
            <div>
              {/* Without these headers the row reads "15 … 41.83 PASS", which
                  does not say which number is the limit. */}
              <div className="grid grid-cols-[0.75rem_1fr_5rem_5.5rem] gap-2.5 label pb-1.5 border-b border-line">
                <span />
                <span>Rule</span>
                <span className="text-right">Limit</span>
                <span className="text-right">Actual</span>
              </div>
              {r.compliance_evals.map((evaluation) => {
                const guide = RULE_GUIDE[evaluation.rule_code];
                return (
                  <div
                    key={evaluation.rule_code}
                    className="grid grid-cols-[0.75rem_1fr_5rem_5.5rem] gap-2.5 items-center
                               py-2 border-b border-hairline last:border-0"
                  >
                    <span
                      className={`w-2 h-2 ${
                        evaluation.passed ? 'rounded-full bg-info' : 'rotate-45 bg-danger'
                      }`}
                      aria-hidden
                    />
                    <span className="flex items-center gap-1.5 min-w-0">
                      <span className="text-xs text-muted truncate">
                        {ruleTitle(evaluation.rule_code)}
                      </span>
                      <InfoTip label={evaluation.rule_code}>
                        {guide ? (
                          <>
                            <span className="block text-ink font-semibold mb-1">
                              {guide.title}
                            </span>
                            {guide.what}
                            <span className="block mt-2 pt-2 border-t border-hairline">
                              <span className="text-faint">Limit</span> is {guide.limitMeans}.
                              <br />
                              <span className="text-faint">Actual</span> is {guide.actualMeans}.
                            </span>
                            <span className="block mt-2 text-faint">
                              {guide.blocking
                                ? 'A breach blocks the price in every mode — it cannot be approved or overridden.'
                                : 'Advisory only. A breach is flagged but does not block the price.'}
                            </span>
                          </>
                        ) : (
                          <>
                            A policy rule evaluated by the deterministic rule engine. No
                            plain-language description is registered for this code yet.
                          </>
                        )}
                      </InfoTip>
                    </span>
                    <span className="text-tiny text-faint text-right">
                      {formatRuleValue(evaluation.threshold, guide?.unit)}
                    </span>
                    <span
                      className={`text-tiny text-right ${
                        evaluation.passed ? 'text-muted' : 'text-danger'
                      }`}
                    >
                      {formatRuleValue(evaluation.actual_value, guide?.unit)}{' '}
                      {evaluation.passed ? 'pass' : 'FAIL'}
                    </span>
                  </div>
                );
              })}
              <p className="text-tiny text-faint mt-2.5 leading-relaxed">
                Every rule is recorded with the values it was checked against, passes
                included — an audit months from now needs to show what was checked, not
                only what failed.
              </p>
            </div>
          ) : (
            <Empty>No policy checks recorded.</Empty>
          )}
        </div>

        <div className="mt-4">
          <div className="label mb-1">{TERMS.baselineTitle}</div>
          <p className="text-xs text-muted">
            would have said <strong className="text-ink">{money(r.baseline_price)}</strong> —
            with no range and no confidence attached, whether it was backed by three years
            of history or three noisy weeks.
          </p>
        </div>
      </Disclosure>

      <Disclosure title="What went into it" hint="cost, elasticity, confidence">
        <KeyValue
          rows={[
            ['Confidence', ratio(r.confidence, 0)],
            [
              'Elasticity',
              r.elasticity !== null
                ? `${r.elasticity.toFixed(2)} (range ${r.elasticity_ci_low?.toFixed(
                    2,
                  )} … ${r.elasticity_ci_high?.toFixed(2)}, from ${num(
                    r.elasticity_samples,
                  )} observations)`
                : 'could not be estimated',
            ],
            ['Unit cost', money(r.unit_cost)],
            [
              'Margin at the proposed price',
              ratio((r.recommended_price - r.unit_cost) / r.recommended_price, 1),
            ],
            ['Spread of simulated outcomes', r.variance?.toFixed(2) ?? '—'],
            ['Chance of falling below the margin floor', ratio(r.prob_below_margin ?? 0, 1)],
            ['Price bouncing back and forth', r.oscillating ? 'detected' : 'no'],
            ['Proposed', datetime(r.created_at)],
          ]}
        />
        <p className="text-tiny text-faint mt-2.5 leading-snug">{BAND_MEANING[r.band]}</p>
      </Disclosure>

      <Disclosure title="History" hint="every action taken on this product">
        {audit.data?.audit_events?.length ? (
          <div className="space-y-2 max-h-72 overflow-y-auto">
            {audit.data.audit_events.slice(0, 20).map((event, i) => (
              <div key={i} className="flex gap-3 items-start">
                <span className="font-mono text-micro text-faint w-32 shrink-0">
                  {datetime(event.ts)}
                </span>
                <div className="min-w-0">
                  <div className="text-tiny text-ink">
                    {event.event_type.replace(/_/g, ' ')}
                  </div>
                  <div className="text-micro text-faint">
                    {event.actor === 'system' ? 'the system, automatically' : event.actor}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty>Nothing has happened to this product yet.</Empty>
        )}
      </Disclosure>

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}

function safeCitations(json: string): { id: string; source?: string }[] {
  try {
    const parsed = JSON.parse(json ?? '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
