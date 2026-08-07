import { useId } from 'react';
import { Info } from 'lucide-react';

/**
 * An explanation available on demand, without spending permanent screen space.
 *
 * Opens on **hover and on keyboard focus**. Hover-only tooltips are invisible to
 * anyone navigating by keyboard, and this app's review queue is explicitly
 * keyboard-navigable (FR-070) — so the trigger is a real `<button>` rather than
 * a decorated `<span>`, which gets focus and Enter/Space for free.
 *
 * Rendered as a sibling that appears on `group-hover`/`group-focus-within`
 * rather than through a positioning library: the content is short, the anchor
 * never sits inside a scroll container, and a floating-element dependency would
 * be a lot of machinery for a paragraph of text.
 *
 * The icon is Lucide, never an emoji (FR-063).
 */
export function InfoTip({
  label,
  children,
  align = 'left',
}: {
  /** What the tooltip explains, e.g. "MARGIN_FLOOR". Used for the accessible name. */
  label: string;
  children: React.ReactNode;
  align?: 'left' | 'right';
}) {
  const id = useId();

  return (
    <span className="relative inline-flex group align-middle">
      <button
        type="button"
        aria-label={`What is ${label}?`}
        aria-describedby={id}
        // The tooltip is the whole point; clicking should not submit or navigate.
        onClick={(e) => e.preventDefault()}
        className="text-faint hover:text-info focus-visible:text-info transition-colors
                   inline-flex items-center justify-center"
      >
        <Info size={12} aria-hidden />
      </button>
      <span
        id={id}
        role="tooltip"
        className={`pointer-events-none absolute top-full mt-1.5 z-30 w-64
                    rounded-lg border border-line bg-surface px-3 py-2
                    text-tiny leading-relaxed text-muted shadow-lg
                    opacity-0 invisible transition-opacity duration-100
                    group-hover:opacity-100 group-hover:visible
                    group-focus-within:opacity-100 group-focus-within:visible
                    ${align === 'right' ? 'right-0' : 'left-0'}`}
      >
        {children}
      </span>
    </span>
  );
}
