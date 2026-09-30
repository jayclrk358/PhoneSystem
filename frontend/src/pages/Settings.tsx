import { type FormEvent, useEffect, useState } from "react";
import { api, type Settings } from "../api";
import { CopyButton, ErrorNote, Field, Loading, Note, PageHeader } from "../components/ui";
import { option242 } from "../format";
import { useAction, useApi } from "../hooks";

export function SettingsPage() {
  const loaded = useApi<Settings>("/api/settings");
  const [form, setForm] = useState<Settings | null>(null);
  const [saved, setSaved] = useState(false);
  const { busy, error, run } = useAction();

  useEffect(() => {
    if (loaded.data) setForm(loaded.data);
  }, [loaded.data]);

  if (!form) return loaded.error ? <ErrorNote error={loaded.error} /> : <Loading />;

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setSaved(false);
    setForm({ ...form, [key]: value });
  };
  const text = (key: keyof Settings) => ({
    value: (form[key] ?? "") as string,
    onChange: (e: { target: { value: string } }) => set(key, e.target.value as never),
  });
  const num = (key: keyof Settings) => ({
    type: "number",
    value: form[key] as number,
    onChange: (e: { target: { value: string } }) => set(key, Number(e.target.value) as never),
  });

  const save = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const body = { ...form, server_ip: form.server_ip || null, sip_domain: form.sip_domain || null };
      setForm(await api.put<Settings>("/api/settings", body));
      setSaved(true);
    });
  };

  const serverIp = form.server_ip || form.detected_server_ip || "";
  const dhcp = serverIp ? option242(serverIp, form.provisioning_port) : "";
  const portChanged = loaded.data && loaded.data.sip_tcp_port !== form.sip_tcp_port;

  return (
    <>
      <PageHeader title="Settings" />
      <form onSubmit={save}>
        <section className="card">
          <h2>Server</h2>
          <Field
            label="Server IP address"
            hint={
              <>
                The phones connect to this numeric IPv4 address (they can't use a hostname here).
                {form.detected_server_ip && !form.server_ip && (
                  <>
                    {" "}
                    Detected: <code>{form.detected_server_ip}</code>{" "}
                    <button
                      type="button"
                      className="btn btn-small"
                      onClick={() => set("server_ip", form.detected_server_ip)}
                    >
                      Use it
                    </button>
                  </>
                )}
              </>
            }
          >
            <input {...text("server_ip")} placeholder={form.detected_server_ip ?? "192.168.1.10"} inputMode="decimal" />
          </Field>
          <Field label="SIP domain" hint="Leave empty to use the server IP.">
            <input {...text("sip_domain")} placeholder={serverIp} />
          </Field>
          <Field label="SIP port" hint="Phones connect over TCP; UDP on the same port is for other devices.">
            <input {...num("sip_tcp_port")} min={1} max={65535} />
          </Field>
          {portChanged && <Note kind="warn">Changing the SIP port only takes effect after Asterisk restarts.</Note>}
        </section>

        <section className="card">
          <h2>Pointing phones at this server</h2>
          <p className="muted small">
            On the DHCP server for the phones' network (usually your router), set option <strong>242</strong> (type:
            string) to:
          </p>
          {dhcp ? (
            <p>
              <code className="big-code">{dhcp}</code> <CopyButton text={dhcp} />
            </p>
          ) : (
            <p className="muted">Set the server IP address first.</p>
          )}
          <p className="muted small">
            <code>SIG=2</code> tells phones with H.323 firmware to switch to SIP. Phones fetch their settings from port{" "}
            {form.provisioning_port} and send logs to UDP port {form.syslog_port || "(disabled)"}.
          </p>
        </section>

        <section className="card">
          <h2>Phones</h2>
          <Field label="Phone admin menu password" hint="4–7 digits. Protects the phones' local (craft) admin menu.">
            <input {...text("phone_admin_password")} inputMode="numeric" pattern="\d{4,7}" required />
          </Field>
          <Field label="Registration interval (seconds)">
            <input {...num("register_interval")} min={30} max={86400} />
          </Field>
          <Field label="Seconds to wait between digits before dialing">
            <input {...num("inter_digit_timeout")} min={1} max={10} />
          </Field>
          <Field
            label="Dial plan override"
            hint="Leave empty to generate it from your extension numbers. Avaya syntax, e.g. 1XX|9Z1XXXXXXXXXX."
          >
            <input {...text("dialplan_override")} />
          </Field>
          <label className="checkbox">
            <input type="checkbox" checked={form.phone_syslog} onChange={(e) => set("phone_syslog", e.target.checked)} />
            Ask phones to send their logs to this server
          </label>
        </section>

        <section className="card">
          <h2>Time</h2>
          <Field label="Time server (NTP)">
            <input {...text("ntp_server")} />
          </Field>
          <div className="grid-2">
            <Field label="Offset from GMT" hint='e.g. "-5:00" for US Eastern.'>
              <input {...text("gmt_offset")} />
            </Field>
            <Field label="Daylight saving offset (hours)">
              <select value={form.dst_offset} onChange={(e) => set("dst_offset", Number(e.target.value))}>
                <option value={0}>None</option>
                <option value={1}>1 hour</option>
                <option value={2}>2 hours</option>
              </select>
            </Field>
            <Field label="Daylight saving starts" hint='Avaya rule, e.g. "2SunMar2L".'>
              <input {...text("dst_start")} />
            </Field>
            <Field label="Daylight saving ends" hint='e.g. "1SunNov2L".'>
              <input {...text("dst_stop")} />
            </Field>
          </div>
        </section>

        <section className="card">
          <h2>Advanced</h2>
          <Field
            label="Extra lines for 46xxsettings.txt"
            hint="Added to every phone's settings file. One SET, GET, IF or GOTO statement (or ## comment) per line."
          >
            <textarea {...text("extra_46xx_settings")} rows={6} spellCheck={false} className="mono" />
          </Field>
        </section>

        <div className="sticky-actions">
          <ErrorNote error={error} />
          {saved && (
            <Note kind="ok">
              Saved. Phones pick up settings-file changes when they restart; Asterisk changes need "Apply changes".
            </Note>
          )}
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Saving…" : "Save settings"}
          </button>
        </div>
      </form>
    </>
  );
}
