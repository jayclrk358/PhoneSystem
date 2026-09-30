import { Link } from "react-router-dom";
import type {
  CallStats,
  Extension,
  FirmwareInfo,
  LiveStatus,
  Phone,
  ProvisioningRequest,
  Settings,
} from "../api";
import { CopyButton, ErrorNote, PageHeader, Pill } from "../components/ui";
import { dateRange, formatMac, option242, timeAgo } from "../format";
import { useApi } from "../hooks";

export function DashboardPage() {
  const status = useApi<LiveStatus>("/api/status", 5000);
  const extensions = useApi<Extension[]>("/api/extensions");
  const phones = useApi<Phone[]>("/api/phones", 10000);
  const settings = useApi<Settings>("/api/settings");
  const firmware = useApi<FirmwareInfo>("/api/firmware");
  const requests = useApi<ProvisioningRequest[]>("/api/provisioning/requests?limit=8", 10000);
  const today = dateRange("today");
  const calls = useApi<CallStats>(
    `/api/calls/stats?start=${encodeURIComponent(today.start.toISOString())}` +
      `&end=${encodeURIComponent(today.end.toISOString())}` +
      `&tz=${encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC")}`,
    15000,
  );

  const exts = extensions.data ?? [];
  const nameOf = new Map(exts.map((e) => [e.number, e.name]));
  const regs = status.data?.registrations ?? [];
  const serverIp = settings.data?.server_ip || settings.data?.detected_server_ip || "";
  const dhcp = serverIp && settings.data ? option242(serverIp, settings.data.provisioning_port) : "";

  const steps = [
    {
      done: !!settings.data?.server_ip,
      text: (
        <>
          Confirm the server's IP address in <Link to="/settings">Settings</Link>
          {settings.data?.detected_server_ip && !settings.data.server_ip
            ? ` (detected: ${settings.data.detected_server_ip})`
            : ""}
        </>
      ),
    },
    {
      done: exts.length > 0,
      text: (
        <>
          Add <Link to="/extensions">extensions</Link> for your users
        </>
      ),
    },
    {
      done: (requests.data ?? []).length > 0,
      text: (
        <>
          Point the phones here with DHCP option 242{" "}
          {dhcp && (
            <>
              <code>{dhcp}</code> <CopyButton text={dhcp} />
            </>
          )}
        </>
      ),
    },
    {
      done: (firmware.data?.files ?? []).length > 0,
      text: (
        <>
          Upload the Avaya 96x1 SIP <Link to="/firmware">firmware</Link> (needed to convert H.323 phones)
        </>
      ),
    },
    {
      done: (phones.data ?? []).some((p) => p.extension_id !== null),
      text: (
        <>
          Assign each <Link to="/phones">phone</Link> to an extension
        </>
      ),
    },
  ];

  return (
    <>
      <PageHeader title="Dashboard" />
      <ErrorNote error={status.error} />

      <div className="stats">
        <div className="stat card">
          <span className="stat-label">Asterisk</span>
          {status.data ? (
            status.data.asterisk.reachable ? (
              <Pill kind="ok">Running</Pill>
            ) : (
              <Pill kind="bad">Not reachable</Pill>
            )
          ) : (
            <Pill kind="muted">…</Pill>
          )}
          <span className="stat-sub" title={status.data?.asterisk.message}>
            {status.data?.asterisk.reachable
              ? status.data.asterisk.message.split(" built")[0]
              : status.data?.asterisk.message}
          </span>
        </div>
        <div className="stat card">
          <span className="stat-label">Extensions</span>
          <span className="stat-value">{exts.length}</span>
        </div>
        <Link to="/calls?range=today" className="stat card stat-link">
          <span className="stat-label">Calls today</span>
          <span className="stat-value">{calls.data?.totals.calls ?? "…"}</span>
          <span className="stat-sub">
            {calls.data
              ? `${calls.data.totals.inbound} in · ${calls.data.totals.outbound} out · ${calls.data.totals.missed + calls.data.totals.busy} missed`
              : ""}
          </span>
        </Link>
        <div className="stat card">
          <span className="stat-label">Registered now</span>
          <span className="stat-value">{regs.length}</span>
        </div>
        <div className="stat card">
          <span className="stat-label">Phones</span>
          <span className="stat-value">{phones.data?.length ?? "…"}</span>
          <span className="stat-sub">
            {(phones.data ?? []).filter((p) => p.extension_id === null).length} not assigned
          </span>
        </div>
      </div>

      {steps.some((s) => !s.done) && (
        <section className="card">
          <h2>Getting started</h2>
          <ol className="checklist">
            {steps.map((s, i) => (
              <li key={i} className={s.done ? "done" : ""}>
                <span className="check" aria-hidden>
                  {s.done ? "✓" : i + 1}
                </span>
                <span>{s.text}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section className="card">
        <h2>Registered phones</h2>
        {regs.length === 0 ? (
          <p className="muted">No phones are registered right now.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Extension</th>
                  <th>Name</th>
                  <th>Address</th>
                  <th>Device</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {regs.map((r) => (
                  <tr key={r.uri}>
                    <td>{r.endpoint}</td>
                    <td>{nameOf.get(r.endpoint) ?? ""}</td>
                    <td>
                      <code>{r.uri.replace(/^sips?:[^@]*@/, "")}</code>
                    </td>
                    <td className="muted">{r.user_agent}</td>
                    <td>
                      <Pill kind={r.status === "Reachable" || r.status === "NonQualified" ? "ok" : "warn"}>
                        {r.status}
                      </Pill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Recent phone provisioning</h2>
        {(requests.data ?? []).length === 0 ? (
          <p className="muted">No phone has asked for its settings yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Phone</th>
                  <th>File</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {(requests.data ?? []).map((r, i) => (
                  <tr key={i}>
                    <td>{timeAgo(r.at)}</td>
                    <td>
                      {r.mac ? formatMac(r.mac) : <span className="muted">unknown</span>}{" "}
                      <span className="muted">{r.ip}</span>
                    </td>
                    <td>
                      <code>{r.path}</code>
                    </td>
                    <td>
                      <Pill kind={r.status === 200 ? "ok" : "warn"}>{r.status}</Pill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
