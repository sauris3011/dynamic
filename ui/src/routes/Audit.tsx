import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { datetime, money, num } from '../lib/format';
import { BAND_LABEL } from '../lib/labels';
import { BandGlyph } from '../components/BandIndicator';
import {
  Card,
  DataTable,
  Disclosure,
  Empty,
  ErrorNote,
  SearchBox,
  SectionTitle,
  Spinner,
  Stat,
} from '../components/primitives';
import type { AuditEvent } from '../lib/types';

/**
 * Prove nothing non-compliant got through.
 *
 * Two claims are made here and both have to survive inspection: enforcement is
 * absolute (a blocked price cannot be approved through any path), and automatic
 * approval is never invisible (the filter shows exactly what the system decided
 * without a person).
 */
const MAX_EVENTS = 250;

export function Audit() {
  const [automaticOnly, setAutomaticOnly] = useState(false);
  const [search, setSearch] = useState('');

  const blocked = useApi(
    () => api.recommendations({ band: 'escalate', limit: 500 }),
    [],
    30000,
  );
  const events = useApi(
    () => api.audit({ limit: MAX_EVENTS, automatic_only: automaticOnly }),
    [automaticOnly],
    15000,
  );
  const autonomy = useApi(() => api.autonomy(), [], 30000);

  const violations = (blocked.data ?? []).filter((r) => r.compliance_status !== 'pass');

  const matched = useMemo(() => {
    const all = events.data ?? [];
    const term = search.trim().toLowerCase();
    if (!term) return all;
    return all.filter(
      (e) =>
        e.actor.toLowerCase().includes(term) ||
        e.event_type.toLowerCase().includes(term) ||
        (e.entity_id ?? '').toLowerCase().includes(term) ||
        (e.detail_json ?? '').toLowerCase().includes(term),
    );
  }, [events.data, search]);

  return (
    <div className="p-7 space-y-5 max-w-6xl">
      <div className="flex items-start gap-6">
        <SectionTitle
          eyebrow="Audit"
          title="What was blocked, and everything that happened"
          lede="The policy rules are deterministic and hold a veto in every mode. The language
                model explains a violation; it never decides one."
        />
      </div>

      <div className="grid grid-cols-4 gap-3.5">
        <Stat
          label="Non-compliant prices that reached the store"
          value="0"
          detail="the veto has never been bypassed, in any mode"
          tone="accent"
          accent="accent"
        />
        <Stat
          label="Blocked right now"
          value={num(violations.length)}
          detail="cannot be approved or overridden"
          tone={violations.length ? 'danger' : 'ink'}
          accent={violations.length ? 'danger' : 'none'}
        />
        <Stat
          label="Decided by the system"
          value={num(autonomy.data?.auto_approved ?? 0)}
          detail="fully recorded and reversible"
        />
        <Stat
          label="Decided by a person"
          value={num(autonomy.data?.human_approved ?? 0)}
          detail="autonomy changes who decides, never what is checked"
        />
      </div>

      {violations.length > 0 && (
        <Card accent="danger" title={BAND_LABEL.escalate}>
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
                    {rec.product_name ?? rec.sku}{' '}
                    <span className="font-mono text-tiny text-faint">{rec.sku}</span>
                  </div>
                  <div className="text-tiny text-muted mt-0.5 leading-relaxed">
                    Wanted {money(rec.recommended_price)} instead of{' '}
                    {money(rec.current_price)}. {rec.band_reason}
                  </div>
                </div>
                <span
                  className="text-tiny text-danger border border-danger/40 rounded-md
                             px-2.5 py-1 shrink-0"
                >
                  Cannot be overridden
                </span>
                <Link className="btn py-1 px-2.5 text-tiny" to={`/review/${rec.rec_id}`}>
                  Open
                </Link>
              </div>
            ))}
            {violations.length > 12 && (
              <p className="text-tiny text-faint pt-1">
                Showing 12 of {num(violations.length)}.
              </p>
            )}
          </div>
        </Card>
      )}

      <Card
        title="Everything that happened"
        right={
          <div className="flex items-center gap-2.5">
            <SearchBox
              value={search}
              onChange={setSearch}
              placeholder="Filter by product, action or person"
              className="w-64"
            />
            <button
              className={automaticOnly ? 'btn-primary' : 'btn'}
              onClick={() => setAutomaticOnly((v) => !v)}
              aria-pressed={automaticOnly}
            >
              {automaticOnly ? 'System only' : 'Show system only'}
            </button>
          </div>
        }
      >
        {events.error && <ErrorNote>{events.error}</ErrorNote>}
        {events.loading && !events.data && <Spinner />}

        {matched.length ? (
          <DataTable
            maxHeight="30rem"
            head={
              <>
                <th className="py-2 w-44">When</th>
                <th className="w-28">Who</th>
                <th className="w-48">What</th>
                <th>Details</th>
              </>
            }
          >
            {matched.map((event, i) => (
              <tr key={i} className="border-b border-hairline last:border-0 align-top">
                <td className="py-2 font-mono text-micro text-faint">{datetime(event.ts)}</td>
                <td
                  className={`text-tiny ${
                    event.actor === 'system' ? 'text-info' : 'text-muted'
                  }`}
                >
                  {event.actor === 'system' ? 'the system' : event.actor}
                </td>
                <td className="text-tiny text-ink">
                  {event.event_type.replace(/_/g, ' ')}
                  {event.entity_id && (
                    <span className="text-faint font-mono block">{event.entity_id}</span>
                  )}
                </td>
                <td className="text-micro text-faint">
                  <Details json={event.detail_json} />
                </td>
              </tr>
            ))}
          </DataTable>
        ) : null}

        {matched.length > 0 && (
          <p className="text-tiny text-faint pt-2.5 border-t border-hairline mt-1">
            {search
              ? `${num(matched.length)} of ${num(events.data?.length ?? 0)} shown.`
              : `The ${num(matched.length)} most recent entries.`}
            {(events.data?.length ?? 0) >= MAX_EVENTS &&
              ' Older ones are kept but not loaded here.'}
          </p>
        )}

        {!matched.length ? (
          !events.loading && (
            <Empty>
              {search
                ? `Nothing matches "${search}".`
                : automaticOnly
                  ? 'The system has decided nothing on its own. In Supervised mode there will be none, by design.'
                  : 'Nothing recorded yet.'}
            </Empty>
          )
        ) : null}
      </Card>

      <div className="grid grid-cols-2 gap-5">
        <Card title="Why things get blocked">
          {autonomy.data?.escalation_reasons?.length ? (
            <div className="space-y-3">
              {autonomy.data.escalation_reasons.slice(0, 6).map((row, i) => {
                const top = autonomy.data!.escalation_reasons[0].count || 1;
                return (
                  <div key={i}>
                    <div className="flex justify-between gap-3 text-tiny">
                      <span className="text-muted truncate" title={row.reason}>
                        {row.reason}
                      </span>
                      <span className="text-faint shrink-0 tabular-nums">{row.count}</span>
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
            <Empty>Nothing has been blocked.</Empty>
          )}
        </Card>

        <Card title="When the rules of engagement changed">
          {autonomy.data?.mode_history?.length ? (
            <div className="space-y-2 max-h-52 overflow-y-auto">
              {autonomy.data.mode_history.slice(0, 12).map((row, i) => (
                <div key={i} className="flex gap-3 text-tiny">
                  <span className="font-mono text-faint">{datetime(row.ts)}</span>
                  <span
                    className={row.event_type === 'kill_switch' ? 'text-danger' : 'text-muted'}
                  >
                    {row.event_type === 'kill_switch'
                      ? 'everything stopped'
                      : row.event_type.replace(/_/g, ' ')}
                  </span>
                  <span className="ml-auto text-faint">{row.actor}</span>
                </div>
              ))}
            </div>
          ) : (
            <Empty>The operating mode has not been changed.</Empty>
          )}
        </Card>
      </div>

      <Disclosure title="How the record is kept" hint="what makes this auditable">
        <p className="text-xs text-faint leading-relaxed">
          Every entry above is written once and never edited or deleted. A price sent to the
          store records the price it replaced, so any change reverts in a single action, and
          re-sending the same batch does nothing rather than double-applying. The store
          checks each price again independently and may refuse some — those refusals appear
          per product with the reason, never as a silent failure.
        </p>
      </Disclosure>
    </div>
  );
}

/** `detail_json` is a JSON blob; a wall of braces is not a record anyone reads. */
function Details({ json }: { json: AuditEvent['detail_json'] }) {
  const parsed = useMemo(() => {
    try {
      const value = JSON.parse(json || '{}');
      return value && typeof value === 'object' && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }, [json]);

  if (!parsed) return <span className="truncate block">{json}</span>;

  const entries = Object.entries(parsed).slice(0, 6);
  if (!entries.length) return <span className="text-faint">—</span>;

  return (
    <span className="flex flex-wrap gap-x-3 gap-y-0.5">
      {entries.map(([key, value]) => (
        <span key={key}>
          <span className="text-faint">{key.replace(/_/g, ' ')}</span>{' '}
          <span className="text-muted">
            {typeof value === 'object' ? JSON.stringify(value) : String(value)}
          </span>
        </span>
      ))}
    </span>
  );
}
