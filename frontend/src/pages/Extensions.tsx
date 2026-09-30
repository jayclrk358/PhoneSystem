import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Extension, type ExtensionDetail, type LiveStatus } from "../api";
import { ErrorNote, Field, Loading, Modal, Note, PageHeader, Pill, Secret } from "../components/ui";
import { useAction, useApi } from "../hooks";

export function ExtensionsPage() {
  const list = useApi<Extension[]>("/api/extensions");
  const status = useApi<LiveStatus>("/api/status", 10000);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);

  const registered = new Set((status.data?.registrations ?? []).map((r) => r.endpoint));

  return (
    <>
      <PageHeader title="Extensions">
        <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
          Add extension
        </button>
      </PageHeader>
      <ErrorNote error={list.error} />
      {list.loading && !list.data ? (
        <Loading />
      ) : (list.data ?? []).length === 0 ? (
        <div className="card empty">
          <p>No extensions yet. Each person (or desk) gets an extension number to dial and log in with.</p>
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            Add the first extension
          </button>
        </div>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>
                <th>Number</th>
                <th>Name</th>
                <th>Phone</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((e) => (
                <tr key={e.id} className={e.enabled ? "" : "row-disabled"}>
                  <td>
                    <strong>{e.number}</strong>
                  </td>
                  <td>{e.name}</td>
                  <td>
                    {e.phone_id ? (
                      <Link to={`/phones/${e.phone_id}`}>
                        <code>{e.phone_mac}</code>
                      </Link>
                    ) : (
                      <span className="muted">none</span>
                    )}
                  </td>
                  <td>
                    {!e.enabled ? (
                      <Pill kind="muted">Disabled</Pill>
                    ) : registered.has(e.number) ? (
                      <Pill kind="ok">Registered</Pill>
                    ) : (
                      <Pill kind="warn">Offline</Pill>
                    )}
                  </td>
                  <td className="actions">
                    <button type="button" className="btn btn-small" onClick={() => setEditing(e.id)}>
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && (
        <CreateExtension
          existing={(list.data ?? []).map((e) => e.number)}
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            list.reload();
            setEditing(id);
          }}
        />
      )}
      {editing !== null && (
        <EditExtension
          id={editing}
          onClose={() => setEditing(null)}
          onChanged={list.reload}
        />
      )}
    </>
  );
}

function nextNumber(existing: string[]): string {
  const nums = existing.map(Number).filter((n) => Number.isFinite(n));
  return nums.length ? String(Math.max(...nums) + 1) : "101";
}

function CreateExtension({
  existing,
  onClose,
  onCreated,
}: {
  existing: string[];
  onClose: () => void;
  onCreated: (id: number) => void;
}) {
  const [number, setNumber] = useState(nextNumber(existing));
  const [name, setName] = useState("");
  const { busy, error, run } = useAction();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const ext = await api.post<ExtensionDetail>("/api/extensions", { number, name });
      onCreated(ext.id);
    });
  };

  return (
    <Modal title="Add extension" onClose={onClose}>
      <form onSubmit={submit}>
        <Field label="Extension number" hint="2–6 digits, not starting with 0.">
          <input value={number} onChange={(e) => setNumber(e.target.value)} inputMode="numeric" pattern="[1-9][0-9]{1,5}" required />
        </Field>
        <Field label="Name" hint="Shown to the people this extension calls.">
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={64} autoFocus required />
        </Field>
        <p className="muted small">A SIP password is generated automatically.</p>
        <ErrorNote error={error} />
        <div className="form-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy}>
            Add extension
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EditExtension({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const ext = useApi<ExtensionDetail>(`/api/extensions/${id}`);
  const [form, setForm] = useState<{ number: string; name: string; enabled: boolean } | null>(null);
  const { busy, error, run } = useAction();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const current = form ?? (ext.data ? { number: ext.data.number, name: ext.data.name, enabled: ext.data.enabled } : null);

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!current) return;
    run(async () => {
      await api.patch(`/api/extensions/${id}`, current);
      onChanged();
      onClose();
    });
  };

  const regenerate = () =>
    run(async () => {
      await api.patch(`/api/extensions/${id}`, { regenerate_password: true });
      ext.reload();
      onChanged();
    });

  const remove = () =>
    run(async () => {
      await api.del(`/api/extensions/${id}`);
      onChanged();
      onClose();
    });

  return (
    <Modal title={ext.data ? `Extension ${ext.data.number}` : "Extension"} onClose={onClose}>
      {!ext.data || !current ? (
        <>
          <ErrorNote error={ext.error} />
          <Loading />
        </>
      ) : (
        <form onSubmit={save}>
          <Field label="Extension number">
            <input
              value={current.number}
              onChange={(e) => setForm({ ...current, number: e.target.value })}
              inputMode="numeric"
              pattern="[1-9][0-9]{1,5}"
              required
            />
          </Field>
          <Field label="Name">
            <input value={current.name} onChange={(e) => setForm({ ...current, name: e.target.value })} maxLength={64} required />
          </Field>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={current.enabled}
              onChange={(e) => setForm({ ...current, enabled: e.target.checked })}
            />
            Enabled (disabled extensions can't register or be called)
          </label>

          <div className="field">
            <span className="field-label">Phone login</span>
            <div className="login-box">
              <div>
                Extension <code>{ext.data.number}</code>
              </div>
              <div>
                Password <Secret value={ext.data.sip_password} />
              </div>
            </div>
            <span className="field-hint">
              Phones assigned on the Phones page log in automatically. Otherwise, enter these on the phone.
            </span>
            <div>
              <button type="button" className="btn btn-small" onClick={regenerate} disabled={busy}>
                New password
              </button>
            </div>
          </div>

          <ErrorNote error={error} />
          {confirmDelete ? (
            <Note kind="warn">
              Delete extension {ext.data.number}? Its phone will be unassigned.{" "}
              <button type="button" className="btn btn-small btn-danger" onClick={remove} disabled={busy}>
                Delete
              </button>{" "}
              <button type="button" className="btn btn-small btn-ghost" onClick={() => setConfirmDelete(false)}>
                Keep
              </button>
            </Note>
          ) : null}
          <div className="form-actions">
            <button type="button" className="btn btn-danger-ghost" onClick={() => setConfirmDelete(true)}>
              Delete
            </button>
            <span className="spacer" />
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button className="btn btn-primary" disabled={busy}>
              Save
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
