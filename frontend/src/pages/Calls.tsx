import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { CallPage, CallRecord, CallStats, CallStatus, Direction } from "../api";
import { CallsChart } from "../components/CallsChart";
import { ErrorNote, Loading, PageHeader, Pill } from "../components/ui";
import {
  dateRange,
  formatDuration,
  formatTalkTime,
  formatTalkTimeShort,
  formatTime,
  isoDate,
  type RangePreset,
} from "../format";
import { useApi } from "../hooks";

const PRESETS: { key: RangePreset; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "90d", label: "90 days" },
  { key: "custom", label: "Custom" },
];
const DIRECTION_LABEL: Record<Direction, string> = {
  inbound: "Incoming",
  outbound: "Outgoing",
  internal: "Internal",
};
const DIRECTION_ICON: Record<Direction, string> = { inbound: "↙", outbound: "↗", internal: "⇄" };
const STATUS_KIND: Record<CallStatus, "ok" | "warn" | "bad" | "muted"> = {
  answered: "ok",
  missed: "warn",
  busy: "warn",
  failed: "bad",
};
const PAGE_SIZE = 50;
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

export function CallsPage() {
  const [params, setParams] = useSearchParams();
  const preset = (params.get("range") as RangePreset) || "7d";
  const from = params.get("from");
  const to = params.get("to");
  const q = params.get("q") ?? "";
  const direction = params.get("direction") ?? "";
  const status = params.get("status") ?? "";
  const extension = params.get("extension") ?? "";
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);

  const set = (changes: Record<string, string | null>, resetPage = true) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    if (resetPage) next.delete("page");
    setParams(next, { replace: true });
  };

  // Recompute the range when the day changes, not on every render.
  const today = isoDate(new Date());
  const { start, end } = useMemo(() => dateRange(preset, from, to), [preset, from, to, today]);
  const range = `start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`;

  const stats = useApi<CallStats>(`/api/calls/stats?${range}&tz=${encodeURIComponent(TZ)}`, 15000, [
    "calls",
    "extensions",
    "phones",
  ]);

  // Search box: type freely, query after a short pause.
  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);
  useEffect(() => {
    if (search === q) return;
    const t = window.setTimeout(() => set({ q: search.trim() || null }), 350);
    return () => window.clearTimeout(t);
  }, [search]); // only when the text changes

  const filters = new URLSearchParams();
  if (q) filters.set("q", q);
  if (direction) filters.set("direction", direction);
  if (status) filters.set("status", status);
  if (extension) filters.set("extension", extension);
  const filterQs = filters.toString();
  const log = useApi<CallPage>(
    `/api/calls?${range}${filterQs ? `&${filterQs}` : ""}&page=${page}&page_size=${PAGE_SIZE}`,
    15000,
    ["calls"],
  );
  const exportHref = `/api/calls/export.csv?${range}${filterQs ? `&${filterQs}` : ""}`;

  const t = stats.data?.totals;
  const names = new Map((stats.data?.per_extension ?? []).map((e) => [e.extension, e.name]));
  const pages = Math.max(1, Math.ceil((log.data?.total ?? 0) / PAGE_SIZE));

  return (
    <>
      <PageHeader title="Calls" />

      <div className="filter-bar">
        <div className="segmented" role="group" aria-label="Date range">
          {PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              className={p.key === preset ? "active" : ""}
              aria-pressed={p.key === preset}
              onClick={() =>
                set(
                  p.key === "custom"
                    ? { range: "custom", from: from ?? isoDate(start), to: to ?? isoDate(new Date(end.getTime() - 1)) }
                    : { range: p.key === "7d" ? null : p.key, from: null, to: null },
                )
              }
            >
              {p.label}
            </button>
          ))}
        </div>
        {preset === "custom" && (
          <div className="inline">
            <input
              type="date"
              aria-label="From"
              value={from ?? isoDate(start)}
              max={to ?? undefined}
              onChange={(e) => e.target.value && set({ from: e.target.value })}
            />
            <span className="muted">to</span>
            <input
              type="date"
              aria-label="To"
              value={to ?? isoDate(new Date(end.getTime() - 1))}
              min={from ?? undefined}
              onChange={(e) => e.target.value && set({ to: e.target.value })}
            />
          </div>
        )}
      </div>

      <ErrorNote error={stats.error} />
      <div className="stats">
        <StatTile label="Calls" value={t?.calls} />
        <StatTile
          label="Incoming"
          value={t?.inbound}
          sub={t ? `${t.inbound_missed.toLocaleString()} not answered` : undefined}
        />
        <StatTile label="Outgoing external" value={t?.outbound} />
        <StatTile label="Internal" value={t?.internal} />
        <StatTile label="Missed" value={t ? t.missed + t.busy : undefined} sub="rang out or busy" />
        <StatTile
          label="Talk time"
          text={t ? formatTalkTimeShort(t.talk_seconds) : undefined}
          sub={t && t.talk_seconds >= 3600 ? formatTalkTime(t.talk_seconds) : undefined}
        />
      </div>
      {t && t.inbound + t.outbound === 0 && (
        <p className="muted small">
          Incoming and outgoing external calls are counted once an outside line (SIP trunk) is set up.
        </p>
      )}

      <section className="card">
        <h2>Calls per day</h2>
        {!stats.data ? (
          <Loading />
        ) : t && t.calls === 0 ? (
          <p className="muted">No calls in this period.</p>
        ) : (
          <CallsChart daily={stats.data.daily} />
        )}
      </section>

      <section className="card">
        <h2>By extension</h2>
        {!stats.data ? (
          <Loading />
        ) : stats.data.per_extension.length === 0 ? (
          <p className="muted">No extensions yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Extension</th>
                  <th>Phone</th>
                  <th className="num">Outgoing</th>
                  <th className="num">of which external</th>
                  <th className="num">Incoming</th>
                  <th className="num">of which external</th>
                  <th className="num">Missed</th>
                  <th className="num">Talk time</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {stats.data.per_extension.map((e) => (
                  <tr key={e.extension} className={e.outgoing + e.incoming === 0 ? "row-disabled" : ""}>
                    <td>
                      <strong>{e.extension}</strong> {e.name || <span className="muted">(deleted)</span>}
                    </td>
                    <td className="muted">{e.phone ?? ""}</td>
                    <td className="num">{e.outgoing.toLocaleString()}</td>
                    <td className="num">{e.outgoing_external.toLocaleString()}</td>
                    <td className="num">{e.incoming.toLocaleString()}</td>
                    <td className="num">{e.incoming_external.toLocaleString()}</td>
                    <td className="num">{e.missed.toLocaleString()}</td>
                    <td className="num">{formatDuration(e.talk_seconds)}</td>
                    <td className="actions">
                      <button
                        type="button"
                        className="btn btn-small"
                        onClick={() => {
                          set({ extension: e.extension });
                          document.getElementById("call-log")?.scrollIntoView({ behavior: "smooth" });
                        }}
                      >
                        Calls
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" id="call-log">
        <div className="card-head">
          <h2>Call log</h2>
          <a className="btn btn-small" href={exportHref} download>
            Export CSV
          </a>
        </div>
        <div className="filter-bar">
          <input
            type="search"
            className="search"
            placeholder="Search number or name"
            aria-label="Search number or name"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select aria-label="Direction" value={direction} onChange={(e) => set({ direction: e.target.value })}>
            <option value="">All directions</option>
            <option value="inbound">Incoming</option>
            <option value="outbound">Outgoing external</option>
            <option value="internal">Internal</option>
          </select>
          <select aria-label="Result" value={status} onChange={(e) => set({ status: e.target.value })}>
            <option value="">All results</option>
            <option value="answered">Answered</option>
            <option value="missed">Missed</option>
            <option value="busy">Busy</option>
            <option value="failed">Failed</option>
          </select>
          <select aria-label="Extension" value={extension} onChange={(e) => set({ extension: e.target.value })}>
            <option value="">All extensions</option>
            {(stats.data?.per_extension ?? []).map((e) => (
              <option key={e.extension} value={e.extension}>
                {e.extension} {e.name}
              </option>
            ))}
          </select>
          {(q || direction || status || extension) && (
            <button
              type="button"
              className="btn btn-small btn-ghost"
              onClick={() => {
                setSearch("");
                set({ q: null, direction: null, status: null, extension: null });
              }}
            >
              Clear filters
            </button>
          )}
        </div>

        <ErrorNote error={log.error} />
        {log.data?.lookup && log.data.lookup.total > 0 && (
          <div className="lookup">
            <strong>{q}</strong>: {log.data.lookup.total.toLocaleString()} call
            {log.data.lookup.total === 1 ? "" : "s"} in this period · {log.data.lookup.answered} answered ·{" "}
            {log.data.lookup.missed} missed · first {formatTime(log.data.lookup.first_at)} · last{" "}
            {formatTime(log.data.lookup.last_at)}
          </div>
        )}
        {!log.data ? (
          <Loading />
        ) : log.data.items.length === 0 ? (
          <p className="muted">No calls match.</p>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Direction</th>
                    <th>From</th>
                    <th>To</th>
                    <th>Result</th>
                    <th className="num">Talk time</th>
                  </tr>
                </thead>
                <tbody>
                  {log.data.items.map((c) => (
                    <CallRow key={c.id} call={c} names={names} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <span className="muted">
                {log.data.total.toLocaleString()} call{log.data.total === 1 ? "" : "s"}
              </span>
              {pages > 1 && (
                <span className="inline">
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={page <= 1}
                    onClick={() => set({ page: String(page - 1) }, false)}
                  >
                    Previous
                  </button>
                  <span className="muted">
                    Page {page} of {pages}
                  </span>
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={page >= pages}
                    onClick={() => set({ page: String(page + 1) }, false)}
                  >
                    Next
                  </button>
                </span>
              )}
            </div>
          </>
        )}
      </section>
    </>
  );
}

function StatTile({
  label,
  value,
  text,
  sub,
}: {
  label: string;
  value?: number;
  text?: string;
  sub?: string;
}) {
  return (
    <div className="stat card">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{text ?? (value === undefined ? "…" : value.toLocaleString())}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

function Party({ number, name }: { number: string; name?: string }) {
  return (
    <span className="party">
      {name && <strong>{name}</strong>} <span className={name ? "muted" : ""}>{number || "unknown"}</span>
    </span>
  );
}

function CallRow({ call: c, names }: { call: CallRecord; names: Map<string, string> }) {
  const toName = c.to_extension ? names.get(c.to_extension) : undefined;
  const fromName = c.src_name || (c.from_extension ? names.get(c.from_extension) : undefined);
  return (
    <tr>
      <td>{formatTime(c.started_at)}</td>
      <td>
        <span className={`direction direction-${c.direction}`}>
          <span className="direction-key" aria-hidden />
          <span aria-hidden>{DIRECTION_ICON[c.direction]}</span> {DIRECTION_LABEL[c.direction]}
        </span>
      </td>
      <td>
        <Party number={c.src_number} name={fromName} />
      </td>
      <td>
        <Party number={c.dst_number} name={toName} />
        {c.trunk && <span className="muted small"> via {c.trunk.replace(/^trunk-/, "")}</span>}
      </td>
      <td>
        <Pill kind={STATUS_KIND[c.status]}>{c.status}</Pill>
      </td>
      <td className="num">{c.status === "answered" ? formatDuration(c.talk_seconds) : ""}</td>
    </tr>
  );
}
