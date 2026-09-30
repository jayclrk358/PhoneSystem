import { useState } from "react";
import { api, type AuditEntry, type ConfigVersion } from "../api";
import { ErrorNote, Loading, Modal, Note, PageHeader, Pill } from "../components/ui";
import { formatTime } from "../format";
import { useAction, useApi } from "../hooks";

const STATUS_KIND = { applied: "ok", superseded: "muted", failed: "bad", pending: "warn" } as const;

export function ConfigPage() {
  const versions = useApi<ConfigVersion[]>("/api/config/versions");
  const [preview, setPreview] = useState(false);
  const [confirm, setConfirm] = useState<number | null>(null);
  const { busy, error, run } = useAction();

  const rollback = (id: number) =>
    run(async () => {
      await api.post(`/api/config/versions/${id}/rollback`);
      setConfirm(null);
      versions.reload();
    });

  return (
    <>
      <PageHeader title="Config history">
        <button type="button" className="btn" onClick={() => setPreview(true)}>
          Preview generated config
        </button>
      </PageHeader>
      <p className="muted">
        Every time changes are applied, the complete Asterisk config is saved as a new version. If something goes
        wrong you can go back to an earlier one.
      </p>
      <ErrorNote error={error ?? versions.error} />
      {versions.loading && !versions.data ? (
        <Loading />
      ) : (versions.data ?? []).length === 0 ? (
        <p className="muted">Nothing has been applied yet.</p>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>When</th>
                <th>By</th>
                <th>Result</th>
                <th>Notes</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(versions.data ?? []).map((v) => (
                <tr key={v.id}>
                  <td>v{v.id}</td>
                  <td>{formatTime(v.created_at)}</td>
                  <td>{v.created_by}</td>
                  <td>
                    <Pill kind={STATUS_KIND[v.status] ?? "muted"}>{v.status === "applied" ? "live" : v.status}</Pill>
                  </td>
                  <td className="small">{v.message}</td>
                  <td className="actions">
                    {v.status === "superseded" &&
                      (confirm === v.id ? (
                        <span className="inline">
                          <button type="button" className="btn btn-small btn-danger" onClick={() => rollback(v.id)} disabled={busy}>
                            Roll back
                          </button>
                          <button type="button" className="btn btn-small btn-ghost" onClick={() => setConfirm(null)}>
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button type="button" className="btn btn-small" onClick={() => setConfirm(v.id)}>
                          Roll back…
                        </button>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {confirm !== null && (
        <Note kind="warn">
          Rolling back makes Asterisk run version v{confirm} again. Your current extensions and settings stay in the
          database, so "Apply changes" will show until you apply them again.
        </Note>
      )}
      {preview && <ConfigPreview onClose={() => setPreview(false)} />}
    </>
  );
}

function ConfigPreview({ onClose }: { onClose: () => void }) {
  const files = useApi<Record<string, string>>("/api/config/preview");
  return (
    <Modal title="Generated Asterisk config" onClose={onClose}>
      <p className="muted small">What applying now would give Asterisk. Passwords are hidden.</p>
      <ErrorNote error={files.error} />
      {Object.entries(files.data ?? {}).map(([name, text]) => (
        <div key={name}>
          <h3>
            <code>{name}</code>
          </h3>
          <pre className="code-block">{text}</pre>
        </div>
      ))}
    </Modal>
  );
}

export function ActivityPage() {
  const entries = useApi<AuditEntry[]>("/api/audit?limit=300", 15000);
  return (
    <>
      <PageHeader title="Activity" />
      <p className="muted">Who changed what, and sign-in attempts.</p>
      <ErrorNote error={entries.error} />
      {entries.loading && !entries.data ? (
        <Loading />
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>What</th>
                <th>Target</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {(entries.data ?? []).map((e, i) => (
                <tr key={i}>
                  <td>{formatTime(e.at)}</td>
                  <td>{e.username}</td>
                  <td>
                    <code>{e.action}</code>
                  </td>
                  <td>{e.target}</td>
                  <td className="muted small">{e.detail ? JSON.stringify(e.detail) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
