import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { isLiveConnected, onLive, onLiveState, type Topic } from "./live";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Whether the live-update connection is up. */
export function useLiveConnected(): boolean {
  const [connected, setConnected] = useState(isLiveConnected());
  useEffect(() => onLiveState(setConnected), []);
  return connected;
}

/**
 * GET a path and keep the result.
 *
 * ``live``: reload whenever the server says one of these topics changed. While
 * the live connection is down, fall back to polling every ``intervalMs``
 * (default 30 s). Without ``live``, poll every ``intervalMs`` if given.
 */
export function useApi<T>(
  path: string | null,
  intervalMs?: number,
  live?: readonly Topic[],
): Loaded<T> {
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

  const liveKey = live?.join(",") ?? "";
  useEffect(() => {
    if (!liveKey) return;
    return onLive(liveKey.split(",") as Topic[], reload);
  }, [liveKey, reload]);

  const liveConnected = useLiveConnected();
  const pollMs = live ? (liveConnected ? 0 : (intervalMs ?? 30000)) : intervalMs;
  useEffect(() => {
    if (!pollMs) return;
    const id = window.setInterval(reload, pollMs);
    return () => window.clearInterval(id);
  }, [pollMs, reload]);

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
