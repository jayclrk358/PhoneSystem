import { type ReactNode, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { api, type ConfigStatus, onConfigChange } from "../api";
import { useAction, useLiveConnected } from "../hooks";
import { onLive, startLive, stopLive } from "../live";
import { ChangePassword } from "../pages/Account";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/calls", label: "Calls" },
  { to: "/extensions", label: "Extensions" },
  { to: "/phones", label: "Phones" },
  { to: "/firmware", label: "Firmware" },
  { to: "/settings", label: "Settings" },
  { to: "/config", label: "Config history" },
  { to: "/activity", label: "Activity" },
];

export function Layout({
  username,
  onSignOut,
  onSessionEnded,
  children,
}: {
  username: string;
  onSignOut: () => void;
  /** The server ended the session (signed out elsewhere, expired). */
  onSessionEnded: () => void;
  children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);

  useEffect(() => {
    startLive(onSessionEnded);
    return stopLive;
  }, [onSessionEnded]);
  return (
    <div className="shell">
      <header className="topbar">
        <button
          type="button"
          className="btn btn-ghost nav-toggle"
          aria-label="Menu"
          onClick={() => setMenuOpen(!menuOpen)}
        >
          ☰
        </button>
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            ☎
          </span>
          PhoneSystem
        </div>
        <div className="topbar-user">
          <LiveIndicator />
          <span className="muted">{username}</span>
          <button type="button" className="btn btn-small btn-ghost" onClick={() => setPwOpen(true)}>
            Password
          </button>
          <button type="button" className="btn btn-small" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </header>
      <nav className={`sidebar ${menuOpen ? "open" : ""}`} onClick={() => setMenuOpen(false)}>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end}>
            {n.label}
          </NavLink>
        ))}
      </nav>
      <main className="content">
        <PendingBanner />
        {children}
      </main>
      {pwOpen && <ChangePassword onClose={() => setPwOpen(false)} />}
    </div>
  );
}

/** Whether pages update by themselves right now. */
function LiveIndicator() {
  const connected = useLiveConnected();
  return (
    <span
      className={`live ${connected ? "live-on" : "live-off"}`}
      title={
        connected
          ? "Pages update by themselves as calls and changes happen."
          : "Reconnecting… pages refresh every 30 seconds meanwhile."
      }
    >
      <span className="live-dot" aria-hidden />
      {connected ? "Live" : "Reconnecting"}
    </span>
  );
}

/** Shows when the database has changes Asterisk isn't running yet. */
function PendingBanner() {
  const [status, setStatus] = useState<ConfigStatus | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const { busy, error, run } = useAction();

  useEffect(() => {
    let timer: number | undefined;
    const refresh = () => {
      window.clearTimeout(timer);
      // Small delay so a burst of edits causes one request.
      timer = window.setTimeout(() => {
        api.get<ConfigStatus>("/api/config/status").then(setStatus).catch(() => undefined);
      }, 300);
    };
    refresh();
    const unsubscribe = onConfigChange(refresh);
    const unsubscribeLive = onLive(["config", "extensions", "settings"], refresh);
    const interval = window.setInterval(refresh, 15000);
    return () => {
      unsubscribe();
      unsubscribeLive();
      window.clearInterval(interval);
      window.clearTimeout(timer);
    };
  }, []);

  const apply = () =>
    run(async () => {
      const v = await api.post<{ status: string; message: string }>("/api/config/apply");
      setResult({
        ok: v.status === "applied",
        text: v.status === "applied" ? v.message || "Changes are live." : v.message,
      });
    });

  if (!status?.pending && !result && !error) return null;
  return (
    <div className="banners">
      {status?.pending && (
        <div className="banner banner-pending">
          <span>
            {status.live_checksum
              ? "You have changes that aren't live on the phone system yet."
              : "Nothing has been applied to the phone system yet."}
          </span>
          <button type="button" className="btn btn-primary" onClick={apply} disabled={busy}>
            {busy ? "Applying…" : "Apply changes"}
          </button>
        </div>
      )}
      {(result || error) && (
        <div className={`banner ${result?.ok && !error ? "banner-ok" : "banner-error"}`}>
          <span>{error ?? result?.text}</span>
          <button type="button" className="btn btn-small btn-ghost" onClick={() => setResult(null)}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
