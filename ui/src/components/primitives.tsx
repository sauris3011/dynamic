import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronRight, Search, X } from 'lucide-react';

/** Shared layout and display primitives. No business logic lives here. */

export function Card({
  title,
  right,
  accent,
  children,
  className = '',
}: {
  title?: ReactNode;
  right?: ReactNode;
  accent?: 'accent' | 'info' | 'danger' | 'none';
  children: ReactNode;
  className?: string;
}) {
  const edge = {
    accent: 'border-l-2 border-l-accent',
    info: 'border-l-2 border-l-info',
    danger: 'border-l-2 border-l-danger',
    none: '',
  }[accent ?? 'none'];

  return (
    <section className={`card ${edge} p-4 ${className}`}>
      {(title || right) && (
        <header className="flex items-center gap-3 mb-3">
          {title && <h2 className="text-[15px] font-bold text-ink">{title}</h2>}
          {right && <div className="ml-auto">{right}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  detail,
  tone = 'ink',
  accent,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: 'ink' | 'accent' | 'info' | 'danger';
  accent?: 'accent' | 'info' | 'danger' | 'none';
}) {
  const colour = {
    ink: 'text-ink',
    accent: 'text-accent',
    info: 'text-info',
    danger: 'text-danger',
  }[tone];
  const edge = {
    accent: 'border-l-2 border-l-accent',
    info: 'border-l-2 border-l-info',
    danger: 'border-l-2 border-l-danger',
    none: 'border-l-2 border-l-line',
  }[accent ?? 'none'];

  return (
    <div className={`card ${edge} px-4 py-3.5`}>
      <div className="label">{label}</div>
      <div className={`stat mt-1 ${colour}`}>{value}</div>
      {detail && <div className="text-tiny text-faint mt-0.5">{detail}</div>}
    </div>
  );
}

export function SectionTitle({ eyebrow, title, lede }: {
  eyebrow: string;
  title: string;
  lede?: string;
}) {
  return (
    <div>
      <div className="flex items-center gap-2.5 mb-2">
        <span className="w-8 h-px bg-info" aria-hidden />
        <span className="label">{eyebrow}</span>
      </div>
      <h1 className="text-2xl font-bold text-ink leading-tight">{title}</h1>
      {lede && <p className="text-[13px] text-faint mt-1.5 max-w-3xl">{lede}</p>}
    </div>
  );
}

export function Meter({ value, tone = 'info' }: { value: number; tone?: string }) {
  const colour =
    { accent: 'bg-accent', info: 'bg-info', danger: 'bg-danger' }[tone] ?? 'bg-info';
  return (
    <div className="h-[3px] bg-line/60 rounded-sm mt-1.5 overflow-hidden">
      <div
        className={`h-full rounded-sm ${colour}`}
        style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }}
      />
    </div>
  );
}

export function Pill({
  children,
  tone = 'muted',
}: {
  children: ReactNode;
  tone?: 'muted' | 'accent' | 'info' | 'danger';
}) {
  const styles = {
    muted: 'border-line text-faint',
    accent: 'border-accent/50 text-accent bg-accent-wash',
    info: 'border-info/40 text-info bg-info-wash',
    danger: 'border-danger/40 text-danger bg-danger-wash',
  }[tone];
  return (
    <span className={`text-tiny border rounded-md px-2 py-0.5 ${styles}`}>{children}</span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="border border-dashed border-line rounded-xl p-8 text-center text-xs text-faint">
      {children}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="border border-danger/40 bg-danger-wash text-danger rounded-lg px-3.5 py-2.5 text-xs"
    >
      {children}
    </div>
  );
}

export function Toast({
  message,
  onDismiss,
}: {
  message: { text: string; kind: 'ok' | 'error' } | null;
  onDismiss: () => void;
}) {
  if (!message) return null;
  const tone =
    message.kind === 'ok'
      ? 'border-info/40 bg-info-wash text-info'
      : 'border-danger/40 bg-danger-wash text-danger';
  return (
    <div
      role="status"
      className={`fixed bottom-12 right-6 z-50 max-w-md border rounded-lg px-4 py-3 text-xs ${tone}`}
    >
      <div className="flex items-start gap-3">
        <span className="flex-1">{message.text}</span>
        <button onClick={onDismiss} className="text-faint hover:text-ink" aria-label="Dismiss">
          Close
        </button>
      </div>
    </div>
  );
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5 text-xs text-faint py-6">
      <span
        className="w-3 h-3 border-2 border-line border-t-info rounded-full animate-spin"
        aria-hidden
      />
      {label}
    </div>
  );
}

/**
 * A labelled number with an optional sub-line. Three separate copies of this
 * existed as route-local components before; it is the same thing every time.
 */
export function Figure({
  label,
  value,
  detail,
  tone = 'ink',
  size = 'md',
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: 'ink' | 'accent' | 'info' | 'danger' | 'muted';
  size?: 'sm' | 'md' | 'lg';
}) {
  const colour = {
    ink: 'text-ink',
    accent: 'text-accent',
    info: 'text-info',
    danger: 'text-danger',
    muted: 'text-muted',
  }[tone];
  const scale = { sm: 'text-sm', md: 'text-lg', lg: 'text-3xl' }[size];

  return (
    <div>
      <div className="label">{label}</div>
      <div className={`${scale} font-semibold tabular-nums mt-0.5 ${colour}`}>{value}</div>
      {detail && <div className="text-tiny text-faint mt-0.5 leading-snug">{detail}</div>}
    </div>
  );
}

/**
 * A collapsed section. The redesign leans on this heavily: anything an engineer
 * would want but a stakeholder would not is present and one click away, rather
 * than either deleted or permanently on screen.
 */
export function Disclosure({
  title,
  hint,
  defaultOpen = false,
  right,
  children,
}: {
  title: ReactNode;
  hint?: string;
  defaultOpen?: boolean;
  right?: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();

  return (
    <div className="border border-hairline rounded-xl overflow-hidden">
      <div className="flex items-center gap-3 bg-surface">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-controls={panelId}
          className="flex-1 flex items-center gap-2 px-4 py-3 text-left hover:bg-raised
                     transition-colors min-w-0"
        >
          <ChevronRight
            size={13}
            aria-hidden
            className={`shrink-0 text-faint transition-transform ${open ? 'rotate-90' : ''}`}
          />
          <span className="text-xs font-semibold text-ink truncate">{title}</span>
          {hint && <span className="text-tiny text-faint truncate">{hint}</span>}
        </button>
        {right && <div className="pr-4 shrink-0">{right}</div>}
      </div>
      {open && (
        <div id={panelId} className="px-4 py-4 border-t border-hairline">
          {children}
        </div>
      )}
    </div>
  );
}

/**
 * Debounced search input. Reports through `onChange` after the user stops
 * typing, so a keystroke never triggers a filter pass or a request; `value` is
 * the committed term and the box keeps its own draft.
 */
export function SearchBox({
  value,
  onChange,
  placeholder = 'Search',
  delayMs = 200,
  className = '',
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  delayMs?: number;
  className?: string;
}) {
  const [draft, setDraft] = useState(value);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // Accept resets from the parent (a cleared filter, a restored URL) without
  // fighting the user mid-keystroke.
  useEffect(() => setDraft(value), [value]);

  useEffect(() => {
    if (draft === value) return;
    const timer = setTimeout(() => onChangeRef.current(draft), delayMs);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, delayMs]);

  return (
    <div className={`relative ${className}`}>
      <Search
        size={13}
        aria-hidden
        className="absolute left-3 top-1/2 -translate-y-1/2 text-faint pointer-events-none"
      />
      <input
        type="search"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="input w-full pl-8 pr-8"
      />
      {draft && (
        <button
          type="button"
          onClick={() => setDraft('')}
          aria-label="Clear search"
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-faint hover:text-ink"
        >
          <X size={13} aria-hidden />
        </button>
      )}
    </div>
  );
}

/**
 * Table chrome: sticky head, consistent row rules, and — the part that matters —
 * an honest footer when the view is showing fewer rows than exist. A silently
 * truncated list is worse than no list.
 */
export function DataTable({
  head,
  children,
  shown,
  total,
  narrowHint = 'narrow it with search',
  maxHeight,
}: {
  head: ReactNode;
  children: ReactNode;
  shown?: number;
  total?: number;
  narrowHint?: string;
  maxHeight?: string;
}) {
  const truncated = shown !== undefined && total !== undefined && shown < total;

  // `h-full` + `flex-1 min-h-0` lets the scroll area fill a height-constrained
  // parent (the Review screen fills the viewport) while still collapsing to its
  // content when the parent has no height of its own (a card on Products).
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div
        className="flex-1 min-h-0 overflow-auto"
        style={maxHeight ? { maxHeight } : undefined}
      >
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface z-10">
            <tr className="text-left label border-b border-line">{head}</tr>
          </thead>
          <tbody>{children}</tbody>
        </table>
      </div>
      {truncated && (
        <div className="shrink-0 text-tiny text-faint pt-2.5 border-t border-hairline mt-1">
          Showing {shown!.toLocaleString('en-GB')} of {total!.toLocaleString('en-GB')} —{' '}
          {narrowHint}.
        </div>
      )}
    </div>
  );
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="mt-2">
      {rows.map(([key, value]) => (
        <div key={key} className="flex gap-4 py-1.5 border-b border-hairline last:border-0">
          <dt className="text-xs text-faint">{key}</dt>
          <dd className="ml-auto text-xs text-muted text-right">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
