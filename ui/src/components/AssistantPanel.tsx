import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useLocation } from 'react-router-dom';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Mic,
  Send,
  Sparkles,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react';

import { api } from '../lib/api';
import { Pill } from './primitives';
import {
  canListen,
  canSpeak,
  listen,
  listenUnavailableReason,
  speak,
  stopSpeaking,
  type Listener,
} from '../lib/speech';
import type { ChatIntent, ChatReply, ChatStarter } from '../lib/types';

/**
 * The analyst's assistant, docked rather than floating.
 *
 * It used to be a 440x640 overlay anchored to the bottom-right corner, which
 * covered whatever was underneath it — on the review screen that was the
 * decision panel, which is the worst possible thing to hide. Docking it means
 * the page reflows to make room: the assistant takes width from the layout
 * instead of painting over it, so nothing is ever obscured and the width is the
 * reader's to set.
 *
 * Every reply arrives with the evidence it was written from, one click away
 * rather than behind a feedback form.
 */

const INTENT_LABEL: Record<ChatIntent, string> = {
  product: 'Product',
  history: 'History',
  what_if: 'Scenario',
  analysis: 'Run analysis',
  platform: 'Platform',
  unsupported: 'Out of scope',
};

/**
 * React keys for turns. A timestamp collides when two turns land in the same
 * millisecond — which a question and its error reply routinely do.
 */
let turnCounter = 0;
const nextId = () => ++turnCounter;

/** First path segment -> the name of the screen, for the backend's context. */
const VIEW_LABEL: Record<string, string> = {
  '': 'overview',
  run: 'run',
  review: 'review and decide',
  products: 'products',
  impact: 'business impact',
  audit: 'audit',
  diagnostics: 'diagnostics',
};

/**
 * Capsules offered before the backend's own suggestions arrive, and as a
 * fallback if that call fails. Keyed by screen so what is offered relates to
 * what is being looked at.
 */
const CAPSULES: Record<string, string[]> = {
  '': [
    'What did the last run change?',
    'How much is this worth so far?',
    'What is waiting for me?',
  ],
  run: [
    'What did the last run change?',
    'Why were some products blocked?',
    'How long does a run usually take?',
  ],
  review: [
    'Why did this one stop?',
    'Which of these matters most?',
    'What happens if I approve it?',
  ],
  products: [
    'How has this product been selling?',
    'What would a 5% cut do here?',
    'Why is this price where it is?',
  ],
  impact: [
    'How are we doing against the old way?',
    'Which categories gained most?',
    'How accurate have the forecasts been?',
  ],
  audit: [
    'What was blocked this week, and why?',
    'What has the system decided on its own?',
    'Show me the last mode change.',
  ],
  diagnostics: [
    'What is costing the most?',
    'Is the forecast getting better?',
    'Which stage takes longest?',
  ],
};

interface AnalystTurn {
  id: number;
  role: 'analyst';
  text: string;
}
interface AssistantTurn {
  id: number;
  role: 'assistant';
  reply?: ChatReply;
  error?: string;
}
type Turn = AnalystTurn | AssistantTurn;

export function AssistantPanel({
  open,
  onClose,
  width,
  onResize,
}: {
  open: boolean;
  onClose: () => void;
  width: number;
  onResize: (next: number) => void;
}) {
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [starters, setStarters] = useState<ChatStarter[]>([]);

  // Voice
  const [listening, setListening] = useState(false);
  const [heard, setHeard] = useState('');
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const listenerRef = useRef<Listener | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const location = useLocation();

  const segment = location.pathname.split('/')[1] ?? '';

  /**
   * What the analyst is looking at, so "why did this one stop?" resolves
   * against the record on screen rather than the whole catalog. `rec_id` comes
   * from the Review route, `sku` from the Products route — both are in the URL
   * precisely so this can read them without the screens plumbing state down.
   */
  const context = useMemo(() => {
    const recMatch = location.pathname.match(/\/review\/([^/]+)/);
    const sku = new URLSearchParams(location.search).get('sku');
    return {
      view: VIEW_LABEL[segment] ?? 'overview',
      rec_id: recMatch ? decodeURIComponent(recMatch[1]) : undefined,
      sku: sku ?? undefined,
    };
  }, [location.pathname, location.search, segment]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open || starters.length) return;
    let cancelled = false;
    void api
      .chatSuggestions()
      .then((body) => {
        if (!cancelled) setStarters(body.suggestions);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open, starters.length]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns, busy, heard]);

  // Stop the microphone and the voice when the panel closes or unmounts —
  // a assistant that keeps listening to a closed panel is a bug and a
  // privacy problem.
  useEffect(() => {
    if (open) return;
    listenerRef.current?.stop();
    listenerRef.current = null;
    setListening(false);
    setHeard('');
    stopSpeaking();
  }, [open]);

  useEffect(
    () => () => {
      listenerRef.current?.stop();
      stopSpeaking();
    },
    [],
  );

  const send = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || busy) return;

      const history = turns
        .map((turn) =>
          turn.role === 'analyst'
            ? { role: 'analyst' as const, text: turn.text }
            : { role: 'assistant' as const, text: turn.reply?.answer ?? '' },
        )
        .filter((turn) => turn.text)
        .slice(-8);

      setDraft('');
      setHeard('');
      setBusy(true);
      setTurns((current) => [...current, { id: nextId(), role: 'analyst', text }]);

      try {
        const reply = await api.chat({ message: text, history, context });
        setTurns((current) => [...current, { id: nextId(), role: 'assistant', reply }]);
        if (!muted) speak(reply.headline || reply.answer);
      } catch (error) {
        const message =
          error instanceof Error ? error.message : 'The assistant is unreachable.';
        setTurns((current) => [
          ...current,
          { id: nextId(), role: 'assistant', error: message },
        ]);
      } finally {
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [busy, turns, context, muted],
  );

  /**
   * Hands-free: the phrase appears on screen as it is spoken, and goes as soon
   * as the speaker stops. Pressing the button again cancels instead of sending,
   * which is the escape hatch for a question that came out wrong.
   */
  const toggleMic = () => {
    setVoiceError(null);

    if (listening) {
      listenerRef.current?.stop();
      listenerRef.current = null;
      setListening(false);
      setHeard('');
      return;
    }

    stopSpeaking();
    setHeard('');
    setListening(true);

    listenerRef.current = listen({
      onPartial: setHeard,
      onFinal: (text) => {
        setHeard('');
        void send(text);
      },
      onError: (message) => {
        setVoiceError(message);
        setHeard('');
      },
      onEnd: () => {
        setListening(false);
        listenerRef.current = null;
      },
    });

    if (!listenerRef.current) setListening(false);
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void send(draft);
  };

  // Drag-to-resize. Pointer events rather than mouse so a stylus or touch drag
  // behaves, and capture so the drag survives the pointer leaving the handle.
  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);

    const move = (e: PointerEvent) => onResize(window.innerWidth - e.clientX);
    const up = () => {
      handle.releasePointerCapture(event.pointerId);
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
  };

  if (!open) return null;

  const micSupported = canListen();
  const capsules = CAPSULES[segment] ?? CAPSULES[''];
  const suggested = starters.length
    ? starters.slice(0, 4).map((s) => s.question)
    : capsules;

  return (
    <aside
      className="shrink-0 border-l border-line bg-surface flex flex-col min-h-0 relative"
      style={{ width }}
      aria-label="Analyst assistant"
    >
      <div
        role="separator"
        aria-label="Resize the assistant"
        aria-orientation="vertical"
        onPointerDown={startResize}
        className="absolute left-0 top-0 bottom-0 w-1 -ml-0.5 cursor-col-resize
                   hover:bg-accent/40 active:bg-accent/60 z-10"
      />

      <header className="h-11 shrink-0 px-3.5 border-b border-hairline flex items-center gap-2.5">
        <Sparkles size={14} className="text-accent shrink-0" aria-hidden />
        <h2 className="text-xs font-bold text-ink">Ask</h2>

        {listening && (
          <span className="flex items-center gap-1.5 text-micro text-danger">
            <span className="w-1.5 h-1.5 rounded-full bg-danger animate-pulse" aria-hidden />
            Listening
          </span>
        )}

        {canSpeak() && (
          <button
            type="button"
            onClick={() => {
              setMuted((m) => !m);
              stopSpeaking();
            }}
            className="ml-auto w-7 h-7 rounded-lg text-faint hover:text-ink hover:bg-line/30
                       flex items-center justify-center"
            aria-label={muted ? 'Turn spoken answers on' : 'Turn spoken answers off'}
            aria-pressed={muted}
            title={muted ? 'Answers are silent' : 'Answers are read aloud'}
          >
            {muted ? <VolumeX size={14} aria-hidden /> : <Volume2 size={14} aria-hidden />}
          </button>
        )}

        <button
          type="button"
          onClick={onClose}
          className={`w-7 h-7 rounded-lg text-faint hover:text-ink hover:bg-line/30
                     flex items-center justify-center ${canSpeak() ? '' : 'ml-auto'}`}
          aria-label="Close the assistant"
        >
          <X size={14} aria-hidden />
        </button>
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto p-3.5 space-y-3" aria-live="polite">
        {turns.length === 0 && <Opening />}

        {turns.map((turn) =>
          turn.role === 'analyst' ? (
            <div key={turn.id} className="flex justify-end">
              <div
                className="max-w-[88%] rounded-xl rounded-br-sm px-3 py-2 text-xs
                           leading-relaxed border bg-accent-wash border-accent/40 text-ink"
              >
                {turn.text}
              </div>
            </div>
          ) : (
            <Answer key={turn.id} turn={turn} onPick={send} />
          ),
        )}

        {/* The words as they are spoken — this is the "intercepted and shown
            on screen" part, and it is what makes voice feel answerable rather
            than a black box. */}
        {heard && (
          <div className="flex justify-end">
            <div
              className="max-w-[88%] rounded-xl rounded-br-sm px-3 py-2 text-xs leading-relaxed
                         border border-dashed border-danger/50 text-muted italic"
            >
              {heard}
            </div>
          </div>
        )}

        {busy && (
          <div className="flex items-center gap-2.5 text-xs text-faint">
            <span
              className="w-3 h-3 border-2 border-line border-t-info rounded-full animate-spin"
              aria-hidden
            />
            Gathering the evidence
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="shrink-0 border-t border-hairline p-3 space-y-2.5">
        {voiceError && (
          <p role="alert" className="text-micro text-danger leading-snug">
            {voiceError}
          </p>
        )}

        {/* Capsules. Kept above the box at all times, not only on an empty
            transcript — the second question is the one people struggle to
            think of, not the first. */}
        <div className="flex flex-wrap gap-1.5">
          {suggested.map((question) => (
            <button
              key={question}
              type="button"
              onClick={() => void send(question)}
              disabled={busy}
              className="text-micro text-faint border border-line rounded-full px-2.5 py-1
                         hover:text-ink hover:border-accent/60 hover:bg-accent-wash/40
                         transition-colors disabled:opacity-40 text-left"
            >
              {question}
            </button>
          ))}
        </div>

        <form onSubmit={onSubmit} className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleMic}
            disabled={busy || !micSupported}
            title={micSupported ? 'Ask out loud' : listenUnavailableReason()}
            aria-label={listening ? 'Stop listening' : 'Ask out loud'}
            aria-pressed={listening}
            className={`w-9 h-9 shrink-0 rounded-lg border flex items-center justify-center
                        transition-colors disabled:opacity-40 ${
                          listening
                            ? 'border-danger bg-danger-wash text-danger animate-pulse'
                            : 'border-line text-faint hover:text-ink hover:border-accent/60'
                        }`}
          >
            <Mic size={15} aria-hidden />
          </button>

          <input
            ref={inputRef}
            className="input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={listening ? 'Listening…' : 'Ask about a product, a price or a run'}
            aria-label="Question for the assistant"
            maxLength={600}
            disabled={busy || listening}
          />

          <button
            className="w-9 h-9 shrink-0 rounded-lg border border-accent text-accent
                       hover:bg-accent-wash flex items-center justify-center disabled:opacity-40"
            type="submit"
            disabled={!draft.trim() || busy}
            aria-label="Send"
          >
            <Send size={15} aria-hidden />
          </button>
        </form>

        <p className="text-micro text-faint leading-snug">
          Explains and projects only. Approving, overriding and sending prices stay with
          you.
        </p>
      </div>
    </aside>
  );
}

function Opening() {
  return (
    <div
      className="rounded-xl border border-line bg-raised px-3 py-2.5 text-xs
                 leading-relaxed text-muted"
    >
      I answer from what this platform already holds: the catalog and its trading history,
      price scenarios run through the same engine as the pipeline, and whatever your runs
      have decided. Every figure comes with the evidence behind it. Type a question, tap a
      suggestion, or press the microphone and just ask.
    </div>
  );
}

function Answer({ turn, onPick }: { turn: AssistantTurn; onPick: (q: string) => void }) {
  const [openEvidence, setOpenEvidence] = useState(false);
  const reply = turn.reply;

  if (turn.error) {
    return (
      <div className="flex justify-start">
        <div
          role="alert"
          className="max-w-[94%] rounded-xl rounded-bl-sm border border-danger/40
                     bg-danger-wash text-danger px-3 py-2 text-xs"
        >
          {turn.error}
        </div>
      </div>
    );
  }
  if (!reply) return null;

  return (
    <div className="space-y-2">
      <div className="flex justify-start">
        <div
          className="max-w-[94%] rounded-xl rounded-bl-sm px-3 py-2 text-xs leading-relaxed
                     border bg-raised border-line text-muted whitespace-pre-line"
        >
          {reply.answer}
        </div>
      </div>

      {reply.key_points.length > 0 && (
        <ul className="ml-1 space-y-1">
          {reply.key_points.map((point) => (
            <li key={point} className="text-xs text-muted flex gap-2">
              <span
                className="text-accent mt-1.5 w-1 h-1 rounded-full bg-accent shrink-0"
                aria-hidden
              />
              {point}
            </li>
          ))}
        </ul>
      )}

      {reply.caveats.map((caveat) => (
        <div
          key={caveat}
          className="flex gap-2 items-start text-micro text-faint border border-dashed
                     border-line rounded-lg px-2.5 py-2"
        >
          <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden />
          <span>{caveat}</span>
        </div>
      ))}

      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone="info">{INTENT_LABEL[reply.intent] ?? reply.intent}</Pill>
        {reply.scope.skus.map((sku) => (
          <Pill key={sku}>{sku}</Pill>
        ))}
        {!reply.narrated && <Pill>computed evidence</Pill>}
        {reply.unsupported_figures.length > 0 && (
          <Pill tone="danger">{reply.unsupported_figures.length} unverified figure(s)</Pill>
        )}
        {reply.facts.length > 0 && (
          <button
            type="button"
            onClick={() => setOpenEvidence((current) => !current)}
            className="text-tiny text-faint hover:text-ink flex items-center gap-1"
            aria-expanded={openEvidence}
          >
            {openEvidence ? (
              <ChevronDown size={12} aria-hidden />
            ) : (
              <ChevronRight size={12} aria-hidden />
            )}
            Evidence ({reply.facts.length})
          </button>
        )}
      </div>

      {openEvidence && (
        <div className="rounded-lg border border-line bg-surface px-3 py-2.5 space-y-2">
          <ul className="space-y-1">
            {reply.facts.map((fact, index) => (
              <li
                key={`${index}-${fact.slice(0, 24)}`}
                className="text-micro text-muted leading-snug"
              >
                {fact}
              </li>
            ))}
          </ul>
          {reply.citations.length > 0 && (
            <div className="border-t border-hairline pt-2 space-y-1">
              <div className="label">Cited sources</div>
              {reply.citations.map((citation) => (
                <div key={citation.id} className="text-micro text-faint">
                  <span className="text-muted">{citation.id}</span> — {citation.excerpt}
                </div>
              ))}
            </div>
          )}
          <div className="border-t border-hairline pt-2 text-micro text-faint">
            {reply.sources.join(' · ')}
            {reply.model && ` · ${reply.model}`}
            {` · ${reply.latency_ms} ms`}
            {reply.tokens > 0 && ` · ${reply.tokens.toLocaleString()} tokens`}
          </div>
        </div>
      )}

      {reply.suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {reply.suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => onPick(suggestion)}
              className="text-tiny text-faint border border-line rounded-full px-2.5 py-1
                         hover:text-ink hover:border-accent/60 transition-colors text-left"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
