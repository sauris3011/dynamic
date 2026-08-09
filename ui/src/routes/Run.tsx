import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check } from 'lucide-react';

import { api } from '../lib/api';
import { useActionState, useApi } from '../lib/hooks';
import { countdown, datetime, duration, num } from '../lib/format';
import {
  BAND_LABEL,
  CATEGORIES,
  MODE_MEANING,
  OBJECTIVES,
  OBJECTIVE_HINT,
  OBJECTIVE_LABEL,
  STAGES,
} from '../lib/labels';
import { BandGlyph } from '../components/BandIndicator';
import {
  Card,
  Disclosure,
  Empty,
  ErrorNote,
  Meter,
  SectionTitle,
  Stat,
  Toast,
} from '../components/primitives';
import type { Band, LoopStatus, Objective, PlatformConfig, RunProgress } from '../lib/types';

/**
 * Price a batch and watch it finish.
 *
 * One action, one progress view, one hand-off to Review. Everything an engineer
 * would want while watching — per-stage seconds, which model ran each stage,
 * token spend — is on the Diagnostics tab, because this screen is the one most often
 * on a projector.
 */
export function Run({
  config,
  loop,
  onChanged,
}: {
  config: PlatformConfig | null;
  loop: LoopStatus | null;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const action = useActionState();
  const [runId, setRunId] = useState<string | null>(null);
  const [scopeKind, setScopeKind] = useState<'all' | 'category'>('category');
  const [category, setCategory] = useState(CATEGORIES[0]);
  const [objective, setObjective] = useState<Objective>('balanced');

  const runs = useApi(() => api.runs(30), [], 10000);
  const active = runs.data?.[0];
  const watching = runId ?? active?.run_id ?? null;

  const progress = useApi<RunProgress | null>(
    () => (watching ? api.runProgress(watching) : Promise.resolve(null)),
    [watching],
    2000,
  );

  const running = progress.data?.status === 'running' || progress.data?.status === 'queued';
  useEffect(() => {
    if (!running && runId) void runs.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  const start = () =>
    action.run(async () => {
      const result = await api.startRun({
        scope_kind: scopeKind,
        scope_value: scopeKind === 'category' ? category : null,
        objective,
      });
      setRunId(result.run_id);
      onChanged();
      return `Pricing ${result.scope}. This usually takes under a minute.`;
    });

  const bands = progress.data?.bands ?? {};
  const totalBands = Object.values(bands).reduce((a, b) => a + (b ?? 0), 0);
  const needsDecision = bands.review ?? 0;
  const autonomy = progress.data?.autonomy;

  // Reported by the narration stage itself. Absent when narration never ran,
  // which is why this is not inferred from the stage's item count.
  const explained = progress.data?.narrated ?? 0;

  return (
    <div className="p-7 space-y-5 max-w-6xl">
      <div className="flex items-start gap-6">
        <SectionTitle
          eyebrow="Run"
          title="Price a batch of products"
          lede="Pick what to price and what to optimise for. The system proposes a price for
                every product in scope, then sorts them by whether it can decide alone."
        />

        <div className="ml-auto flex items-end gap-2.5 shrink-0">
          <div>
            <label className="label block mb-1" htmlFor="scope">
              What to price
            </label>
            <select
              id="scope"
              className="input w-44"
              value={scopeKind === 'all' ? 'all' : category}
              onChange={(e) => {
                if (e.target.value === 'all') setScopeKind('all');
                else {
                  setScopeKind('category');
                  setCategory(e.target.value);
                }
              }}
            >
              <option value="all">Everything</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="label block mb-1" htmlFor="objective">
              Goal
            </label>
            <select
              id="objective"
              className="input w-36"
              value={objective}
              onChange={(e) => setObjective(e.target.value as Objective)}
              title={OBJECTIVE_HINT[objective]}
            >
              {OBJECTIVES.map((o) => (
                <option key={o} value={o}>
                  {OBJECTIVE_LABEL[o]}
                </option>
              ))}
            </select>
          </div>

          <button
            className="btn-primary h-[34px]"
            onClick={start}
            disabled={action.busy || running}
          >
            {running ? 'Running…' : 'Run pricing'}
          </button>
        </div>
      </div>

      {config && (
        <p className="text-xs text-faint -mt-2">{MODE_MEANING[config.mode]}</p>
      )}

      {progress.data?.errors?.length ? (
        <ErrorNote>
          <strong>The run {progress.data.status}.</strong>{' '}
          {progress.data.errors.join(' · ')}
        </ErrorNote>
      ) : null}

      <div className="grid grid-cols-4 gap-3.5">
        <Stat
          label="Products priced"
          value={num(progress.data?.sku_count ?? active?.sku_count ?? 0)}
          tone="accent"
          accent="accent"
        />
        <Stat
          label={BAND_LABEL.review}
          value={num(needsDecision)}
          detail="waiting for you"
          tone={needsDecision ? 'info' : 'ink'}
          accent={needsDecision ? 'info' : 'none'}
        />
        {/* The band is a classification, not an outcome. Whether these actually
            went out depends on the operating mode, and is reported separately
            below — labelling this "Approved automatically" claimed something
            that had not happened. */}
        <Stat
          label="Could go automatically"
          value={num(bands.auto_approve ?? 0)}
          detail={
            autonomy
              ? autonomy.auto_approved > 0
                ? `${num(autonomy.auto_approved)} approved by the system`
                : 'none went out — see below'
              : 'if the mode allows it'
          }
        />
        <Stat
          label={BAND_LABEL.escalate}
          value={num(bands.escalate ?? active?.escalated ?? 0)}
          detail="a policy rule stopped these"
          tone={bands.escalate ? 'danger' : 'ink'}
          accent={bands.escalate ? 'danger' : 'none'}
        />
      </div>

      <div className="grid grid-cols-[1fr_20rem] gap-5">
        <ProgressCard progress={progress.data} running={running} />

        <div className="space-y-4">
          {/* While a run is in flight there is nothing to report yet. Saying
              "Nothing priced yet — run a batch above" *during* a batch reads as
              a contradiction, so the panel states what it is waiting for
              instead, and the button only appears once it can do something. */}
          {totalBands ? (
            <>
              <Card title="How it came out">
                {(['review', 'auto_approve', 'escalate'] as Band[]).map((band) => (
                  <div
                    key={band}
                    className="flex items-center gap-2.5 py-2 border-b border-hairline last:border-0"
                  >
                    <BandGlyph band={band} size={10} />
                    <span className="text-xs text-ink">{BAND_LABEL[band]}</span>
                    <span className="ml-auto text-lg font-light text-ink tabular-nums">
                      {num(bands[band] ?? 0)}
                    </span>
                  </div>
                ))}
                <Meter value={(bands.auto_approve ?? 0) / totalBands} tone="accent" />
              </Card>

              <PriceOutcome autonomy={autonomy} config={config} bands={bands} />

              <button className="btn-primary w-full" onClick={() => navigate('/review')}>
                Review {needsDecision > 0 ? `${num(needsDecision)} ` : ''}recommendations →
              </button>
            </>
          ) : running ? (
            <Card title="How it came out">
              <p className="text-xs text-faint leading-relaxed">
                The breakdown appears here once the run finishes — how many the system
                could decide alone, how many need you, and how many a policy rule
                stopped.
              </p>
            </Card>
          ) : (
            <Card title="How it came out">
              <Empty>Nothing priced yet. Start a run above.</Empty>
            </Card>
          )}
        </div>
      </div>

      {totalBands > 0 && explained > 0 && (
        <Card title="Where the explanations are">
          <p className="text-xs text-muted leading-relaxed">
            <strong className="text-ink">{num(explained)}</strong> of{' '}
            {num(progress.data?.sku_count ?? totalBands)} products got a written
            explanation with its sources cited — the ones with the most money riding on
            them. The rest carry a computed explanation built from the same figures,
            without the prose.
          </p>
          <p className="text-tiny text-faint mt-2 leading-relaxed">
            To read one: open Review, pick a product, and expand{' '}
            <strong className="text-muted">Why this price</strong>. Written ones are
            marked and list the documents they drew on.
          </p>
          <button className="btn mt-3" onClick={() => navigate('/review')}>
            Go read them →
          </button>
        </Card>
      )}

      <QualityGate progress={progress.data} />

      <Disclosure
        title="Recent runs"
        hint={`${runs.data?.length ?? 0} recorded`}
      >
        {runs.data?.length ? (
          <div className="space-y-1">
            {runs.data.slice(0, 8).map((run) => (
              <button
                key={run.run_id}
                onClick={() => setRunId(run.run_id)}
                className="w-full flex items-center gap-3 py-1.5 text-left
                           border-b border-hairline last:border-0 hover:bg-line/20 px-1"
              >
                <span className="text-tiny text-faint w-40">{datetime(run.started_at)}</span>
                <span className="text-tiny text-muted">{run.scope_value ?? 'Everything'}</span>
                <span className="text-tiny text-faint">
                  {OBJECTIVE_LABEL[run.objective as Objective] ?? run.objective}
                </span>
                <span className="ml-auto text-tiny text-faint w-24 text-right">
                  {num(run.sku_count)} products
                </span>
                <span
                  className={`text-tiny w-20 text-right ${
                    run.status === 'completed'
                      ? 'text-muted'
                      : run.status === 'halted'
                        ? 'text-accent'
                        : 'text-danger'
                  }`}
                >
                  {run.status}
                </span>
              </button>
            ))}
          </div>
        ) : (
          <Empty>No runs recorded.</Empty>
        )}
      </Disclosure>

      <ScheduleSection loop={loop} onChanged={onChanged} action={action} runs={runs.data ?? []} />

      <Toast message={action.message} onDismiss={action.clear} />
    </div>
  );
}

/**
 * Did any price actually change?
 *
 * The band counts above say how each product was *classified*. This says what
 * the autonomy policy then *did* — which in Supervised mode is nothing at all,
 * however many products were eligible. Those are different numbers and
 * conflating them is the difference between "65 prices changed in your store"
 * and "65 prices could have, and none did".
 */
function PriceOutcome({
  autonomy,
  config,
  bands,
}: {
  autonomy: RunProgress['autonomy'];
  config: PlatformConfig | null;
  bands: Partial<Record<Band, number>>;
}) {
  const eligible = bands.auto_approve ?? 0;
  const supervised = config?.mode === 'supervised';

  // The run finished before this session started, so the outcome was never
  // reported to this client. Don't invent one.
  if (!autonomy) {
    return (
      <Card title="Did any price change?">
        <p className="text-xs text-faint leading-relaxed">
          Not recorded for this run. The Audit tab holds the definitive answer — look for
          a <strong className="text-muted">prices sent</strong> entry.
        </p>
      </Card>
    );
  }

  const pushed = autonomy.pushed ?? 0;
  const approved = autonomy.auto_approved ?? 0;
  const held = autonomy.held_no_change ?? 0;

  return (
    <Card
      title="Did any price change?"
      accent={pushed > 0 ? 'accent' : 'none'}
    >
      <div className="text-3xl font-light text-ink tabular-nums leading-none">
        {num(pushed)}
      </div>
      <div className="text-xs text-muted mt-1">
        {pushed === 1 ? 'price sent to the store' : 'prices sent to the store'}
      </div>

      <p className="text-tiny text-faint mt-3 leading-relaxed">
        {supervised ? (
          <>
            Supervised mode: nothing goes out on its own. All {num(eligible)} the system
            could have decided are waiting for you in Review. Switch to Assisted in the top
            bar to let them through.
          </>
        ) : approved === 0 ? (
          <>Nothing qualified to go out without you on this run.</>
        ) : (
          <>
            The system approved {num(approved)}
            {held > 0 && (
              <>
                {' '}
                — {num(held)} of those needed no change, so only {num(pushed)} were
                actually sent
              </>
            )}
            . Every one is reversible from Review.
          </>
        )}
      </p>

      <p className="text-micro text-faint mt-2 leading-snug">
        The full record is on the Audit tab: who decided what, and which batch went to the
        store.
      </p>
    </Card>
  );
}

/**
 * The answer to "is anything actually happening?".
 *
 * Three independent signals, because any one of them can legitimately sit still
 * for a while: a bar that advances, a clock that ticks every second regardless
 * of what the server says, and the name of the product being worked on. A run
 * can spend six minutes in narration issuing one gateway call per product — the
 * bar barely moves during that, so the clock and the per-product counter are
 * what carry the message that the system is alive.
 *
 * The estimate is deliberately labelled "about" and only appears once there is
 * enough of a run behind it to extrapolate from. An precise-looking countdown
 * that is wrong is worse than no countdown.
 */
function ProgressCard({
  progress,
  running,
}: {
  progress: RunProgress | null;
  running: boolean;
}) {
  // Ticks every second so the elapsed time moves even when the server has
  // nothing new to say. This is the signal that survives a slow stage.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const startedAt = progress?.started_at ? new Date(progress.started_at).getTime() : null;
  const elapsedMs = startedAt ? Math.max(0, now - startedAt) : 0;

  const overall = progress?.overall ?? 0;
  const stageKey = progress?.stage ?? '';
  const current = STAGES.find((s) => s.key === stageKey);
  const currentIndex = STAGES.findIndex((s) => s.key === stageKey);

  // Extrapolate from what is done so far. Held back until 8 seconds and 3% in,
  // below which the estimate swings wildly and reads as noise.
  const remainingMs =
    running && overall > 0.03 && elapsedMs > 8000
      ? (elapsedMs / overall) * (1 - overall)
      : null;

  const pct = Math.round(overall * 100);

  return (
    <Card
      title={running ? 'Working…' : progress?.status === 'completed' ? 'Finished' : 'Ready'}
      right={
        running ? (
          <span className="text-tiny text-info tabular-nums">
            {duration(elapsedMs)} elapsed
            {remainingMs !== null && ` · about ${duration(remainingMs)} left`}
          </span>
        ) : progress?.stages?.total !== undefined ? (
          <span className="text-tiny text-faint tabular-nums">
            took {duration(progress.stages.total * 1000)}
          </span>
        ) : undefined
      }
    >
      {running && (
        <div className="mb-4">
          <div className="flex items-baseline gap-2 mb-1.5">
            <span className="text-2xl font-light text-info tabular-nums leading-none">
              {pct}%
            </span>
            <span className="text-xs text-muted">
              {current?.title ?? 'Starting up'}
            </span>
            {progress?.total ? (
              <span className="ml-auto text-tiny text-faint tabular-nums">
                {num(progress.processed ?? 0)} of {num(progress.total)} products
              </span>
            ) : null}
          </div>

          <div
            className="h-2 bg-line/60 rounded-full overflow-hidden"
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Run progress"
          >
            <div
              className="h-full bg-info rounded-full transition-[width] duration-500 ease-out"
              style={{ width: `${Math.max(pct, 1.5)}%` }}
            />
          </div>

          {progress?.detail && (
            <p className="text-tiny text-faint mt-1.5 truncate" title={progress.detail}>
              {progress.detail}
            </p>
          )}
        </div>
      )}

      {STAGES.map((stage, i) => {
        const seconds = progress?.stages?.[stage.key];
        const done = seconds !== undefined || (currentIndex >= 0 && i < currentIndex);
        const isCurrent = running && stageKey === stage.key;
        const showCount = isCurrent && !!progress?.total;

        return (
          <div
            key={stage.key}
            className="flex items-start gap-3 py-2.5 border-b border-hairline last:border-0"
          >
            <span
              className={`w-5 h-5 rounded-full grid place-items-center text-micro shrink-0
                          mt-px ${
                            done
                              ? 'bg-accent text-canvas'
                              : isCurrent
                                ? 'bg-info text-canvas animate-pulse'
                                : 'border border-line text-faint'
                          }`}
            >
              {done ? <Check size={11} aria-hidden /> : i + 1}
            </span>

            <div className="min-w-0 flex-1">
              <div
                className={`text-xs font-semibold ${
                  done || isCurrent ? 'text-ink' : 'text-faint'
                }`}
              >
                {stage.title}
              </div>
              <p className="text-tiny text-faint mt-0.5 leading-snug">{stage.what}</p>

              {showCount && (
                <div className="mt-1.5 h-[3px] bg-line/60 rounded-sm overflow-hidden max-w-xs">
                  <div
                    className="h-full bg-info rounded-sm transition-[width] duration-300"
                    style={{
                      width: `${((progress!.processed ?? 0) / progress!.total!) * 100}%`,
                    }}
                  />
                </div>
              )}
            </div>

            <span
              className={`text-tiny shrink-0 tabular-nums ${
                isCurrent ? 'text-info' : done ? 'text-muted' : 'text-faint'
              }`}
            >
              {showCount
                ? `${num(progress!.processed ?? 0)} / ${num(progress!.total!)}`
                : isCurrent
                  ? 'working'
                  : seconds !== undefined
                    ? `${seconds.toFixed(1)}s`
                    : done
                      ? 'done'
                      : 'waiting'}
            </span>
          </div>
        );
      })}
    </Card>
  );
}

function QualityGate({ progress }: { progress: RunProgress | null }) {
  const verdict = progress?.quality;
  if (!verdict) return null;

  const halted = progress?.status === 'halted';

  return (
    <Card
      title="Data check"
      accent={halted ? 'danger' : verdict === 'warn' ? 'accent' : 'info'}
      right={
        <span
          className={`text-tiny border rounded px-2 py-0.5 ${
            halted
              ? 'border-danger/40 text-danger'
              : verdict === 'warn'
                ? 'border-accent/40 text-accent'
                : 'border-info/40 text-info'
          }`}
        >
          {halted ? 'Failed — run stopped' : verdict === 'warn' ? 'Warnings' : 'Passed'}
        </span>
      }
    >
      <p className="text-xs text-muted leading-relaxed">
        {halted
          ? 'The check failed, so the run stopped before pricing anything. The system does not price on data it has judged unfit — a confident recommendation built on broken inputs is more dangerous than no recommendation.'
          : verdict === 'warn'
            ? 'Some inputs looked doubtful. The run went ahead, but the affected products cannot be approved automatically and their confidence is reduced.'
            : 'Freshness, completeness, structure, cross-references and outliers all checked out.'}
      </p>
    </Card>
  );
}

/**
 * The scheduled loop, collapsed. It is a real capability but it is not the
 * story, and leaving it expanded made the tab read as two competing screens.
 */
function ScheduleSection({
  loop,
  onChanged,
  action,
  runs,
}: {
  loop: LoopStatus | null;
  onChanged: () => void;
  action: ReturnType<typeof useActionState>;
  runs: import('../lib/types').RunRecord[];
}) {
  const [interval, setIntervalSeconds] = useState(300);
  const [scopeValue, setScopeValue] = useState(CATEGORIES[0]);
  const [objective, setObjective] = useState<Objective>('balanced');

  const loopRuns = runs.filter((r) => r.trigger === 'loop');

  const start = () =>
    action.run(async () => {
      await api.startLoop({
        interval_seconds: interval,
        scope_kind: 'category',
        scope_value: scopeValue,
        objective,
      });
      onChanged();
      return `Now re-pricing ${scopeValue} every ${interval} seconds.`;
    });

  const stop = () =>
    action.run(async () => {
      await api.stopLoop();
      onChanged();
      return 'Stopped. The round already in flight finishes cleanly.';
    });

  const nextIn =
    loop?.running && loop.started_at
      ? loop.interval_seconds -
        (((Date.now() - new Date(loop.started_at).getTime()) / 1000) % loop.interval_seconds)
      : 0;

  return (
    <Disclosure
      title="Run automatically on a schedule"
      hint={loop?.running ? `running · next in ${countdown(nextIn)}` : 'off'}
      right={
        loop?.running ? (
          <span className="text-tiny text-info">round {loop.iterations}</span>
        ) : undefined
      }
    >
      <div className="flex items-end gap-2.5">
        <div>
          <label className="label block mb-1" htmlFor="loop-scope">
            What to price
          </label>
          <select
            id="loop-scope"
            className="input w-40"
            value={scopeValue}
            onChange={(e) => setScopeValue(e.target.value)}
            disabled={loop?.running}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="label block mb-1" htmlFor="loop-interval">
            How often
          </label>
          <select
            id="loop-interval"
            className="input w-32"
            value={interval}
            onChange={(e) => setIntervalSeconds(Number(e.target.value))}
            disabled={loop?.running}
          >
            {[30, 60, 120, 300, 900, 1800].map((s) => (
              <option key={s} value={s}>
                {s < 60 ? `every ${s}s` : `every ${s / 60} min`}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="label block mb-1" htmlFor="loop-objective">
            Goal
          </label>
          <select
            id="loop-objective"
            className="input w-36"
            value={objective}
            onChange={(e) => setObjective(e.target.value as Objective)}
            disabled={loop?.running}
          >
            {OBJECTIVES.map((o) => (
              <option key={o} value={o}>
                {OBJECTIVE_LABEL[o]}
              </option>
            ))}
          </select>
        </div>

        {loop?.running ? (
          <button className="btn-danger h-[34px]" onClick={stop} disabled={action.busy}>
            Stop
          </button>
        ) : (
          <button className="btn-primary h-[34px]" onClick={start} disabled={action.busy}>
            Start
          </button>
        )}

        {loop?.running && (
          <span className="text-tiny text-faint pb-2 ml-2">
            Round {num(loop.iterations)} · next in {countdown(nextIn)} · started{' '}
            {datetime(loop.started_at)}
          </span>
        )}
      </div>

      {loop?.last_error && (
        <div className="mt-4">
          <ErrorNote>
            <strong>It stopped on a failed round.</strong> {loop.last_error} It did not
            retry — continuing to price on inputs that already failed once would be worse
            than stopping.
          </ErrorNote>
        </div>
      )}

      <p className="text-tiny text-faint mt-4 leading-relaxed">
        This runs inside the application, never on its own, and never after you close it.
        A failed round stops the schedule rather than retrying quietly. "Stop everything"
        in the top bar reaches it from any screen.
      </p>

      {loopRuns.length > 0 && (
        <div className="mt-4">
          <div className="label mb-1.5">Scheduled rounds</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left label border-b border-line">
                <th className="py-1.5">Started</th>
                <th>Scope</th>
                <th className="text-right">Products</th>
                <th className="text-right">Blocked</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {loopRuns.slice(0, 10).map((run) => (
                <tr key={run.run_id} className="border-b border-hairline last:border-0">
                  <td className="py-1.5 text-tiny text-faint">{datetime(run.started_at)}</td>
                  <td className="text-tiny text-muted">{run.scope_value ?? 'Everything'}</td>
                  <td className="text-right tabular-nums">{num(run.sku_count)}</td>
                  <td
                    className={`text-right tabular-nums ${
                      run.escalated ? 'text-danger' : 'text-faint'
                    }`}
                  >
                    {num(run.escalated ?? 0)}
                  </td>
                  <td
                    className={`text-tiny ${
                      run.status === 'completed' ? 'text-muted' : 'text-danger'
                    }`}
                  >
                    {run.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Disclosure>
  );
}
