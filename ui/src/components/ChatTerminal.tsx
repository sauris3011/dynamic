import { Component, ErrorInfo, FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  MessageSquareText,
  Send,
  Sparkles,
  X,
} from 'lucide-react';

import { api } from '../lib/api';
import type { ChatReply, ChatStarter } from '../lib/types';
import { Pill } from './primitives';

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

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  errorText?: string;
}

class TurnErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, errorText: error?.message || 'Rendering error' };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Turn rendering error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex justify-start">
          <div className="max-w-[92%] rounded-xl rounded-bl-sm border border-danger/40 bg-danger-wash text-danger px-3 py-2.5 text-xs">
            {this.state.errorText || 'Unable to render message response.'}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const INTENT_LABEL: Record<string, string> = {
  product: 'Product',
  history: 'History',
  what_if: 'Scenario',
  analysis: 'Run analysis',
  platform: 'Platform',
  unsupported: 'Out of scope',
};

/**
 * The analyst's assistant.
 *
 * Every reply arrives with the evidence it was written from, and that evidence
 * is one click away rather than hidden behind a feedback form. The panel
 * overlays the workspace so opening it never reflows the operational view
 * underneath, and it passes the record currently on screen to the backend so
 * "why did this one escalate?" resolves to the recommendation being looked at.
 */
export function ChatTerminal() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [starters, setStarters] = useState<ChatStarter[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const location = useLocation();

  const context = useMemo(() => {
    const recMatch = location.pathname.match(/\/recommendation\/([^/]+)/);
    return {
      view: location.pathname === '/' ? 'run console' : location.pathname.slice(1),
      rec_id: recMatch ? decodeURIComponent(recMatch[1]) : undefined,
    };
  }, [location.pathname]);

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
  }, [turns, busy]);

  const send = async (question: string) => {
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
    setBusy(true);
    setTurns((current) => [
      ...current,
      { id: Date.now(), role: 'analyst', text },
    ]);
    try {
      const reply = await api.chat({ message: text, history, context });
      setTurns((current) => [...current, { id: Date.now() + 1, role: 'assistant', reply }]);
    } catch (error) {
      setTurns((current) => [
        ...current,
        {
          id: Date.now() + 1,
          role: 'assistant',
          error: error instanceof Error ? error.message : 'The assistant is unreachable.',
        },
      ]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void send(draft);
  };

  return (
    <div className="fixed bottom-12 right-6 z-40 flex flex-col items-end gap-3">
      {open && (
        <section
          className="w-[440px] h-[640px] max-h-[calc(100vh-8rem)] card shadow-2xl
                     overflow-hidden flex flex-col"
          aria-label="Analyst assistant"
        >
          <header className="h-14 shrink-0 px-4 border-b border-hairline flex items-center gap-3 bg-surface">
            <span className="w-8 h-8 rounded-full bg-info-wash border border-info/40 flex items-center justify-center text-info">
              <Bot size={16} aria-hidden />
            </span>
            <div className="min-w-0">
              <h2 className="text-sm font-bold text-ink">Analyst assistant</h2>
              <div className="text-micro text-faint tracking-wide uppercase truncate">
                Products · history · scenarios · runs
              </div>
            </div>
            <button
              type="button"
              className="ml-auto w-8 h-8 rounded-lg text-faint hover:text-ink hover:bg-line/30 flex items-center justify-center"
              onClick={() => setOpen(false)}
              aria-label="Close assistant"
            >
              <X size={16} aria-hidden />
            </button>
          </header>

          <div
            className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3 bg-canvas/40"
            aria-live="polite"
          >
            {turns.length === 0 && <Opening starters={starters} onPick={send} />}

            {turns.map((turn) => (
              <TurnErrorBoundary key={turn.id}>
                {turn.role === 'analyst' ? (
                  <div className="flex justify-end">
                    <div className="max-w-[86%] rounded-xl rounded-br-sm px-3 py-2.5 text-xs leading-relaxed border bg-accent-wash border-accent/40 text-ink">
                      {turn.text}
                    </div>
                  </div>
                ) : (
                  <Answer turn={turn} onPick={send} />
                )}
              </TurnErrorBoundary>
            ))}

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

          <form onSubmit={onSubmit} className="shrink-0 border-t border-hairline bg-surface p-3">
            <div className="flex items-center gap-2">
              <input
                ref={inputRef}
                className="input"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Ask about a product, its history, a price move, or a run"
                aria-label="Question for the analyst assistant"
                maxLength={600}
                disabled={busy}
              />
              <button
                className="w-9 h-9 shrink-0 rounded-lg border border-accent text-accent hover:bg-accent-wash flex items-center justify-center disabled:opacity-40"
                type="submit"
                disabled={!draft.trim() || busy}
                aria-label="Send question"
              >
                <Send size={15} aria-hidden />
              </button>
            </div>
            <p className="text-micro text-faint mt-2 px-0.5">
              Explains and projects only. Approving, overriding and pushing stay with you.
            </p>
          </form>
        </section>
      )}

      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className={`w-14 h-14 rounded-full shadow-xl border flex items-center justify-center transition-all ${
          open
            ? 'bg-raised border-line text-muted hover:text-ink'
            : 'bg-accent border-accent text-surface hover:brightness-110'
        }`}
        aria-label={open ? 'Close analyst assistant' : 'Open analyst assistant'}
        aria-expanded={open}
      >
        {open ? <X size={21} aria-hidden /> : <MessageSquareText size={22} aria-hidden />}
      </button>
    </div>
  );
}

function Opening({
  starters,
  onPick,
}: {
  starters: ChatStarter[];
  onPick: (question: string) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-line bg-raised px-3 py-2.5 text-xs leading-relaxed text-muted">
        I answer from what this platform already holds: the catalog and its trading
        history, price scenarios run through the same engine as the pipeline, and
        whatever your runs have decided. Every figure comes with the evidence behind it.
      </div>
      <div className="flex items-center gap-2 text-micro text-faint tracking-wider uppercase">
        <Sparkles size={12} aria-hidden />
        Start with one of these
      </div>
      <div className="flex flex-col gap-1.5">
        {starters.length === 0 && (
          <div className="text-xs text-faint">Loading suggestions…</div>
        )}
        {starters.map((starter) => (
          <button
            key={starter.question}
            type="button"
            onClick={() => onPick(starter.question)}
            className="text-left rounded-lg border border-line bg-surface px-3 py-2
                       hover:border-accent/60 hover:bg-accent-wash/40 transition-colors"
          >
            <div className="text-xs text-ink">{starter.label}</div>
            <div className="text-micro text-faint mt-0.5 leading-snug">
              {starter.question}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function FormattedInline({ text }: { text: string }) {
  if (typeof text !== 'string' || !text) return null;
  const parts = text.split(/(\*\*.*?\*\*|~~.*?~~|\*.*?\*|`.*?`)/g);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
          return <strong key={index} className="font-semibold text-ink">{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith('~~') && part.endsWith('~~') && part.length > 4) {
          return <span key={index} className="line-through text-faint opacity-80">{part.slice(2, -2)}</span>;
        }
        if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
          return <em key={index} className="italic">{part.slice(1, -1)}</em>;
        }
        if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
          return <code key={index} className="px-1 py-0.5 rounded bg-surface border border-line font-mono text-micro">{part.slice(1, -1)}</code>;
        }
        return part;
      })}
    </>
  );
}

function FormattedText({ text }: { text: string }) {
  if (typeof text !== 'string' || !text) return null;
  const lines = text.split('\n');

  return (
    <div className="space-y-1.5">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={index} className="h-0.5" />;

        if (trimmed.startsWith('# ')) {
          return (
            <h1 key={index} className="text-xs font-bold text-ink border-b border-hairline pb-1 mt-1 mb-1 uppercase tracking-wide">
              <FormattedInline text={trimmed.slice(2)} />
            </h1>
          );
        }
        if (trimmed.startsWith('## ')) {
          return (
            <h2 key={index} className="text-xs font-bold text-ink mt-1.5 mb-0.5">
              <FormattedInline text={trimmed.slice(3)} />
            </h2>
          );
        }
        if (trimmed.startsWith('### ')) {
          return (
            <h3 key={index} className="text-xs font-semibold text-ink mt-1 mb-0.5">
              <FormattedInline text={trimmed.slice(4)} />
            </h3>
          );
        }

        if (/^[-*]\s+/.test(trimmed)) {
          const content = trimmed.replace(/^[-*]\s+/, '');
          return (
            <div key={index} className="flex items-start gap-2 pl-1 text-xs text-muted">
              <span className="text-accent mt-1.5 w-1 h-1 rounded-full bg-accent shrink-0" aria-hidden />
              <div className="flex-1 leading-normal">
                <FormattedInline text={content} />
              </div>
            </div>
          );
        }

        const numMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
        if (numMatch) {
          return (
            <div key={index} className="flex items-start gap-1.5 pl-1 text-xs text-muted">
              <span className="font-semibold text-accent text-micro shrink-0 mt-0.5">{numMatch[1]}.</span>
              <div className="flex-1 leading-normal">
                <FormattedInline text={numMatch[2]} />
              </div>
            </div>
          );
        }

        return (
          <p key={index} className="text-xs text-muted leading-relaxed">
            <FormattedInline text={line} />
          </p>
        );
      })}
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
          className="max-w-[92%] rounded-xl rounded-bl-sm border border-danger/40 bg-danger-wash text-danger px-3 py-2.5 text-xs"
        >
          {turn.error}
        </div>
      </div>
    );
  }
  if (!reply) return null;

  const keyPoints = Array.isArray(reply.key_points) ? reply.key_points : [];
  const caveats = Array.isArray(reply.caveats) ? reply.caveats : [];
  const skus = Array.isArray(reply.scope?.skus) ? reply.scope.skus : [];
  const unsupported = Array.isArray(reply.unsupported_figures) ? reply.unsupported_figures : [];
  const facts = Array.isArray(reply.facts) ? reply.facts : [];
  const citations = Array.isArray(reply.citations) ? reply.citations : [];
  const sources = Array.isArray(reply.sources) ? reply.sources : [];
  const suggestions = Array.isArray(reply.suggestions) ? reply.suggestions : [];

  return (
    <div className="space-y-2">
      <div className="flex justify-start">
        <div className="max-w-[92%] rounded-xl rounded-bl-sm px-3 py-2.5 text-xs border bg-raised border-line text-muted">
          <FormattedText text={reply.answer || ''} />
        </div>
      </div>

      {keyPoints.length > 0 && (
        <ul className="ml-1 space-y-1">
          {keyPoints.map((point, index) => {
            const strPoint = typeof point === 'string' ? point : String(point ?? '');
            return (
              <li key={`${index}-${strPoint.slice(0, 20)}`} className="text-xs text-muted flex gap-2">
                <span className="text-accent mt-1.5 w-1 h-1 rounded-full bg-accent shrink-0" aria-hidden />
                <div className="flex-1">
                  <FormattedInline text={strPoint} />
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {caveats.map((caveat, index) => {
        const strCaveat = typeof caveat === 'string' ? caveat : String(caveat ?? '');
        return (
          <div
            key={`${index}-${strCaveat.slice(0, 20)}`}
            className="flex gap-2 items-start text-micro text-faint border border-dashed border-line rounded-lg px-2.5 py-2"
          >
            <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden />
            <span><FormattedInline text={strCaveat} /></span>
          </div>
        );
      })}

      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone="info">{INTENT_LABEL[reply.intent] ?? reply.intent}</Pill>
        {skus.map((sku, index) => {
          const strSku = typeof sku === 'string' ? sku : String(sku ?? '');
          return <Pill key={`${index}-${strSku}`}>{strSku}</Pill>;
        })}
        {!reply.narrated && <Pill>computed evidence</Pill>}
        {unsupported.length > 0 && (
          <Pill tone="danger">{unsupported.length} unverified figure(s)</Pill>
        )}
        {facts.length > 0 && (
          <button
            type="button"
            onClick={() => setOpenEvidence((current) => !current)}
            className="text-tiny text-faint hover:text-ink flex items-center gap-1"
            aria-expanded={openEvidence}
          >
            {openEvidence ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
            Evidence ({facts.length})
          </button>
        )}
      </div>

      {openEvidence && (
        <div className="rounded-lg border border-line bg-surface px-3 py-2.5 space-y-2">
          <ul className="space-y-1">
            {facts.map((fact, index) => {
              const strFact = typeof fact === 'string' ? fact : String(fact ?? '');
              return (
                <li key={`${index}-${strFact.slice(0, 24)}`} className="text-micro text-muted leading-snug">
                  <FormattedInline text={strFact} />
                </li>
              );
            })}
          </ul>
          {citations.length > 0 && (
            <div className="border-t border-hairline pt-2 space-y-1">
              <div className="label">Cited sources</div>
              {citations.map((citation, index) => {
                if (!citation) return null;
                const id = citation.id || `Citation-${index + 1}`;
                const excerpt = citation.excerpt || '';
                return (
                  <div key={id} className="text-micro text-faint">
                    <span className="text-muted">{id}</span> — {excerpt}
                  </div>
                );
              })}
            </div>
          )}
          <div className="border-t border-hairline pt-2 text-micro text-faint">
            {sources.filter(Boolean).map(String).join(' · ')}
            {reply.model && ` · ${reply.model}`}
            {` · ${reply.latency_ms || 0} ms`}
            {typeof reply.tokens === 'number' && reply.tokens > 0 && ` · ${reply.tokens.toLocaleString()}`}
          </div>
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {suggestions.map((suggestion, index) => {
            const strSugg = typeof suggestion === 'string' ? suggestion : String(suggestion ?? '');
            if (!strSugg) return null;
            return (
              <button
                key={`${index}-${strSugg}`}
                type="button"
                onClick={() => onPick(strSugg)}
                className="text-tiny text-faint border border-line rounded-md px-2 py-1
                           hover:text-ink hover:border-accent/60 transition-colors text-left"
              >
                {strSugg}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
