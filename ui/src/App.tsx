import { useEffect, useMemo, useState } from 'react';
import { Outlet, Route, Routes } from 'react-router-dom';

import { Header } from './components/Header';
import { ChatTerminal } from './components/ChatTerminal';
import { Sidebar } from './components/Sidebar';
import { SettingsDrawer } from './components/SettingsDrawer';
import { api } from './lib/api';
import { useApi, usePersisted } from './lib/hooks';
import { Compliance } from './routes/Compliance';
import { LoopControl } from './routes/LoopControl';
import { Metrics } from './routes/Metrics';
import { Products } from './routes/Products';
import { RecommendationDetail } from './routes/RecommendationDetail';
import { ReviewQueue } from './routes/ReviewQueue';
import { RunConsole } from './routes/RunConsole';
import { Simulation } from './routes/Simulation';

/**
 * Application shell.
 *
 * Config, telemetry and loop status are polled once here and passed down rather
 * than fetched per route, so the header monitor and the loop indicator stay
 * consistent across navigation instead of flickering on every route change.
 */
export default function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [theme, setTheme] = usePersisted<'dark' | 'light'>('pricing-theme', 'dark');

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const config = useApi(() => api.config(), [], 20000);
  const telemetry = useApi(() => api.telemetry(), [], 4000);
  const loop = useApi(() => api.loopStatus(), [], 5000);
  const pending = useApi(
    () => api.recommendations({ status: 'pending', limit: 5000 }),
    [],
    15000,
  );

  const counts = useMemo(() => {
    const rows = pending.data ?? [];
    return {
      review: rows.filter((r) => r.band === 'review').length,
      escalate: rows.filter((r) => r.band === 'escalate').length,
    };
  }, [pending.data]);

  const refreshAll = () => {
    void config.reload();
    void loop.reload();
    void pending.reload();
  };

  return (
    <div className="h-full flex flex-col min-w-[1280px]">
      <Header
        config={config.data}
        telemetry={telemetry.data}
        loop={loop.data}
        onOpenSettings={() => setSettingsOpen(true)}
        onChanged={refreshAll}
        theme={theme}
        onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
      />

      <div className="flex-1 flex min-h-0">
        <Sidebar
          reviewCount={counts.review}
          escalateCount={counts.escalate}
          loop={loop.data}
        />
        <main className="flex-1 min-w-0 overflow-y-auto">
          <Routes>
            <Route element={<Outlet />}>
              <Route
                index
                element={<RunConsole config={config.data} onChanged={refreshAll} />}
              />
              <Route path="queue" element={<ReviewQueue onChanged={refreshAll} />} />
              <Route
                path="recommendation/:recId"
                element={<RecommendationDetail onChanged={refreshAll} />}
              />
              <Route path="compliance" element={<Compliance />} />
              <Route path="simulate" element={<Simulation />} />
              <Route path="metrics" element={<Metrics />} />
              <Route path="products" element={<Products />} />
              <Route
                path="loop"
                element={<LoopControl loop={loop.data} onChanged={refreshAll} />}
              />
              <Route
                path="*"
                element={
                  <div className="p-8 text-sm text-faint">
                    No such view. Use the workflow navigation on the left.
                  </div>
                }
              />
            </Route>
          </Routes>
        </main>
      </div>

      <Footer configError={config.error} />

      <ChatTerminal />

      <SettingsDrawer
        open={settingsOpen}
        config={config.data}
        onClose={() => setSettingsOpen(false)}
        onChanged={refreshAll}
      />
    </div>
  );
}

function Footer({ configError }: { configError: string | null }) {
  const health = useApi(() => api.health(), [], 20000);
  const commerce = health.data?.commerce as { status?: string } | undefined;

  return (
    <footer className="h-8 shrink-0 border-t border-hairline bg-surface flex items-center
                       gap-4 px-5 text-micro text-faint">
      <span>
        Commerce :8001 {commerce?.status ?? 'unknown'} · Platform :8000{' '}
        {health.data?.status ?? (configError ? 'unreachable' : '…')}
      </span>
      <span className="ml-auto">
        Append-only audit · {health.data?.tls.detail ?? 'TLS status pending'} ·
        single-region, single-currency
      </span>
    </footer>
  );
}
