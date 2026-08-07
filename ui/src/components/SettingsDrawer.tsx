import { useEffect, useState } from 'react';
import { X } from 'lucide-react';

import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { num, ratio } from '../lib/format';
import { Toast } from './primitives';
import { useActionState } from '../lib/hooks';
import type { PlatformConfig } from '../lib/types';

/**
 * Settings drawer (FR-066, FR-067). Everything here applies at runtime; nothing
 * requires a restart.
 *
 * The API key field is write-only and never round-trips. The backend holds it
 * in process memory and never writes it to disk (NFR-011), so it does not
 * survive a restart — that is deliberate, and the drawer says so rather than
 * letting the operator discover it.
 */

export function SettingsDrawer({
  open,
  config,
  onClose,
  onChanged,
}: {
  open: boolean;
  config: PlatformConfig | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const cache = useApi(() => api.cacheStats(), [open], open ? 5000 : undefined);
  const telemetry = useApi(() => api.telemetryStatus(), [open]);
  const action = useActionState();

  const [gatewayUrl, setGatewayUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [insecure, setInsecure] = useState(false);
  const [thresholds, setThresholds] = useState({
    min_confidence: 0.75,
    max_delta_pct: 10,
    max_variance: 0.25,
    margin_buffer_pct: 3,
  });

  useEffect(() => {
    if (!config) return;
    setGatewayUrl(config.gateway_url);
    setInsecure(!config.tls.secure);
    setThresholds(config.thresholds);
  }, [config]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const saveGateway = () =>
    action.run(async () => {
      await api.setGateway({
        gateway_url: gatewayUrl,
        api_key: apiKey || undefined,
        allow_insecure_tls: insecure,
      });
      setApiKey('');
      onChanged();
      return 'Gateway settings applied. No restart needed.';
    });

  const saveThresholds = () =>
    action.run(async () => {
      await api.setThresholds(thresholds);
      onChanged();
      return 'Band thresholds updated. They apply from the next run.';
    });

  return (
    <>
      <div className="fixed inset-0 z-40 bg-canvas/70" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        className="fixed right-0 top-0 bottom-0 z-40 w-[26rem] bg-surface border-l
                   border-line overflow-y-auto"
      >
        <header className="flex items-center gap-3 px-5 h-14 border-b border-hairline">
          <h2 className="text-sm font-bold text-ink">Settings</h2>
          <button
            onClick={onClose}
            className="ml-auto text-faint hover:text-ink"
            aria-label="Close settings"
          >
            <X size={16} />
          </button>
        </header>

        <div className="p-5 space-y-7">
          <section>
            <h3 className="label mb-3">LLM gateway</h3>
            <label className="block text-tiny text-faint mb-1" htmlFor="gw-url">
              Gateway URL
            </label>
            <input
              id="gw-url"
              className="input font-mono"
              value={gatewayUrl}
              onChange={(e) => setGatewayUrl(e.target.value)}
              placeholder="http://localhost:4000"
            />

            <label className="block text-tiny text-faint mt-3 mb-1" htmlFor="gw-key">
              API key {config?.api_key_set && <span className="text-info">(set)</span>}
            </label>
            <input
              id="gw-key"
              type="password"
              className="input font-mono"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Leave blank to keep the current key"
              autoComplete="off"
            />
            <p className="text-micro text-faint mt-1.5 leading-snug">
              Held in process memory only, never written to disk, never sent to the
              browser. It does not survive a restart.
            </p>

            <Toggle
              label="Disable SSL verification"
              checked={insecure}
              onChange={setInsecure}
              warning={
                insecure
                  ? 'Man-in-the-middle protection is off. Prefer CA_BUNDLE_PATH pointing at the corporate root CA.'
                  : undefined
              }
            />

            <button className="btn-primary mt-4 w-full" onClick={saveGateway}
                    disabled={action.busy}>
              Apply gateway settings
            </button>
          </section>

          <section>
            <h3 className="label mb-3">Autonomy band thresholds</h3>
            {(
              [
                ['min_confidence', 'Minimum confidence', 0, 1, 0.01],
                ['max_delta_pct', 'Maximum price change %', 0.5, 50, 0.5],
                ['max_variance', 'Maximum variance', 0.05, 2, 0.01],
                ['margin_buffer_pct', 'Margin proximity buffer (pts)', 0, 20, 0.5],
              ] as const
            ).map(([key, label, min, max, step]) => (
              <div key={key} className="flex items-center gap-3 py-1.5">
                <label className="text-xs text-muted flex-1" htmlFor={`th-${key}`}>
                  {label}
                </label>
                <input
                  id={`th-${key}`}
                  type="number"
                  min={min}
                  max={max}
                  step={step}
                  value={thresholds[key]}
                  onChange={(e) =>
                    setThresholds({ ...thresholds, [key]: Number(e.target.value) })
                  }
                  className="input w-24 text-right font-mono"
                />
              </div>
            ))}
            <button className="btn mt-3 w-full" onClick={saveThresholds}
                    disabled={action.busy}>
              Apply thresholds
            </button>
          </section>

          <section>
            <h3 className="label mb-3">Cache</h3>
            <dl className="space-y-1.5">
              {(
                [
                  ['Exact hits', num(cache.data?.exact_hits)],
                  ['Semantic hits', num(cache.data?.semantic_hits)],
                  ['Misses', num(cache.data?.misses)],
                  ['Hit rate', ratio(cache.data?.hit_rate ?? 0, 1)],
                  ['Tokens saved', num(cache.data?.tokens_saved)],
                  ['Entries', num(cache.data?.entries)],
                ] as [string, string][]
              ).map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs">
                  <dt className="text-faint">{k}</dt>
                  <dd className="text-muted font-mono">{v}</dd>
                </div>
              ))}
            </dl>
            <button
              className="btn mt-3 w-full"
              onClick={() =>
                action.run(async () => {
                  await api.clearCache();
                  await cache.reload();
                  return 'Cache cleared.';
                })
              }
            >
              Clear cache
            </button>
          </section>

          <section>
            <h3 className="label mb-3">Tracing</h3>
            <div className="text-xs text-muted space-y-1.5">
              <div className="flex justify-between">
                <span className="text-faint">Local JSONL sink</span>
                <span>{telemetry.data?.local_sink.events ?? 0} events</span>
              </div>
              <div className="flex justify-between">
                <span className="text-faint">Langfuse</span>
                <span>
                  {telemetry.data?.langfuse.configured
                    ? telemetry.data.langfuse.active
                      ? 'active'
                      : 'configured, unreachable'
                    : 'not configured'}
                </span>
              </div>
            </div>
            <p className="text-micro text-faint mt-2 leading-snug">
              The local sink is always on, so traces survive blocked egress.
              Telemetry never fails a pricing run.
            </p>
          </section>

          <section>
            <h3 className="label mb-3">Resolved models</h3>
            <dl className="space-y-1.5">
              {Object.entries(config?.models ?? {}).map(([role, alias]) => (
                <div key={role} className="flex justify-between gap-3 text-xs">
                  <dt className="text-faint">{role}</dt>
                  <dd className="text-muted font-mono truncate">{alias}</dd>
                </div>
              ))}
            </dl>
            <p className="text-micro text-faint mt-2 leading-snug">
              Model IDs are never hardcoded — roles resolve to aliases from
              configuration and are probed against the gateway at boot.
            </p>
          </section>
        </div>
      </aside>
      <Toast message={action.message} onDismiss={action.clear} />
    </>
  );
}

/** iOS-style toggle (FR-066). */
function Toggle({
  label,
  checked,
  onChange,
  warning,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  warning?: string;
}) {
  return (
    <div className="mt-4">
      <div className="flex items-center gap-3">
        <span className="text-xs text-muted flex-1">{label}</span>
        <button
          role="switch"
          aria-checked={checked}
          aria-label={label}
          onClick={() => onChange(!checked)}
          className={`w-10 h-[22px] rounded-full transition-colors relative shrink-0 ${
            checked ? 'bg-danger' : 'bg-line'
          }`}
        >
          <span
            className={`absolute top-[3px] w-4 h-4 rounded-full bg-surface transition-all ${
              checked ? 'left-[21px]' : 'left-[3px]'
            }`}
          />
        </button>
      </div>
      {warning && (
        <p className="text-micro text-danger mt-1.5 leading-snug">{warning}</p>
      )}
    </div>
  );
}
