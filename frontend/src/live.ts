// Live updates: one EventSource on /api/events for the whole app. The server
// sends the names of things that changed; pages re-fetch what they show.

export type Topic =
  | "calls"
  | "provisioning"
  | "phones"
  | "phone_logs"
  | "status"
  | "extensions"
  | "settings"
  | "firmware"
  | "config"
  | "audit";

type Listener = { topics: Set<string>; fn: () => void };

const listeners = new Set<Listener>();
const stateListeners = new Set<(connected: boolean) => void>();
let source: EventSource | null = null;
let connected = false;
let everConnected = false;
let wanted = false;
let retryTimer: number | undefined;
let signedOut: (() => void) | null = null;

const RETRY_MS = 5000;

function setConnected(value: boolean) {
  if (connected === value) return;
  connected = value;
  stateListeners.forEach((fn) => fn(value));
}

function notify(topics: string[] | null) {
  // null = "anything may have changed" (after a reconnect we may have missed events)
  for (const l of [...listeners]) {
    if (topics === null || topics.some((t) => l.topics.has(t))) l.fn();
  }
}

function open() {
  window.clearTimeout(retryTimer);
  const es = new EventSource("/api/events");
  source = es;
  es.addEventListener("ready", () => {
    setConnected(true);
    if (everConnected) notify(null);
    everConnected = true;
  });
  es.addEventListener("change", (e) => {
    try {
      const topics = JSON.parse((e as MessageEvent).data);
      if (Array.isArray(topics)) notify(topics.map(String));
    } catch {
      // ignore a malformed message
    }
  });
  es.addEventListener("signed-out", () => {
    stopLive();
    signedOut?.();
  });
  es.onerror = () => {
    setConnected(false);
    // The browser retries by itself while CONNECTING; a refused connection
    // (e.g. HTTP 401 or 502) is CLOSED and needs a manual retry.
    if (es.readyState === EventSource.CLOSED && source === es) {
      source = null;
      if (wanted) retryTimer = window.setTimeout(open, RETRY_MS);
    }
  };
}

/** Start live updates (once signed in). ``onSignedOut`` runs if the session ends. */
export function startLive(onSignedOut: () => void) {
  signedOut = onSignedOut;
  wanted = true;
  if (!source && typeof EventSource !== "undefined") open();
}

export function stopLive() {
  wanted = false;
  window.clearTimeout(retryTimer);
  source?.close();
  source = null;
  everConnected = false;
  setConnected(false);
}

/** Call ``fn`` whenever one of ``topics`` changes. Returns an unsubscribe function. */
export function onLive(topics: readonly Topic[], fn: () => void): () => void {
  const l = { topics: new Set<string>(topics), fn };
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function isLiveConnected(): boolean {
  return connected;
}

export function onLiveState(fn: (connected: boolean) => void): () => void {
  stateListeners.add(fn);
  return () => {
    stateListeners.delete(fn);
  };
}
