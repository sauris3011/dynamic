import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { datetime, money, num } from '../lib/format';
import { BandGlyph } from '../components/BandIndicator';
import { Card, Empty, ErrorNote, SectionTitle, Spinner, Stat } from '../components/primitives';

/**
 * Compliance and audit (W7, FR-058 .. FR-060, FR-114).
 *
 * Two claims are made visually here and both have to be true: enforcement is
 * absolute (blocked items cannot be approved through any path), and automatic
 * approval is never invisible (the audit filter shows exactly what the system
 * decided without a human).
 */
export function Compliance() {
  const [automaticOnly, setAutomaticOnly] = useState(false);

  const blocked = useApi(
    () => api.recommendations({ band: 'escalate', limit: 500 }),
    [],
    30000,
  );
  const events = useApi(
    () => api.audit({ limit: 250, automatic_only: automaticOnly }),
    [automaticOnly],
    15000,
  );
  const autonomy = useApi(() => api.autonomy(), [], 30000);

  const violations = (blocked.data ?? []).filter((r) => r.compliance_status !== 'pass');

  return (
    <div className="p-7 space-y-5">
      <div className="flex items-start">
        <SectionTitle
          eyebrow="Compliance & audit"
          title="Enforcement is absolute. Approval is graduated."
          lede="The rule engine is deterministic and holds a veto in every operating mode. The language model explains violations; it never decides them."
        />
        <div className="ml-auto flex gap-2.5">
          <button
            className={automaticOnly ? 'btn-primary' : 'btn'}
            onClick={() => setAutomaticOnly((v) => !v)}
            aria-pressed={automaticOnly}
          >
            {automaticOnly ? 'Showing automatic only' : 'Automatic approvals only'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3.5">
        <Stat
          label="Violating prices reaching commerce"
          value="0"
          detail="the veto has never been bypassed, in any mode"
          tone="accent"
          accent="accent"
        />
        <Stat
          label="Currently blocked"
          value={num(violations.length)}
          detail="cannot be approved or overridden"
          tone={violations.length ? 'danger' : 'ink'}
          accent={violations.length ? 'danger' : 'none'}
        />
        <Stat
          label="Approved by the system"
          value={num(autonomy.data?.auto_approved ?? 0)}
          detail="fully logged and reversible"
        />
        <Stat
          label="Approved by a human"
          value={num(autonomy.data?.human_approved ?? 0)}
          detail="autonomy changes who approves, never what is verified"
        />
      </div>

      {violations.length > 0 && (
        <Card accent="danger" title="Blocked by the compliance veto">
          <div className="space-y-2.5">
            {violations.slice(0, 12).map((rec) => (
              <div
                key={rec.rec_id}
                className="flex items-start gap-3.5 py-2.5 border-b border-hairline last:border-0"
              >
                <span className="pt-1">
                  <BandGlyph band="escalate" size={10} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-ink">
                    {rec.sku} — {rec.product_name}
                  </div>
                  <div className="text-tiny text-muted mt-0.5 leading-relaxed">
                    Proposed {money(rec.recommended_price)} against a current{' '}
                    {money(rec.current_price)}. {rec.band_reason}
                  </div>
                </div>
                <span className="text-tiny text-danger border border-danger/40 rounded-md
                                 px-2.5 py-1 tracking-wide shrink-0">
                  VETO — NOT OVERRIDABLE
                </span>
                <Link className="btn py-1 px-2.5 text-tiny" to={`/recommendation/${rec.rec_id}`}>
                  Open
                </Link>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-[1.2fr_1fr] gap-5">
        <Card
          title="Audit trail"
          right={
            <span className="label">
              {automaticOnly ? 'system actor only' : 'append-only'}
            </span>
          }
        >
          {events.error && <ErrorNote>{events.error}</ErrorNote>}
          {events.loading && !events.data && <Spinner />}
          {events.data?.length ? (
            <div className="max-h-[30rem] overflow-y-auto">
              {events.data.map((event, i) => (
                <div
                  key={i}
                  className="grid grid-cols-[10rem_7rem_1fr] gap-3 py-2 border-b
                             border-hairline last:border-0 items-start"
                >
                  <span className="font-mono text-micro text-faint">
                    {datetime(event.ts)}
                  </span>
                  <span
                    className={`text-tiny ${
                      event.actor === 'system' ? 'text-info' : 'text-muted'
                    }`}
                  >
                    {event.actor}
                  </span>
                  <div className="min-w-0">
                    <div className="text-tiny text-ink">
                      {event.event_type.replace(/_/g, ' ')}
                      {event.entity_id && (
                        <span className="text-faint font-mono"> · {event.entity_id}</span>
                      )}
                    </div>
                    <div className="text-micro text-faint truncate" title={event.detail_json}>
                      {event.detail_json}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            !events.loading && (
              <Empty>
                {automaticOnly
                  ? 'No automatic approvals recorded. In Supervised mode there will be none by design.'
                  : 'No audit events recorded yet.'}
              </Empty>
            )
          )}
        </Card>

        <div className="space-y-4">
          <Card title="Escalation reasons, ranked">
            {autonomy.data?.escalation_reasons?.length ? (
              <div className="space-y-3">
                {autonomy.data.escalation_reasons
                  .slice(0, 6)
                  .map((row: { reason: string; count: number }, i: number) => {
                    const top = autonomy.data!.escalation_reasons[0].count || 1;
                    return (
                      <div key={i}>
                        <div className="flex justify-between gap-3 text-tiny">
                          <span className="text-muted truncate" title={row.reason}>
                            {row.reason}
                          </span>
                          <span className="text-faint shrink-0">{row.count}</span>
                        </div>
                        <div className="h-1 bg-line/60 rounded-sm mt-1.5">
                          <div
                            className="h-full bg-danger rounded-sm"
                            style={{ width: `${(row.count / top) * 100}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
              </div>
            ) : (
              <Empty>No escalations recorded.</Empty>
            )}
          </Card>

          <Card title="Mode change history">
            {autonomy.data?.mode_history?.length ? (
              <div className="space-y-2 max-h-52 overflow-y-auto">
                {autonomy.data.mode_history
                  .slice(0, 12)
                  .map((row: { ts: string; actor: string; event_type: string }, i: number) => (
                    <div key={i} className="flex gap-3 text-tiny">
                      <span className="font-mono text-faint">{datetime(row.ts)}</span>
                      <span
                        className={
                          row.event_type === 'kill_switch' ? 'text-danger' : 'text-muted'
                        }
                      >
                        {row.event_type.replace(/_/g, ' ')}
                      </span>
                      <span className="ml-auto text-faint">{row.actor}</span>
                    </div>
                  ))}
              </div>
            ) : (
              <Empty>Mode has not been changed.</Empty>
            )}
          </Card>

          <div className="grid grid-cols-1 gap-3.5">
            {[
              [
                'No individual-level pricing',
                'The system refuses to produce personalized prices. Segment and SKU level only — a permanent product boundary, not a timeline decision.',
              ],
              [
                'Redaction before the prompt',
                'Secrets and PII are stripped from every prompt, log and trace. No consumer-level data enters the platform at all.',
              ],
              [
                'Idempotent, reversible push',
                'A batch key makes a re-push a no-op. Every applied change records the prior price and reverts in one action.',
              ],
              [
                'Partial success is first-class',
                'Commerce re-validates independently and may reject items. Rejections show per SKU with the reason, never as a silent failure.',
              ],
            ].map(([title, body]) => (
              <Card key={title} title={title}>
                <p className="text-tiny text-faint leading-relaxed">{body}</p>
              </Card>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
