import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight } from 'lucide-react';

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
import { BandGlyph } from '../components/BandIndicator';
import { InfoTip } from '../components/InfoTip';
import { RULE_GUIDE, formatRuleValue, ruleTitle } from '../lib/ruleGuide';
import {
  Card,
  Empty,
  ErrorNote,
  KeyValue,
  Pill,
  SectionTitle,
  Spinner,
  Toast,
} from '../components/primitives';
import type { Recommendation } from '../lib/types';

/**
 * Recommendation detail (FR-022, FR-028, FR-096).
 *
 * Everything the decision used, and — the part that matters for trust — the
 * specific condition that decided the band, with its threshold and the actual
 * value beside it. "Escalated" alone is not an explanation a pricing manager
 * can act on or a compliance officer can audit.
 */
export function RecommendationDetail({ onChanged }: { onChanged: () => void }) {
  const { recId } = useParams<{ recId: string }>();
  const navigate = useNavigate();
  const action = useActionState();
  const [overridePrice, setOverridePrice] = useState('');
  const [reason, setReason] = useState('');

  const rec = useApi(
    () => (recId ? api.recommendation(recId) : Promise.reject(new Error('No id'))),
    [recId],
  );
  const audit = useApi(
    () => (rec.data ? api.skuAudit(rec.data.sku) : Promise.resolve(null)),
    [rec.data?.sku],
  );

  if (rec.loading) return <Spinner label="Loading recommendation" />;
  if (rec.error || !rec.data)
    return (
      <div className="p-7">
        <ErrorNote>{rec.error ?? 'Not found.'}</ErrorNote>
      </div>
    );

  const r = rec.data;
  const blocked = r.compliance_status !== 'pass';
  const citations = safeCitations(r.citations_json);

  const act = (verb: 'approve' | 'reject' | 'revert') =>
    action.run(async () => {
      const text = reason || `${verb} from the recommendation detail view.`;
      if (verb === 'approve') await api.approve(r.rec_id, text);
      if (verb === 'reject') await api.reject(r.rec_id, text);
      if (verb === 'revert') await api.revert(r.rec_id, text);
      await rec.reload();
      onChanged();
      return `${r.sku} ${verb}${verb === 'revert' ? 'ed' : verb === 'approve' ? 'd' : 'ed'}.`;
    });

  const doOverride = () =>
    action.run(async () => {
      const price = Number(overridePrice);
      if (!price || price <= 0) throw new Error('Enter a positive price.');
      if (reason.trim().length < 3)
        throw new Error('An override needs a written reason — it is a decision, not an exemption.');
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
      return `Pushed ${result.pushed}, rejected ${result.rejected}.`;
    });

  return (
    <div className="p-7 space-y-5">
      <div className="flex items-start">
        <div>
          <button
            className="flex items-center gap-1.5 text-tiny text-faint hover:text-muted mb-2"
            onClick={() => navigate('/queue')}
          >
            <ArrowLeft size={12} /> Back to queue
          </button>
          <SectionTitle
            eyebrow={`${r.sku} · ${r.category ?? 'uncategorised'} · status ${r.status}`}
            title={r.product_name ?? r.sku}
          />
        </div>
        <div className="ml-auto flex gap-2.5 items-start">
          <button className="btn" onClick={() => act('reject')} disabled={action.busy}>
            Reject
          </button>
          {r.status === 'pushed' ? (
            <button className="btn" onClick={() => act('revert')} disabled={action.busy}>
              Revert to {money(r.current_price)}
            </button>
          ) : (
            <button
              className="btn-primary"
              onClick={() => act('approve')}
              disabled={action.busy || blocked}
              title={blocked ? 'Blocked by the compliance veto — cannot be approved in any mode.' : undefined}
            >
              Approve
            </button>
          )}
          <button
            className="btn"
            onClick={pushIt}
            disabled={action.busy || blocked || !['approved', 'overridden'].includes(r.status)}
          >
            Push
          </button>
        </div>
      </div>

      {blocked && (
        <ErrorNote>
          <strong>Blocked by the compliance veto.</strong> {r.band_reason} This cannot be
          approved, overridden, or force-pushed in any operating mode.
        </ErrorNote>
      )}

      <div className="grid grid-cols-2 gap-5">
        <div className="space-y-4">
          <Card accent="accent">
            <div className="flex items-end gap-7">
              <div>
                <div className="label">Current</div>
                <div className="text-2xl font-light text-faint mt-1">
                  {money(r.current_price)}
                </div>
              </div>
              <ArrowRight size={18} className="text-faint mb-2.5" aria-hidden />
              <div>
                <div className="label">Recommended</div>
                <div className="text-4xl font-light text-accent leading-none mt-1">
                  {money(r.final_price ?? r.recommended_price)}
                </div>
              </div>
              <div className="ml-auto text-right">
                <div className="label">Delta</div>
                <div className="text-xl font-light text-ink mt-1">
                  {signedPct(r.delta_pct)}
                </div>
                {r.damped ? (
                  <div className="text-tiny text-accent mt-0.5">damped for stability</div>
                ) : null}
              </div>
            </div>

            <div className="grid grid-cols-3 gap-4 mt-5 pt-4 border-t border-hairline">
              <Figure
                label="Forecast revenue"
                value={signedMoney(r.expected_revenue_delta)}
                detail={
                  r.revenue_ci_low !== null
                    ? `CI ${moneyCompact(r.revenue_ci_low)} — ${moneyCompact(r.revenue_ci_high)}`
                    : 'no interval'
                }
              />
              <Figure
                label="Forecast margin"
                value={signedMoney(r.expected_margin_delta)}
                detail="per day at the modelled volume"
              />
              <Figure
                label="Confidence"
                value={ratio(r.confidence, 0)}
                detail={`variance ${r.variance?.toFixed(2) ?? '—'}`}
              />
            </div>
          </Card>

          <Card
            title={`Band: ${r.band.replace('_', '-')}`}
            right={<span className="label">assigned deterministically</span>}
          >
            <div className="flex items-start gap-2.5">
              <span className="pt-1">
                <BandGlyph band={r.band} size={10} />
              </span>
              <p className="text-xs text-ink leading-relaxed border-l-2 border-info pl-3">
                {r.band_reason}
              </p>
            </div>
            <KeyValue
              rows={[
                ['Compliance verdict', blocked ? 'violation' : 'clean'],
                ['Confidence', ratio(r.confidence, 0)],
                ['Price delta', signedPct(r.delta_pct)],
                ['Monte Carlo variance', r.variance?.toFixed(2) ?? '—'],
                ['P(below margin floor)', ratio(r.prob_below_margin ?? 0, 1)],
                ['Oscillation flag', r.oscillating ? 'detected' : 'none'],
                [
                  'Elasticity sample',
                  r.elasticity_samples ? `n = ${num(r.elasticity_samples)}` : 'insufficient',
                ],
              ]}
            />
          </Card>

          <Card title="Rationale">
            <p className="text-xs text-muted leading-relaxed">
              {r.rationale ?? 'No rationale recorded.'}
            </p>
            {citations.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-3.5">
                {citations.map((c) => (
                  <Pill key={c.id} tone="info">
                    {c.id}
                  </Pill>
                ))}
              </div>
            )}
            {citations.length === 0 && (
              <p className="text-tiny text-faint mt-3">
                No citations — either the gateway was unavailable and this is the
                deterministic rationale, or no grounding documents matched.
              </p>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Rule evaluation" right={<span className="label">persisted with values</span>}>
            {r.compliance_evals?.length ? (
              <div>
                {/* Without these headers the row reads "15 ... 41.83 PASS",
                    which does not say which number is the limit. */}
                <div
                  className="grid grid-cols-[0.75rem_1fr_5.5rem_6rem] gap-3
                             label pb-1.5 border-b border-line"
                >
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
                      className="grid grid-cols-[0.75rem_1fr_5.5rem_6rem] gap-3 items-center
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
                                <span className="text-faint">Limit</span> is{' '}
                                {guide.limitMeans}.
                                <br />
                                <span className="text-faint">Actual</span> is{' '}
                                {guide.actualMeans}.
                              </span>
                              <span className="block mt-2 text-faint">
                                {guide.blocking
                                  ? 'A breach blocks the price in every operating mode — it cannot be approved or overridden.'
                                  : 'Advisory only. A breach is flagged but does not block the price.'}
                              </span>
                            </>
                          ) : (
                            <>
                              A compliance rule evaluated by the deterministic rule
                              engine. No plain-language description is registered for
                              this code yet.
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
                        {evaluation.passed ? 'PASS' : 'FAIL'}
                      </span>
                    </div>
                  );
                })}
                <p className="text-tiny text-faint mt-3 leading-relaxed">
                  Every rule is recorded with the values it was evaluated against,
                  passes included — an audit months from now needs to show what was
                  checked, not only what failed.
                </p>
              </div>
            ) : (
              <Empty>No rule evaluations recorded.</Empty>
            )}
          </Card>

          <Card title="Inputs the decision used">
            <KeyValue
              rows={[
                [
                  'Elasticity',
                  r.elasticity !== null
                    ? `${r.elasticity.toFixed(2)} (CI ${r.elasticity_ci_low?.toFixed(2)} … ${r.elasticity_ci_high?.toFixed(2)}, n=${num(r.elasticity_samples)})`
                    : 'not estimable',
                ],
                ['Unit cost', money(r.unit_cost)],
                [
                  'Margin at recommended',
                  ratio((r.recommended_price - r.unit_cost) / r.recommended_price, 1),
                ],
                ['Rule-based baseline would say', money(r.baseline_price)],
                ['Mode in force at decision', 'recorded on the run'],
                ['Created', datetime(r.created_at)],
              ]}
            />
            <p className="text-tiny text-faint mt-3 leading-relaxed">
              The baseline pricer emits its number with no interval and no confidence,
              whether backed by three years of history or three noisy weeks. That is
              precisely why it can never be trusted to act unsupervised.
            </p>
          </Card>

          <Card title="Override">
            <div className="flex gap-2.5">
              <input
                className="input w-32 font-mono"
                placeholder="0.00"
                value={overridePrice}
                onChange={(e) => setOverridePrice(e.target.value)}
                aria-label="Override price"
                disabled={blocked}
              />
              <input
                className="input flex-1"
                placeholder="Reason (mandatory)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                aria-label="Reason"
              />
              <button className="btn" onClick={doOverride} disabled={action.busy || blocked}>
                Override
              </button>
            </div>
            <p className="text-tiny text-faint mt-2.5 leading-relaxed">
              The manual price is re-validated by the compliance engine before it is
              accepted. A price that breaches a rule is rejected here exactly as the
              optimizer's would be.
            </p>
          </Card>

          <Card title="Audit timeline" right={<span className="label">append-only</span>}>
            {audit.data?.audit_events?.length ? (
              <div className="space-y-2.5 max-h-72 overflow-y-auto">
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
                        {event.actor === 'system' ? 'system (automatic)' : event.actor}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty>No audit events yet for this SKU.</Empty>
            )}
          </Card>
        </div>
      </div>

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}

function Figure({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="text-base font-light text-ink mt-0.5">{value}</div>
      <div className="text-micro text-faint">{detail}</div>
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

export type { Recommendation };
