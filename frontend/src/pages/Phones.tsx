import { type FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  type Extension,
  type LiveStatus,
  type LogLine,
  type Phone,
  type ProvisioningRequest,
} from "../api";
import { CopyButton, ErrorNote, Field, Loading, Modal, Note, PageHeader, Pill } from "../components/ui";
import { formatBytes, formatMac, formatTime, timeAgo } from "../format";
import { useAction, useApi } from "../hooks";

export function PhonesPage() {
  const phones = useApi<Phone[]>("/api/phones", 10000);
  const status = useApi<LiveStatus>("/api/status", 10000);
  const unknown = useApi<ProvisioningRequest[]>("/api/provisioning/requests?limit=200", 10000);
  const [adding, setAdding] = useState(false);

  const registered = new Set((status.data?.registrations ?? []).map((r) => r.endpoint));
  // Requests we couldn't tie to a MAC address, newest per IP.
  const unidentified = new Map<string, ProvisioningRequest>();
  for (const r of unknown.data ?? []) if (!r.mac && !unidentified.has(r.ip)) unidentified.set(r.ip, r);

  return (
    <>
      <PageHeader title="Phones">
        <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
          Add phone
        </button>
      </PageHeader>
      <p className="muted">
        Phones appear here automatically the first time they ask for their settings. Assign each one to an extension
        and it logs itself in.
      </p>
      <ErrorNote error={phones.error} />
      {phones.loading && !phones.data ? (
        <Loading />
      ) : (phones.data ?? []).length === 0 ? (
        <div className="card empty">
          <p>
            No phones yet. Set DHCP option 242 (see the Dashboard) and restart a phone, or add one by the MAC address on
            its label.
          </p>
        </div>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>
                <th>Phone</th>
                <th>MAC</th>
                <th>Extension</th>
                <th>Status</th>
                <th>IP</th>
                <th>Last seen</th>
                <th>Firmware</th>
              </tr>
            </thead>
            <tbody>
              {(phones.data ?? []).map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link to={`/phones/${p.id}`}>{p.label || p.model || "Phone"}</Link>
                    {p.discovered && !p.extension_id && (
                      <>
                        {" "}
                        <Pill kind="warn">New</Pill>
                      </>
                    )}
                  </td>
                  <td>
                    <code>{p.mac}</code>
                  </td>
                  <td>
                    {p.extension_number ? (
                      `${p.extension_number} ${p.extension_name ?? ""}`
                    ) : (
                      <span className="muted">not assigned</span>
                    )}
                  </td>
                  <td>
                    {p.extension_number && registered.has(p.extension_number) ? (
                      <Pill kind="ok">Registered</Pill>
                    ) : (
                      <Pill kind="muted">Not registered</Pill>
                    )}
                  </td>
                  <td>{p.last_ip ?? ""}</td>
                  <td>{timeAgo(p.last_seen_at)}</td>
                  <td className="muted">{p.firmware_version ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {unidentified.size > 0 && (
        <section className="card">
          <h2>Requests from phones we couldn't identify</h2>
          <p className="muted small">
            These devices asked for settings, but their MAC address couldn't be worked out (usually because they're
            on a different network segment than this server). They still get the shared settings; add them by MAC
            address to enable automatic login.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>IP</th>
                  <th>Last request</th>
                  <th>When</th>
                  <th>Device</th>
                </tr>
              </thead>
              <tbody>
                {[...unidentified.values()].map((r) => (
                  <tr key={r.ip}>
                    <td>{r.ip}</td>
                    <td>
                      <code>{r.path}</code> <Pill kind={r.status === 200 ? "ok" : "warn"}>{r.status}</Pill>
                    </td>
                    <td>{timeAgo(r.at)}</td>
                    <td className="muted">{r.user_agent}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {adding && <AddPhone onClose={() => setAdding(false)} onAdded={phones.reload} />}
    </>
  );
}

function ExtensionSelect({
  value,
  onChange,
  currentPhoneId,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
  currentPhoneId?: number;
}) {
  const exts = useApi<Extension[]>("/api/extensions");
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">Not assigned</option>
      {(exts.data ?? []).map((e) => {
        const taken = e.phone_id !== null && e.phone_id !== currentPhoneId;
        return (
          <option key={e.id} value={e.id} disabled={taken}>
            {e.number} {e.name}
            {taken ? " (on another phone)" : ""}
          </option>
        );
      })}
    </select>
  );
}

function AddPhone({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [mac, setMac] = useState("");
  const [label, setLabel] = useState("");
  const [ext, setExt] = useState<number | null>(null);
  const { busy, error, run } = useAction();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      await api.post("/api/phones", { mac, label, extension_id: ext });
      onAdded();
      onClose();
    });
  };

  return (
    <Modal title="Add phone" onClose={onClose}>
      <form onSubmit={submit}>
        <Field label="MAC address" hint="Printed on the label under the phone, e.g. 00:1B:4F:12:34:56.">
          <input value={mac} onChange={(e) => setMac(e.target.value)} autoFocus required />
        </Field>
        <Field label="Label" hint="Optional, e.g. Front desk.">
          <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={64} />
        </Field>
        <Field label="Extension">
          <ExtensionSelect value={ext} onChange={setExt} />
        </Field>
        <ErrorNote error={error} />
        <div className="form-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy}>
            Add phone
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function PhoneDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const phones = useApi<Phone[]>("/api/phones", 10000);
  const current = (phones.data ?? []).find((p) => String(p.id) === id);
  const requests = useApi<ProvisioningRequest[]>(`/api/phones/${id}/requests?limit=50`, 10000);
  const logs = useApi<LogLine[]>(`/api/phones/${id}/logs?limit=200`, 10000);
  const [reveal, setReveal] = useState(false);
  const settingsFile = useApi<{ content: string }>(`/api/phones/${id}/settings-file${reveal ? "?reveal=true" : ""}`);
  const [label, setLabel] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { busy, error, run } = useAction();

  if (phones.error) return <ErrorNote error={phones.error} />;
  if (!current) return phones.loading ? <Loading /> : <ErrorNote error="Phone not found." />;

  const update = (body: Record<string, unknown>) =>
    run(async () => {
      await api.patch(`/api/phones/${current.id}`, body);
      phones.reload();
      settingsFile.reload();
      setLabel(null);
    });

  const remove = () =>
    run(async () => {
      await api.del(`/api/phones/${current.id}`);
      navigate("/phones");
    });

  return (
    <>
      <PageHeader title={current.label || `Phone ${current.mac}`}>
        <Link to="/phones" className="btn btn-ghost">
          All phones
        </Link>
      </PageHeader>
      <ErrorNote error={error} />

      <div className="grid-2">
        <section className="card">
          <h2>Details</h2>
          <dl className="details">
            <dt>MAC address</dt>
            <dd>
              <code>{current.mac}</code>
            </dd>
            <dt>Model</dt>
            <dd>{current.model || <span className="muted">unknown</span>}</dd>
            <dt>Firmware</dt>
            <dd>{current.firmware_version || <span className="muted">unknown</span>}</dd>
            <dt>IP address</dt>
            <dd>{current.last_ip || <span className="muted">not seen yet</span>}</dd>
            <dt>Last seen</dt>
            <dd>{formatTime(current.last_seen_at)}</dd>
            <dt>Device string</dt>
            <dd className="muted small">{current.user_agent || ""}</dd>
          </dl>
        </section>

        <section className="card">
          <h2>Assignment</h2>
          <Field label="Label">
            <div className="inline">
              <input
                value={label ?? current.label}
                onChange={(e) => setLabel(e.target.value)}
                maxLength={64}
                placeholder="e.g. Front desk"
              />
              {label !== null && label !== current.label && (
                <button type="button" className="btn" onClick={() => update({ label })} disabled={busy}>
                  Save
                </button>
              )}
            </div>
          </Field>
          <Field
            label="Extension"
            hint="The phone logs in as this extension the next time it reboots or re-reads its settings."
          >
            <ExtensionSelect
              value={current.extension_id}
              currentPhoneId={current.id}
              onChange={(v) => update({ extension_id: v })}
            />
          </Field>
          <div className="form-actions">
            {confirmDelete ? (
              <Note kind="warn">
                Remove this phone from the list? It will reappear if it asks for settings again.{" "}
                <button type="button" className="btn btn-small btn-danger" onClick={remove} disabled={busy}>
                  Remove
                </button>{" "}
                <button type="button" className="btn btn-small btn-ghost" onClick={() => setConfirmDelete(false)}>
                  Keep
                </button>
              </Note>
            ) : (
              <button type="button" className="btn btn-danger-ghost" onClick={() => setConfirmDelete(true)}>
                Remove phone
              </button>
            )}
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Settings this phone receives</h2>
          <div className="inline">
            <label className="checkbox">
              <input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} /> Show password
            </label>
            {settingsFile.data && <CopyButton text={settingsFile.data.content} />}
          </div>
        </div>
        <p className="muted small">
          This is the <code>46xxsettings.txt</code> file served to this phone. Changes take effect when the phone
          restarts.
        </p>
        <ErrorNote error={settingsFile.error} />
        <pre className="code-block">{settingsFile.data?.content ?? ""}</pre>
      </section>

      <section className="card">
        <h2>Provisioning requests</h2>
        {(requests.data ?? []).length === 0 ? (
          <p className="muted">None yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Request</th>
                  <th>Result</th>
                  <th>Size</th>
                </tr>
              </thead>
              <tbody>
                {(requests.data ?? []).map((r, i) => (
                  <tr key={i}>
                    <td>{formatTime(r.at)}</td>
                    <td>
                      {r.method} <code>{r.path}</code>
                    </td>
                    <td>
                      <Pill kind={r.status === 200 ? "ok" : "warn"}>{r.status}</Pill>
                    </td>
                    <td>{formatBytes(r.size)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Phone log</h2>
        <p className="muted small">
          Log messages the phone sends to this server (syslog). If this stays empty, turn on remote logging in the
          phone's admin menu: press Mute, then 2 7 2 3 8 #.
        </p>
        {(logs.data ?? []).length === 0 ? (
          <p className="muted">No log messages from {current.last_ip ?? "this phone"} yet.</p>
        ) : (
          <pre className="code-block log">
            {[...(logs.data ?? [])]
              .reverse()
              .map((l) => `${formatTime(l.at)}  ${l.message}`)
              .join("\n")}
          </pre>
        )}
      </section>
      <p className="muted small">MAC {formatMac(current.mac)} · added {formatTime(current.created_at)}</p>
    </>
  );
}
