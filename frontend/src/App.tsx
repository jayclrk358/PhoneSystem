import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, type AuthStatus, setUnauthorizedHandler } from "./api";
import { Layout } from "./components/Layout";
import { ErrorNote } from "./components/ui";
import { LoginPage, SetupPage } from "./pages/Account";
import { CallsPage } from "./pages/Calls";
import { DashboardPage } from "./pages/Dashboard";
import { ExtensionsPage } from "./pages/Extensions";
import { FirmwarePage } from "./pages/Firmware";
import { ActivityPage, ConfigPage } from "./pages/History";
import { PhoneDetailPage, PhonesPage } from "./pages/Phones";
import { SettingsPage } from "./pages/Settings";

export function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .get<AuthStatus>("/api/auth/status")
      .then((s) => {
        setAuth(s);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const sessionEnded = useCallback(() => setAuth((a) => (a ? { ...a, user: null } : a)), []);

  useEffect(() => {
    refresh();
    setUnauthorizedHandler(sessionEnded);
  }, [refresh, sessionEnded]);

  if (error) {
    return (
      <div className="auth-page">
        <div className="card auth-card">
          <h1>Can't reach PhoneSystem</h1>
          <ErrorNote error={error} />
          <button type="button" className="btn" onClick={refresh}>
            Try again
          </button>
        </div>
      </div>
    );
  }
  if (!auth) return null;
  if (auth.setup_required) return <SetupPage onDone={refresh} />;
  if (!auth.user) return <LoginPage onDone={(user) => setAuth({ setup_required: false, user })} />;

  const signOut = async () => {
    await api.post("/api/auth/logout").catch(() => undefined);
    setAuth({ setup_required: false, user: null });
  };

  return (
    <BrowserRouter>
      <Layout username={auth.user.username} onSignOut={signOut} onSessionEnded={sessionEnded}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/calls" element={<CallsPage />} />
          <Route path="/extensions" element={<ExtensionsPage />} />
          <Route path="/phones" element={<PhonesPage />} />
          <Route path="/phones/:id" element={<PhoneDetailPage />} />
          <Route path="/firmware" element={<FirmwarePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
