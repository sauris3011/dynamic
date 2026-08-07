import { useState } from 'react';
import {
  Activity,
  Moon,
  OctagonX,
  Settings,
  ShieldAlert,
  Sun,
} from 'lucide-react';

import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { countdown, money, num, ratio } from '../lib/format';
import type { LoopStatus, Mode, PlatformConfig, Telemetry } from '../lib/types';

/**
 * The global header. Four things live here permanently because the PRD requires
 * them to be visible on every route, not buried in a submenu:
 *
 *   FR-065  LLM monitor: active calls, cumulative tokens, estimated cost
 *   FR-115  Operating mode, always displayed
 *   FR-116  Kill switch, visually distinct, one confirmation step
 *   FR-120  Loop-running indicator
 */

const MODES: Mode[] = ['supervised', 'assisted', 'autonomous'];

export function Header({
  config,
  telemetry,
  loop,
  onOpenSettings,
  onChanged,
  theme,
  onToggleTheme,
}: {
  config: PlatformConfig | null;
  telemetry: Telemetry | null;
  loop: LoopStatus | null;
  onOpenSettings: () => void;
  onChanged: () => void;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;
}) {
  const [confirmKill, setConfirmKill] = useState(false);
  const [busy, setBusy] = useState(false);
  const health = useApi(() => api.health(), [], 15000);

  const changeMode = async (mode: Mode) => {
    if (!config || config.mode === mode || busy) return;
    setBusy(true);
    try {
      await api.setMode(mode);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const kill = async () => {
    setBusy(true);
    try {
      await api.killSwitch('Kill switch activated from the header.');
      setConfirmKill(false);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const insecureTls = health.data?.tls && !health.data.tls.secure;

  return (
    <header className="h-14 shrink-0 border-b border-hairline bg-raised flex items-center gap-5 px-5">
      <div className="flex items-center gap-2.5">
        <span className="w-2 h-2 bg-accent" aria-hidden />
        <span className="text-xs font-bold tracking-[0.14em] text-ink">
          PRICING AI PLATFORM
        </span>
        <span className="text-tiny text-faint tracking-wide border-l border-line pl-2.5">
          RETAIL · GROCERY EU
        </span>
      </div>

      <LoopIndicator loop={loop} />

      {insecureTls && (
        // NFR-019: an insecure TLS posture is never allowed to be quiet.
        <span
          className="flex items-center gap-2 text-tiny text-danger border border-danger/40
                     bg-danger-wash rounded-lg px-2.5 py-1"
          title={health.data?.tls.detail}
        >
          <ShieldAlert size={13} aria-hidden />
          TLS VERIFICATION OFF
        </span>
      )}

      <div className="ml-auto flex items-center gap-4">
        <Monitor telemetry={telemetry} />

        <div
          className="flex items-center border border-line rounded-lg overflow-hidden"
          role="group"
          aria-label="Operating mode"
        >
          {MODES.map((mode) => {
            const active = config?.mode === mode;
            return (
              <button
                key={mode}
                onClick={() => void changeMode(mode)}
                disabled={busy}
                aria-pressed={active}
                className={`px-3 py-1.5 text-tiny tracking-wide border-r border-line
                            last:border-r-0 transition-colors ${
                              active
                                ? 'bg-line text-ink'
                                : 'text-faint hover:text-muted'
                            }`}
              >
                {mode.toUpperCase()}
              </button>
            );
          })}
        </div>

        <button
          onClick={() => setConfirmKill(true)}
          className="flex items-center gap-2 px-3 py-1.5 border border-danger rounded-lg
                     text-tiny tracking-wide text-danger hover:bg-danger-wash transition-colors"
        >
          <OctagonX size={14} aria-hidden />
          KILL SWITCH
        </button>

        <button
          onClick={onToggleTheme}
          className="w-7 h-7 border border-line rounded-lg grid place-items-center
                     text-faint hover:text-ink transition-colors"
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        >
          {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        <button
          onClick={onOpenSettings}
          className="w-7 h-7 border border-line rounded-lg grid place-items-center
                     text-faint hover:text-ink transition-colors"
          aria-label="Open settings"
        >
          <Settings size={14} />
        </button>
      </div>

      {confirmKill && (
        <KillConfirm busy={busy} onCancel={() => setConfirmKill(false)} onConfirm={kill} />
      )}
    </header>
  );
}

function Monitor({ telemetry }: { telemetry: Telemetry | null }) {
  const cells: [string, string][] = [
    ['CALLS', num(telemetry?.active_llm_calls ?? 0)],
    ['IN', num(telemetry?.tokens_in ?? 0)],
    ['OUT', num(telemetry?.tokens_out ?? 0)],
    ['EST', money(telemetry?.estimated_cost_usd ?? 0)],
    ['CACHE', ratio(telemetry?.cache.hit_rate ?? 0)],
  ];
  return (
    <div
      className="flex items-center gap-3.5 px-3 py-1.5 border border-line rounded-lg bg-surface"
      title="Cumulative LLM spend, computed server-side. The client never contacts the gateway."
    >
      {cells.map(([label, value]) => (
        <span key={label} className="flex items-baseline gap-1.5">
          <span className="text-micro text-faint tracking-wider">{label}</span>
          <span className="text-tiny text-ink font-medium">{value}</span>
        </span>
      ))}
    </div>
  );
}

function LoopIndicator({ loop }: { loop: LoopStatus | null }) {
  if (!loop?.running) {
    return (
      <span className="flex items-center gap-2 px-2.5 py-1 border border-line rounded-lg
                       text-tiny text-faint">
        <span className="w-1.5 h-1.5 rounded-full bg-faint" aria-hidden />
        LOOP IDLE
      </span>
    );
  }
  const next = loop.started_at
    ? loop.interval_seconds -
      ((Date.now() - new Date(loop.started_at).getTime()) / 1000) % loop.interval_seconds
    : 0;
  return (
    <span className="flex items-center gap-2 px-2.5 py-1 border border-info/40 bg-info-wash
                     rounded-lg text-tiny text-info">
      <Activity size={12} aria-hidden className="animate-pulse" />
      LOOP RUNNING · ITER {loop.iterations} · NEXT {countdown(next)}
    </span>
  );
}

function KillConfirm({
  busy,
  onCancel,
  onConfirm,
}: {
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 bg-canvas/80 grid place-items-center" role="dialog"
         aria-modal="true" aria-label="Confirm kill switch">
      <div className="card border-danger/50 max-w-lg p-6">
        <div className="flex items-center gap-3">
          <OctagonX size={20} className="text-danger" aria-hidden />
          <h2 className="text-lg font-bold text-ink">Halt the system?</h2>
        </div>
        <p className="text-xs text-muted mt-3 leading-relaxed">
          This stops the continuous loop, cancels every pending automatic approval,
          and reverts the operating mode to <strong className="text-ink">Supervised</strong> —
          in one action. Prices already applied to the commerce system are not
          rolled back; revert those individually from the review queue.
        </p>
        <div className="flex gap-2.5 mt-5 justify-end">
          <button className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="btn-danger" onClick={onConfirm} disabled={busy}>
            {busy ? 'Halting…' : 'Halt and revert to Supervised'}
          </button>
        </div>
      </div>
    </div>
  );
}
