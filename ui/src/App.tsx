import { useEffect, useMemo, useState } from 'react';
import { Navigate, Route, Routes, useParams } from 'react-router-dom';

import { Header } from './components/Header';
import { AssistantPanel } from './components/AssistantPanel';
import { SettingsDialog } from './components/SettingsDialog';
import { TabBar } from './components/TabBar';
import { api } from './lib/api';
import { useApi, usePersisted } from './lib/hooks';
import { Audit } from './routes/Audit';
import { Diagnostics } from './routes/Diagnostics';
import { Impact } from './routes/Impact';
import { Overview } from './routes/Overview';
import { Products } from './routes/Products';
import { Review } from './routes/Review';
import { Run } from './routes/Run';
import type { Health } from './lib/types';

/**
 * Application shell.
 *
 * Config, health and loop status are polled once here and passed down rather
 * than fetched per route, so the header and the tab bar stay consistent across
 * navigation instead of flickering on every route change.
 *
 * The assistant is a sibling of the page content, not an overlay on top of it.
 * That is the whole point of the layout below: when it opens, the routed view
 * gets a narrower column and reflows into it, so the assistant never covers a
 * table, a decision panel, or anything else the reader was using.
 */

const MIN_PANEL = 320;
const MAX_PANEL = 640;

export default function App() {
  const [theme, setTheme] = usePersisted<'dark' | 'light'>('pricing-theme', 'dark');
  const [assistantOpen, setAssistantOpen] = usePersisted('pricing-assistant-open', false);
  const [panelWidth, setPanelWidth] = usePersisted('pricing-assistant-width', 400);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const config = useApi(() => api.config(), [], 20000);
  const loop = useApi(() => api.loopStatus(), [], 5000);
  const health = useApi(() => api.health(), [], 20000);

  // Counts for the tab badge and the Overview tiles. The full queue is fetched
  // by Review itself; this is deliberately a small, slow poll so the badge does
  // not cost a multi-thousand-row payload every fifteen seconds.
  const pending = useApi(
    () => api.recommendations({ status: 'pending', limit: 5000 }),
    [],
    30000,
  );

  const counts = useMemo(() => {
    const rows = pending.data ?? [];
    return {
      review: rows.filter((r) => r.band === 'review').length,
      escalate: rows.filter((r) => r.band === 'escalate').length,
      autoApprove: rows.filter((r) => r.band === 'auto_approve').length,
      total: rows.length,
    };
  }, [pending.data]);

  const refreshAll = () => {
    void config.reload();
    void loop.reload();
    void pending.reload();
  };

  return (
    <div className="h-full flex flex-col">
      <Header
        config={config.data}
        loop={loop.data}
        health={health.data}
        onChanged={refreshAll}
        theme={theme}
        onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
        onOpenSettings={() => setSettingsOpen(true)}
        assistantOpen={assistantOpen}
        onToggleAssistant={() => setAssistantOpen(!assistantOpen)}
      />

      <div className="flex-1 min-h-0 flex">
        <div className="flex-1 min-w-0 flex flex-col">
          <TabBar reviewCount={counts.review} loopRunning={loop.data?.running ?? false} />

          <main className="flex-1 min-h-0 overflow-y-auto">
            <Routes>
              <Route path="/" element={<Overview counts={counts} config={config.data} />} />
              <Route
                path="/run"
                element={<Run config={config.data} loop={loop.data} onChanged={refreshAll} />}
              />
              <Route path="/review" element={<Review onChanged={refreshAll} />} />
              <Route path="/review/:recId" element={<Review onChanged={refreshAll} />} />
              <Route path="/products" element={<Products />} />
              <Route path="/impact" element={<Impact />} />
              <Route path="/audit" element={<Audit />} />
              <Route
                path="/diagnostics"
                element={<Diagnostics config={config.data} onChanged={refreshAll} />}
              />

              {/* Links minted by earlier versions of the navigation. */}
              <Route path="/system" element={<Navigate to="/diagnostics" replace />} />
              <Route path="/queue" element={<Navigate to="/review" replace />} />
              <Route path="/recommendation/:recId" element={<LegacyRecommendation />} />
              <Route path="/compliance" element={<Navigate to="/audit" replace />} />
              <Route path="/simulate" element={<Navigate to="/impact" replace />} />
              <Route path="/metrics" element={<Navigate to="/impact" replace />} />
              <Route path="/loop" element={<Navigate to="/run" replace />} />

              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>

        <AssistantPanel
          open={assistantOpen}
          onClose={() => setAssistantOpen(false)}
          width={panelWidth}
          onResize={(next) =>
            setPanelWidth(Math.min(MAX_PANEL, Math.max(MIN_PANEL, Math.round(next))))
          }
        />
      </div>

      <Footer health={health.data} configError={config.error} />

      <SettingsDialog
        open={settingsOpen}
        config={config.data}
        onClose={() => setSettingsOpen(false)}
        onChanged={refreshAll}
      />
    </div>
  );
}

/** `/recommendation/:id` was the old deep link into a single record. */
function LegacyRecommendation() {
  const { recId } = useParams();
  return <Navigate to={recId ? `/review/${recId}` : '/review'} replace />;
}

function Footer({
  health,
  configError,
}: {
  health: Health | null;
  configError: string | null;
}) {
  const commerce = health?.commerce as { status?: string } | undefined;

  return (
    <footer
      className="h-8 shrink-0 border-t border-hairline bg-surface flex items-center
                 gap-4 px-5 text-micro text-faint"
    >
      <span>
        Store service {commerce?.status ?? 'unknown'} · Pricing platform{' '}
        {health?.status ?? (configError ? 'unreachable' : '…')}
      </span>
      <span className="ml-auto">
        Every action is recorded and cannot be edited · single region, single currency
      </span>
    </footer>
  );
}
