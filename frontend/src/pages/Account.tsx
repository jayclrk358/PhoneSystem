import { type FormEvent, useState } from "react";
import { api, type AuthStatus } from "../api";
import { ErrorNote, Field, Modal, Note } from "../components/ui";
import { useAction } from "../hooks";

export function SetupPage({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const { busy, error, setError, run } = useAction();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (password !== repeat) {
      setError("The passwords don't match.");
      return;
    }
    run(async () => {
      await api.post("/api/auth/setup", { username, password });
      onDone();
    });
  };

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Welcome to PhoneSystem</h1>
        <p className="muted">Create the admin account you'll use to manage the phone system.</p>
        <Field label="Username">
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </Field>
        <Field label="Password" hint="At least 10 characters.">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            minLength={10}
            required
          />
        </Field>
        <Field label="Repeat password">
          <input
            type="password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            autoComplete="new-password"
            required
          />
        </Field>
        <ErrorNote error={error} />
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Creating…" : "Create admin account"}
        </button>
      </form>
    </div>
  );
}

export function LoginPage({ onDone }: { onDone: (s: AuthStatus["user"]) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, run } = useAction();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const user = await api.post<{ username: string }>("/api/auth/login", { username, password });
      onDone(user);
    });
  };

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Sign in</h1>
        <Field label="Username">
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
        </Field>
        <Field label="Password">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </Field>
        <ErrorNote error={error} />
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

export function ChangePassword({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);
  const { busy, error, run } = useAction();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      await api.post("/api/auth/password", { current_password: current, new_password: next });
      setDone(true);
    });
  };

  return (
    <Modal title="Change your password" onClose={onClose}>
      {done ? (
        <>
          <Note kind="ok">Password changed. Other signed-in browsers have been signed out.</Note>
          <div className="form-actions">
            <button type="button" className="btn" onClick={onClose}>
              Close
            </button>
          </div>
        </>
      ) : (
        <form onSubmit={submit}>
          <Field label="Current password">
            <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
          </Field>
          <Field label="New password" hint="At least 10 characters.">
            <input
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              autoComplete="new-password"
              minLength={10}
              required
            />
          </Field>
          <ErrorNote error={error} />
          <div className="form-actions">
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button className="btn btn-primary" disabled={busy}>
              Change password
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
