import { useState } from 'react';
import {
  Activity,
  Moon,
  OctagonX,
  Settings,
  ShieldAlert,
  Sparkles,
  Sun,
} from 'lucide-react';

import { api } from '../lib/api';
import { countdown } from '../lib/format';
import { MODE_LABEL, MODE_MEANING } from '../lib/labels';
import type { Health, LoopStatus, Mode, PlatformConfig } from '../lib/types';

/**
 * The global header, reduced to the four things that must be reachable from
 * every screen:
 *
 *   FR-115  Operating mode, always displayed
 *   FR-116  Kill switch, visually distinct, one confirmation step
 *   FR-120  Loop-running indicator
 *   NFR-019 An insecure TLS posture is never quiet
 *
 * The LLM monitor that used to sit here (calls, tokens, estimated cost) moved to
 * the Diagnostics tab. It was the densest block of engineering telemetry in the app
 * and it was on screen during every conversation with a non-technical viewer.
 */

const MODES: Mode[] = ['supervised', 'assisted', 'autonomous'];

export function Header({
  config,
  loop,
  health,
  onChanged,
  theme,
  onToggleTheme,
  onOpenSettings,
  assistantOpen,
  onToggleAssistant,
}: {
  config: PlatformConfig | null;
  loop: LoopStatus | null;
  health: Health | null;
  onChanged: () => void;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;
  onOpenSettings: () => void;
  assistantOpen: boolean;
  onToggleAssistant: () => void;
}) {
  const [confirmKill, setConfirmKill] = useState(false);
  const [busy, setBusy] = useState(false);

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

  const insecureTls = health?.tls && !health.tls.secure;

  return (
    <header className="h-14 shrink-0 border-b border-hairline bg-raised flex items-center gap-5 px-5">
      <div className="flex items-center gap-2.5">
        <span className="w-2 h-2 bg-accent" aria-hidden />
        <span className="text-xs font-bold tracking-[0.14em] text-ink">PRICING AI</span>
        <span className="text-tiny text-faint tracking-wide border-l border-line pl-2.5">
          RETAIL · GROCERY EU
        </span>
      </div>

      <LoopIndicator loop={loop} />

      {insecureTls && (
        <span
          className="flex items-center gap-2 text-tiny text-danger border border-danger/40
                     bg-danger-wash rounded-lg px-2.5 py-1"
          title={health?.tls.detail}
        >
          <ShieldAlert size={13} aria-hidden />
          TLS OFF
        </span>
      )}

      <div className="ml-auto flex items-center gap-4">
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
                title={MODE_MEANING[mode]}
                className={`px-3 py-1.5 text-tiny transition-colors border-r border-line
                            last:border-r-0 ${
                              active ? 'bg-line text-ink font-semibold' : 'text-faint hover:text-muted'
                            }`}
              >
                {MODE_LABEL[mode]}
              </button>
            );
          })}
        </div>

        <button
          onClick={() => setConfirmKill(true)}
          className="flex items-center gap-2 px-3 py-1.5 border border-danger rounded-lg
                     text-tiny text-danger hover:bg-danger-wash transition-colors"
        >
          <OctagonX size={14} aria-hidden />
          Stop everything
        </button>

        <button
          onClick={onToggleAssistant}
          aria-pressed={assistantOpen}
          className={`flex items-center gap-2 px-3 py-1.5 border rounded-lg text-tiny
                      transition-colors ${
                        assistantOpen
                          ? 'border-accent text-accent bg-accent-wash'
                          : 'border-line text-faint hover:text-ink'
                      }`}
          title="Ask the assistant about what is on screen"
        >
          <Sparkles size={14} aria-hidden />
          Ask
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
          aria-label="Settings — gateway connection and models"
          title="Gateway connection and models"
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

function LoopIndicator({ loop }: { loop: LoopStatus | null }) {
  if (!loop?.running) return null;

  const next = loop.started_at
    ? loop.interval_seconds -
      (((Date.now() - new Date(loop.started_at).getTime()) / 1000) % loop.interval_seconds)
    : 0;

  return (
    <span
      className="flex items-center gap-2 px-2.5 py-1 border border-info/40 bg-info-wash
                 rounded-lg text-tiny text-info"
    >
      <Activity size={12} aria-hidden className="animate-pulse" />
      Running on a schedule · round {loop.iterations} · next in {countdown(next)}
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
    <div
      className="fixed inset-0 z-50 bg-canvas/80 grid place-items-center"
      role="dialog"
      aria-modal="true"
      aria-label="Confirm stop"
    >
      <div className="card border-danger/50 max-w-lg p-6">
        <div className="flex items-center gap-3">
          <OctagonX size={20} className="text-danger" aria-hidden />
          <h2 className="text-lg font-bold text-ink">Stop everything?</h2>
        </div>
        <p className="text-xs text-muted mt-3 leading-relaxed">
          This stops the scheduled runs, cancels every pending automatic approval, and puts
          the system back into <strong className="text-ink">Supervised</strong> — in one
          action. Prices already sent to the store are not rolled back; revert those
          individually from Review.
        </p>
        <div className="flex gap-2.5 mt-5 justify-end">
          <button className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="btn-danger" onClick={onConfirm} disabled={busy}>
            {busy ? 'Stopping…' : 'Stop and return to Supervised'}
          </button>
        </div>
      </div>
    </div>
  );
}
