import { NavLink } from 'react-router-dom';

/**
 * One navigation, read left to right as the story: what it does → run it →
 * decide on the results → look something up → what it was worth → prove it was
 * clean → (for engineers) how it works.
 *
 * The previous sidebar listed seven destinations in no particular order, which
 * left a viewer with no way to tell where to start. Order is the whole point of
 * this component.
 */

interface Tab {
  to: string;
  label: string;
  /** Shown under the label on hover, and as the accessible description. */
  hint: string;
}

const TABS: Tab[] = [
  { to: '/', label: 'Overview', hint: 'What this system does' },
  { to: '/run', label: 'Run', hint: 'Price a batch of products' },
  { to: '/review', label: 'Review', hint: 'Decide on each recommendation' },
  { to: '/products', label: 'Products', hint: 'Look up any product' },
  { to: '/impact', label: 'Impact', hint: 'What it was worth' },
  { to: '/audit', label: 'Audit', hint: 'What was blocked, and why' },
  {
    to: '/diagnostics',
    label: 'Diagnostics',
    hint: 'Cost, speed, reference documents and accuracy over time',
  },
];

export function TabBar({
  reviewCount,
  loopRunning,
}: {
  reviewCount: number;
  loopRunning: boolean;
}) {
  return (
    <nav
      aria-label="Main"
      className="h-11 shrink-0 border-b border-hairline bg-surface flex items-stretch px-4"
    >
      {TABS.map(({ to, label, hint }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          title={hint}
          className={({ isActive }) =>
            `relative flex items-center gap-2 px-4 text-xs transition-colors border-b-2 ${
              isActive
                ? 'border-b-accent text-ink font-semibold'
                : 'border-b-transparent text-faint hover:text-muted'
            }`
          }
        >
          {label}

          {to === '/run' && loopRunning && (
            <span
              className="w-1.5 h-1.5 rounded-full bg-info animate-pulse"
              aria-label="running on a schedule"
            />
          )}

          {to === '/review' && reviewCount > 0 && (
            <span className="text-micro text-info tabular-nums" aria-label={`${reviewCount} waiting`}>
              {reviewCount}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
