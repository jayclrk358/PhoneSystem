import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** GET a path and keep the result; optionally refresh every ``intervalMs``. */
export function useApi<T>(path: string | null, intervalMs?: number): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    setLoading(true);
    api
      .get<T>(path)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [path, tick]);

  useEffect(() => {
    if (!intervalMs) return;
    const id = window.setInterval(reload, intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs, reload]);

  return { data, error, loading, reload };
}

/** Run an async action, tracking busy state and the error message. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );
  const run = useCallback(async <R,>(fn: () => Promise<R>): Promise<R | undefined> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
      return undefined;
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, []);
  return { busy, error, setError, run };
}
