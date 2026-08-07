import { NavLink } from 'react-router-dom';
import {
  ClipboardCheck,
  FlaskConical,
  GaugeCircle,
  ScrollText,
  Repeat,
  PackageSearch,
  Terminal,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { RagPanel } from './RagPanel';
import type { LoopStatus } from '../lib/types';

/** Workflow navigation plus the universal RAG grounding panel (FR-068). */

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  badge?: string;
  badgeTone?: 'info' | 'danger' | 'muted';
}

export function Sidebar({
  reviewCount,
  escalateCount,
  loop,
}: {
  reviewCount: number;
  escalateCount: number;
  loop: LoopStatus | null;
}) {
  const items: NavItem[] = [
    {
      to: '/',
      label: 'Run Console',
      icon: Terminal,
      badge: loop?.running ? 'LIVE' : undefined,
      badgeTone: 'info',
    },
    {
      to: '/queue',
      label: 'Review Queue',
      icon: ClipboardCheck,
      badge: reviewCount ? String(reviewCount) : undefined,
      badgeTone: 'muted',
    },
    { to: '/products', label: 'Product Details', icon: PackageSearch },
    {
      to: '/compliance',
      label: 'Compliance & Audit',
      icon: ScrollText,
      badge: escalateCount ? String(escalateCount) : undefined,
      badgeTone: 'danger',
    },
    { to: '/simulate', label: 'Simulation', icon: FlaskConical },
    { to: '/metrics', label: 'Metrics & Baseline', icon: GaugeCircle },
    { to: '/loop', label: 'Continuous Loop', icon: Repeat },
  ];

  return (
    <nav className="w-52 shrink-0 border-r border-hairline bg-surface flex flex-col py-4
                    overflow-y-auto">
      <div className="label px-4 pb-2.5">Workflow</div>
      {items.map(({ to, label, icon: Icon, badge, badgeTone }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          className={({ isActive }) =>
            `flex items-center gap-2.5 px-4 py-2.5 border-l-2 transition-colors ${
              isActive
                ? 'border-l-accent bg-raised text-ink font-semibold'
                : 'border-l-transparent text-faint hover:text-muted'
            }`
          }
        >
          <Icon size={14} aria-hidden />
          <span className="text-xs">{label}</span>
          {badge && (
            <span
              className={`ml-auto text-micro ${
                badgeTone === 'danger'
                  ? 'text-danger'
                  : badgeTone === 'info'
                    ? 'text-info'
                    : 'text-faint'
              }`}
            >
              {badge}
            </span>
          )}
        </NavLink>
      ))}

      <div className="mt-auto pt-4 px-4 border-t border-hairline">
        <RagPanel />
      </div>
    </nav>
  );
}
