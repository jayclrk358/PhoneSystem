import { type FormEvent, useRef, useState } from "react";
import { api, type FirmwareInfo } from "../api";
import { ErrorNote, Loading, Note, PageHeader, Pill } from "../components/ui";
import { formatBytes, formatTime } from "../format";
import { useAction, useApi } from "../hooks";

export function FirmwarePage() {
  const fw = useApi<FirmwareInfo>("/api/firmware", undefined, ["firmware"]);
  const fileInput = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<{ saved: string[]; skipped: string[] } | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const { busy, error, run } = useAction();

  const upload = (e: FormEvent) => {
    e.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    run(async () => {
      setResult(await api.post<{ saved: string[]; skipped: string[] }>("/api/firmware", form));
      if (fileInput.current) fileInput.current.value = "";
      fw.reload();
    });
  };

  const remove = (name: string) =>
    run(async () => {
      await api.del(`/api/firmware/${encodeURIComponent(name)}`);
      fw.reload();
    });

  const clearAll = () =>
    run(async () => {
      await api.del("/api/firmware");
      setConfirmClear(false);
      fw.reload();
    });

  return (
    <>
      <PageHeader title="Firmware" />
      <p className="muted">
        The 9608 needs Avaya's <strong>96x1 SIP</strong> firmware (7.1.x) to work with this system. Phones that still
        run H.323 firmware switch over when they boot with <code>SIG=2</code> in DHCP option 242 and find the SIP
        firmware here. Get the zip from Avaya support or your reseller and upload it as-is.
      </p>

      <section className="card">
        <h2>Upload</h2>
        <form onSubmit={upload} className="inline">
          <input ref={fileInput} type="file" accept=".zip,.bin,.txt,.tar" required />
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Uploading…" : "Upload"}
          </button>
        </form>
        <ErrorNote error={error} />
        {result && (
          <Note kind="ok">
            Saved {result.saved.length} file{result.saved.length === 1 ? "" : "s"}.
            {result.skipped.length > 0 && ` Skipped (names phones can't request): ${result.skipped.join(", ")}.`}
          </Note>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Files served to phones</h2>
          {(fw.data?.files ?? []).length > 0 &&
            (confirmClear ? (
              <span className="inline">
                Delete all firmware files?
                <button type="button" className="btn btn-small btn-danger" onClick={clearAll} disabled={busy}>
                  Delete all
                </button>
                <button type="button" className="btn btn-small btn-ghost" onClick={() => setConfirmClear(false)}>
                  Cancel
                </button>
              </span>
            ) : (
              <button type="button" className="btn btn-small btn-danger-ghost" onClick={() => setConfirmClear(true)}>
                Delete all
              </button>
            ))}
        </div>
        {fw.loading && !fw.data ? (
          <Loading />
        ) : (fw.data?.files ?? []).length === 0 ? (
          <p className="muted">No firmware uploaded.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>File</th>
                  <th>Size</th>
                  <th>Uploaded</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(fw.data?.files ?? []).map((f) => (
                  <tr key={f.name}>
                    <td>
                      <code>{f.name}</code>{" "}
                      {!f.served && <Pill kind="muted">not served (we generate this file)</Pill>}
                    </td>
                    <td>{formatBytes(f.size)}</td>
                    <td>{formatTime(f.modified)}</td>
                    <td className="actions">
                      <button type="button" className="btn btn-small btn-ghost" onClick={() => remove(f.name)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Upgrade script</h2>
        <p className="muted small">
          Phones read this first (<code>96x1Supgrade.txt</code>).{" "}
          {fw.data?.upgrade_script_source === "uploaded"
            ? "This is Avaya's script from the uploaded bundle; it upgrades firmware and then loads the settings file."
            : "No firmware bundle is uploaded, so this minimal script only loads the settings file."}
        </p>
        <pre className="code-block">{fw.data?.upgrade_script ?? ""}</pre>
      </section>
    </>
  );
}
