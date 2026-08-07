import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Fetch-with-refresh. Polling is used rather than websockets deliberately: the
 * data here changes on human timescales, and a poll that fails is visibly stale
 * rather than silently disconnected.
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  intervalMs?: number,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async () => {
    try {
      const result = await fetcherRef.current();
      if (!alive.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (!alive.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    void load();
    if (!intervalMs) return () => void (alive.current = false);
    const timer = setInterval(() => void load(), intervalMs);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs]);

  return { data, error, loading, reload: load };
}

/** Local-storage-backed state, for preferences that must survive a reload. */
export function usePersisted<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(key);
      return stored ? (JSON.parse(stored) as T) : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private browsing — the preference simply does not persist */
    }
  }, [key, value]);
  return [value, setValue] as const;
}

/** Transient status line for an action ("Approved", "Push failed: ..."). */
export function useActionState() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; kind: 'ok' | 'error' } | null>(
    null,
  );

  const run = useCallback(
    async (action: () => Promise<string>) => {
      setBusy(true);
      try {
        setMessage({ text: await action(), kind: 'ok' });
      } catch (err) {
        setMessage({
          text: err instanceof Error ? err.message : String(err),
          kind: 'error',
        });
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return { busy, message, run, clear: () => setMessage(null) };
}
