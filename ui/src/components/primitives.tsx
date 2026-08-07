import type { ReactNode } from 'react';

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
