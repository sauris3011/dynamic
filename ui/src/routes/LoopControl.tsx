import { useState } from 'react';

import { api, categories } from '../lib/api';
import { useActionState, useApi } from '../lib/hooks';
import { countdown, datetime, num } from '../lib/format';
import {
  Card,
  Empty,
  ErrorNote,
  SectionTitle,
  Stat,
  Toast,
} from '../components/primitives';
import type { LoopStatus, Objective } from '../lib/types';

/**
 * Continuous loop control (W9, FR-117, FR-121 .. FR-127).
 *
 * Two properties the panel exists to make legible:
 *
 *   The loop never auto-starts. It is off at boot and requires an explicit
 *   action every time — it is an interval task inside the application's own
 *   lifespan, not a system daemon, and it dies with the process.
 *
 *   A failed iteration stops the loop. It never retries silently into a broken
 *   state, and the error is shown here rather than buried in a log.
 */
export function LoopControl({
  loop,
  onChanged,
}: {
  loop: LoopStatus | null;
  onChanged: () => void;
}) {
  const action = useActionState();
  const [interval, setIntervalSeconds] = useState(300);
  const [scopeValue, setScopeValue] = useState(categories[0]);
  const [objective, setObjective] = useState<Objective>('balanced');

  const runs = useApi(() => api.runs(30), [], 10000);
  const loopRuns = (runs.data ?? []).filter((r) => r.trigger === 'loop');

  const start = () =>
    action.run(async () => {
      await api.startLoop({
        interval_seconds: interval,
        scope_kind: 'category',
        scope_value: scopeValue,
        objective,
      });
      onChanged();
      return `Loop started over ${scopeValue}, re-evaluating every ${interval}s.`;
    });

  const stop = () =>
    action.run(async () => {
      await api.stopLoop();
      onChanged();
      return 'Loop stopped. The in-flight iteration finishes cleanly.';
    });

  const nextIn =
    loop?.running && loop.started_at
      ? loop.interval_seconds -
        (((Date.now() - new Date(loop.started_at).getTime()) / 1000) %
          loop.interval_seconds)
      : 0;

  return (
    <div className="p-7 space-y-5">
      <div className="flex items-start">
        <SectionTitle
          eyebrow="Continuous loop"
          title="Re-evaluate on an interval, stop at any moment"
          lede="An in-process interval task inside the application lifespan — not a system daemon, not an OS-scheduled job. It installs nothing and terminates with the process."
        />
        <div className="ml-auto flex items-end gap-2.5">
          <div>
            <label className="label block mb-1" htmlFor="loop-scope">
              Scope
            </label>
            <select
              id="loop-scope"
              className="input w-40"
              value={scopeValue}
              onChange={(e) => setScopeValue(e.target.value)}
              disabled={loop?.running}
            >
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label block mb-1" htmlFor="loop-interval">
              Interval
            </label>
            <select
              id="loop-interval"
              className="input w-28"
              value={interval}
              onChange={(e) => setIntervalSeconds(Number(e.target.value))}
              disabled={loop?.running}
            >
              {[30, 60, 120, 300, 900, 1800].map((s) => (
                <option key={s} value={s}>
                  {s < 60 ? `${s}s` : `${s / 60} min`}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label block mb-1" htmlFor="loop-objective">
              Objective
            </label>
            <select
              id="loop-objective"
              className="input w-28"
              value={objective}
              onChange={(e) => setObjective(e.target.value as Objective)}
              disabled={loop?.running}
            >
              <option value="balanced">Balanced</option>
              <option value="revenue">Revenue</option>
              <option value="margin">Margin</option>
            </select>
          </div>
          {loop?.running ? (
            <button className="btn-danger h-[34px]" onClick={stop} disabled={action.busy}>
              Stop loop
            </button>
          ) : (
            <button className="btn-primary h-[34px]" onClick={start} disabled={action.busy}>
              Start loop
            </button>
          )}
        </div>
      </div>

      {loop?.last_error && (
        <ErrorNote>
          <strong>The loop stopped on a failed iteration.</strong> {loop.last_error} It
          did not retry — continuing to price on inputs that already failed once would be
          worse than stopping.
        </ErrorNote>
      )}

      <div className="grid grid-cols-4 gap-3.5">
        <Stat
          label="State"
          value={loop?.running ? 'Running' : 'Idle'}
          detail={loop?.running ? `every ${loop.interval_seconds}s` : 'off at boot, always'}
          tone={loop?.running ? 'info' : 'ink'}
          accent={loop?.running ? 'info' : 'none'}
        />
        <Stat
          label="Iterations this session"
          value={num(loop?.iterations ?? 0)}
          detail="resets when the loop restarts"
        />
        <Stat
          label="Next iteration"
          value={loop?.running ? countdown(nextIn) : '—'}
          detail={loop?.started_at ? `started ${datetime(loop.started_at)}` : 'not started'}
        />
        <Stat
          label="Last run"
          value={loop?.last_run_id ? loop.last_run_id.slice(-8) : '—'}
          detail={loop?.stopped_at ? `stopped ${datetime(loop.stopped_at)}` : 'in progress'}
        />
      </div>

      <div className="grid grid-cols-[1.3fr_1fr] gap-5">
        <Card title="Iteration history" right={<span className="label">loop-triggered runs</span>}>
          {loopRuns.length ? (
            <div className="space-y-1">
              <div className="grid grid-cols-[8rem_1fr_5rem_5rem_6rem_5rem] gap-3 label pb-1.5
                              border-b border-line">
                <span>Run</span>
                <span>Scope</span>
                <span>SKUs</span>
                <span>Escalated</span>
                <span>Mode</span>
                <span>Status</span>
              </div>
              {loopRuns.map((run) => (
                <div
                  key={run.run_id}
                  className="grid grid-cols-[8rem_1fr_5rem_5rem_6rem_5rem] gap-3 py-2
                             border-b border-hairline last:border-0 items-center"
                >
                  <span className="font-mono text-tiny text-faint truncate">
                    {run.run_id}
                  </span>
                  <span className="text-tiny text-muted">
                    {run.scope_value ?? 'catalog'} · {run.objective}
                  </span>
                  <span className="text-tiny text-muted">{num(run.sku_count)}</span>
                  <span
                    className={`text-tiny ${run.escalated ? 'text-danger' : 'text-faint'}`}
                  >
                    {num(run.escalated ?? 0)}
                  </span>
                  <span className="text-tiny text-faint">{run.mode}</span>
                  <span
                    className={`text-tiny ${
                      run.status === 'completed' ? 'text-muted' : 'text-danger'
                    }`}
                  >
                    {run.status}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Empty>
              No loop iterations yet. Start the loop above — it never starts on its own.
            </Empty>
          )}
        </Card>

        <div className="space-y-4">
          <Card title="Why this is not a daemon" accent="info">
            <p className="text-xs text-faint leading-relaxed">
              The loop is a thread inside this application process. It installs nothing,
              requires no privileges, cannot be started by anything other than an explicit
              action here, and dies when the process does. What the zero-admin constraint
              prohibits is a service living outside the application; this lives inside it.
            </p>
          </Card>
          <Card title="A failed iteration stops the loop">
            <p className="text-xs text-faint leading-relaxed">
              No silent retries and no continuing on stale inputs. A loop that limps along
              quietly is worse than one that stops loudly, because the damage accumulates
              where nobody is looking.
            </p>
          </Card>
          <Card title="The kill switch is always reachable">
            <p className="text-xs text-faint leading-relaxed">
              It stops the loop, cancels every pending automatic approval, and reverts the
              operating mode to Supervised in one action — from the header, on any route.
            </p>
          </Card>
        </div>
      </div>

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}
