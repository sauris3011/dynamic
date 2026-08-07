import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, CheckCircle2, X } from 'lucide-react';

import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { num, ratio } from '../lib/format';
import { Toast } from './primitives';
import { useActionState } from '../lib/hooks';
import type { AgentRole, ConnectionTest, ModelTest, PlatformConfig } from '../lib/types';

/**
 * Settings drawer (FR-066, FR-067). Everything here applies at runtime; nothing
 * requires a restart, and nothing has to be re-entered after one.
 *
 * The API key field is write-only: it is sent to the backend and never read
 * back. The backend stores it encrypted against a machine-local key file so it
 * survives a restart, and returns only a fingerprint — enough to tell one
 * stored credential from another, useless to anyone who intercepts it. The
 * browser still holds no gateway credential of its own (FR-072, NFR-011).
 *
 * Model choices are a dropdown per agent, populated from the gateway's own
 * model list rather than from a list in this repository — a hardcoded list goes
 * stale the moment a deployment changes, and D4 exists to prevent exactly that.
 * Each agent gets its own test button because listing a model proves only that
 * the gateway knows the name; a real round trip is what proves it works.
 */

/** Every assignable role, with why it matters — order is the pipeline's. */
const AGENT_ROLES: { role: AgentRole; label: string; blurb: string }[] = [
  { role: 'router', label: 'Router', blurb: 'Classifies analyst questions' },
  { role: 'narrator', label: 'Narrator', blurb: 'Run commentary and summaries' },
  { role: 'analyst', label: 'Analyst', blurb: 'Per-SKU rationale' },
  { role: 'strategist', label: 'Strategist', blurb: 'Structured pricing strategy' },
  { role: 'embeddings', label: 'Embeddings', blurb: 'RAG retrieval vectors' },
];

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
  const [connTest, setConnTest] = useState<ConnectionTest | null>(null);
  const [testing, setTesting] = useState(false);
  const [thresholds, setThresholds] = useState({
    min_confidence: 0.75,
    max_delta_pct: 10,
    max_variance: 0.25,
    margin_buffer_pct: 3,
  });

  // Model list and per-agent selection. `nonce` forces a refetch after the
  // gateway changes — the previous list belonged to a different gateway and
  // showing it would be worse than showing nothing.
  const [nonce, setNonce] = useState(0);
  const models = useApi(() => api.gatewayModels(), [open, nonce]);
  const [roles, setRoles] = useState<Partial<Record<AgentRole, string>>>({});
  const [modelTests, setModelTests] = useState<
    Partial<Record<AgentRole, ModelTest | 'running'>>
  >({});

  /**
   * Seed the form from the server **once per opening**, never on every arriving
   * config.
   *
   * `config` is polled by the shell every 20 seconds and each poll yields a new
   * object. Keying this effect on that object meant a poll landing mid-edit
   * overwrote whatever had been typed — so a gateway URL reverted to the stored
   * one a few seconds after being changed, and an operator who kept typing
   * ended up saving the old value back. A form bound to a polled value is not
   * editable; the fetched value is a starting point, not a live binding.
   *
   * The ref resets when the drawer closes, so reopening shows current server
   * state. Saving does not need to re-seed: the fields already hold what was
   * just sent.
   */
  const seeded = useRef(false);
  useEffect(() => {
    if (!open) {
      seeded.current = false;
      return;
    }
    if (seeded.current || !config) return;
    seeded.current = true;
    setGatewayUrl(config.gateway_url);
    setInsecure(!config.tls.secure);
    setThresholds(config.thresholds);
  }, [open, config]);

  // Same rule for the dropdowns, with one difference: the model list is fetched
  // only on open and after a save (`nonce`), never polled, so adopting each
  // arriving payload cannot clobber an in-progress edit — and after a save it
  // is how the drawer picks up what the server actually stored.
  useEffect(() => {
    if (models.data) setRoles(models.data.roles);
  }, [models.data]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Declared before the early return so hook order stays stable across renders.
  const testConnection = useCallback(async () => {
    setTesting(true);
    try {
      // The typed values, not the saved ones — the point is to find out
      // whether a gateway works *before* committing to it.
      setConnTest(
        await api.testGateway({
          gateway_url: gatewayUrl || undefined,
          api_key: apiKey || undefined,
        }),
      );
    } catch (err) {
      setConnTest({
        reachable: false,
        models: [],
        latency_ms: 0,
        error: '',
        detail: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setTesting(false);
    }
  }, [gatewayUrl, apiKey]);

  const testRole = useCallback(
    async (role: AgentRole) => {
      setModelTests((prev) => ({ ...prev, [role]: 'running' }));
      try {
        const result = await api.testModel({
          role,
          model: roles[role] ?? '',
          gateway_url: gatewayUrl || undefined,
          api_key: apiKey || undefined,
        });
        setModelTests((prev) => ({ ...prev, [role]: result }));
      } catch (err) {
        setModelTests((prev) => ({
          ...prev,
          [role]: {
            ok: false,
            role,
            model: roles[role] ?? '',
            latency_ms: 0,
            detail: err instanceof Error ? err.message : String(err),
          },
        }));
      }
    },
    [roles, gatewayUrl, apiKey],
  );

  if (!open) return null;

  const saveGateway = () =>
    action.run(async () => {
      const result = await api.setGateway({
        gateway_url: gatewayUrl,
        api_key: apiKey || undefined,
        allow_insecure_tls: insecure,
      });
      setApiKey('');
      setModelTests({});
      setNonce((n) => n + 1);      // the model list belongs to the old gateway
      onChanged();
      return result.gateway.reachable
        ? 'Gateway saved and stored. It will still be set after a restart.'
        : `Gateway saved, but not reachable: ${result.gateway.detail}`;
    });

  const saveModels = () =>
    action.run(async () => {
      const result = await api.setModels(roles);
      setNonce((n) => n + 1);
      onChanged();
      const count = Object.keys(result.changed).length;
      if (!count) return 'No model assignments changed.';
      return [`${count} agent model(s) saved and applied.`, ...result.warnings].join(' ');
    });

  // Dropdown options. A successful test of an *unsaved* gateway wins: the
  // operator has just demonstrated it works, and making them save an untested
  // URL before they can pick a model from it inverts the useful order.
  const fromTest = Boolean(connTest?.reachable && connTest.models.length);
  const options = fromTest ? connTest!.models : (models.data?.models ?? []);

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
              API key{' '}
              {config?.api_key_set && (
                <span className="text-info">
                  (set · {config.api_key_fingerprint}
                  {config.api_key_persisted ? ' · stored' : ' · session only'})
                </span>
              )}
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
              Encrypted at rest against a key file this machine holds separately,
              so it survives a restart but a copied database does not carry it.
              Never sent back to the browser — the code above identifies which
              key is loaded without disclosing it.
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

            <div className="flex gap-2 mt-4">
              <button
                className="btn flex-1"
                onClick={testConnection}
                disabled={testing}
              >
                {testing ? 'Testing…' : 'Test connection'}
              </button>
              <button
                className="btn-primary flex-1"
                onClick={saveGateway}
                disabled={action.busy}
              >
                Save &amp; apply
              </button>
            </div>
            {connTest && (
              <TestResult ok={connTest.reachable}>
                {connTest.detail}
                {connTest.reachable && ` (${connTest.latency_ms} ms)`}
              </TestResult>
            )}
            <p className="text-micro text-faint mt-2 leading-snug">
              Test uses the values typed above, saved or not. Saving persists
              them to this machine and applies them to the running platform —
              no restart, and nothing to re-enter after one.
            </p>
          </section>

          <section>
            <h3 className="label mb-1">Agent models</h3>
            <p className="text-micro text-faint mb-3 leading-snug">
              {options.length > 0
                ? `${options.length} model(s) offered by the gateway${
                    fromTest ? ' just tested — save the gateway to keep them' : ''
                  }.`
                : models.loading
                  ? 'Loading the gateway model list…'
                  : 'The gateway returned no model list. Fix the URL and key above ' +
                    'and press Test — the dropdowns fill from whatever answers, ' +
                    'saved or not. Current assignments are still shown and testable.'}
            </p>

            {AGENT_ROLES.map(({ role, label, blurb }) => {
              const selected = roles[role] ?? '';
              // An assigned alias the gateway does not list still has to appear,
              // or selecting the dropdown would silently reassign the agent.
              const orphaned = selected !== '' && !options.includes(selected);
              const test = modelTests[role];

              return (
                <div key={role} className="py-2 border-b border-hairline last:border-0">
                  <div className="flex items-baseline gap-2">
                    <span className="text-xs text-muted">{label}</span>
                    <span className="text-micro text-faint flex-1 truncate">{blurb}</span>
                    <button
                      className="btn !px-2 !py-1 text-micro shrink-0"
                      onClick={() => void testRole(role)}
                      disabled={test === 'running'}
                    >
                      {test === 'running' ? 'Testing…' : 'Test'}
                    </button>
                  </div>
                  <select
                    aria-label={`Model for the ${label} agent`}
                    className="input font-mono mt-1.5"
                    value={selected}
                    onChange={(e) =>
                      setRoles((prev) => ({ ...prev, [role]: e.target.value }))
                    }
                  >
                    {role === 'embeddings' && (
                      <option value="">— none (local embedding fallback) —</option>
                    )}
                    {orphaned && (
                      <option value={selected}>{selected} (not listed by gateway)</option>
                    )}
                    {options.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  {test && test !== 'running' && (
                    <TestResult ok={test.ok}>
                      {test.detail}
                      {test.ok && ` · ${test.latency_ms} ms`}
                    </TestResult>
                  )}
                </div>
              );
            })}

            <button className="btn-primary mt-3 w-full" onClick={saveModels}
                    disabled={action.busy}>
              Save model assignments
            </button>
            <p className="text-micro text-faint mt-2 leading-snug">
              Stored on this machine and applied immediately. Test issues one
              real request to the model — the gateway listing a name does not
              prove the credential behind it works. Changing the embedding
              model invalidates existing RAG collections; reset and re-ingest.
            </p>
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

          {config && config.persisted_overrides.length > 0 && (
            <section>
              <h3 className="label mb-2">Stored on this machine</h3>
              <p className="text-micro text-faint leading-snug">
                {config.persisted_overrides.join(', ')} — set here rather than in
                <code className="font-mono"> .env</code>, and re-applied at every
                start. Editing those variables in
                <code className="font-mono"> .env</code> will have no effect until
                the stored value is changed here again.
              </p>
            </section>
          )}
        </div>
      </aside>
      <Toast message={action.message} onDismiss={action.clear} />
    </>
  );
}

/**
 * Outcome of a connectivity or model test.
 *
 * Colour and icon both carry the pass/fail, never colour alone — the same
 * reason the band indicators do (FR-064): a red-green distinction is invisible
 * to a meaningful share of operators.
 */
function TestResult({ ok, children }: { ok: boolean; children: ReactNode }) {
  const Icon = ok ? CheckCircle2 : AlertCircle;
  return (
    <p
      className={`text-micro mt-2 leading-snug flex items-start gap-1.5 ${
        ok ? 'text-accent' : 'text-danger'
      }`}
    >
      <Icon size={12} className="mt-px shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
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
