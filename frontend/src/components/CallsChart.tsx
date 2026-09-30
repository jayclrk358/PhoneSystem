import { useEffect, useRef, useState } from "react";
import type { DailyCalls, Direction } from "../api";

// Fixed order: bottom to top of each stacked column, and legend order. Colors
// come from --series-* in styles.css (validated categorical slots 1-3).
export const SERIES: { key: Direction; label: string; color: string }[] = [
  { key: "inbound", label: "Incoming", color: "var(--series-1)" },
  { key: "outbound", label: "Outgoing external", color: "var(--series-2)" },
  { key: "internal", label: "Internal", color: "var(--series-3)" },
];

const HEIGHT = 220;
const PAD = { top: 12, right: 8, bottom: 28, left: 36 };
const GAP = 2; // surface gap between stacked segments
const RADIUS = 4; // rounded data-end

/** Clean tick step (1, 2, 5 × 10^n) giving about four gridlines. */
export function tickStep(max: number): number {
  if (max <= 4) return 1;
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
}

/** A column segment with a rounded top and square bottom. */
function topRounded(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, w / 2, h);
  return [
    `M${x},${y + h}`,
    `V${y + rr}`,
    `Q${x},${y} ${x + rr},${y}`,
    `H${x + w - rr}`,
    `Q${x + w},${y} ${x + w},${y + rr}`,
    `V${y + h}`,
    "Z",
  ].join(" ");
}

function dayLabel(date: string, long = false): string {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString(undefined, {
    weekday: long ? "long" : "short",
    month: "short",
    day: "numeric",
  });
}

export function CallsChart({ daily }: { daily: DailyCalls[] }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [active, setActive] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setWidth(Math.max(280, Math.floor(w)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const totals = SERIES.map((s) => daily.reduce((sum, d) => sum + d[s.key], 0));
  const dayTotals = daily.map((d) => SERIES.reduce((sum, s) => sum + d[s.key], 0));
  const max = Math.max(1, ...dayTotals);
  const step = tickStep(max);
  const top = Math.ceil(max / step) * step;
  const plotW = width - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const slot = plotW / Math.max(1, daily.length);
  const barW = Math.max(2, Math.min(24, slot * 0.6));
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const labelEvery = Math.max(1, Math.ceil(daily.length / Math.max(1, Math.floor(plotW / 64))));

  const activeDay = active !== null ? daily[active] : undefined;
  const tipLeft = active !== null ? PAD.left + slot * active + slot / 2 : 0;

  return (
    <div className="chart">
      <div className="chart-head">
        <ul className="legend" aria-label="Legend">
          {SERIES.map((s, i) => (
            <li key={s.key}>
              <span className="legend-swatch" style={{ background: s.color }} aria-hidden />
              {s.label} <strong>{totals[i]!.toLocaleString()}</strong>
            </li>
          ))}
        </ul>
        <button type="button" className="btn btn-small btn-ghost" onClick={() => setShowTable(!showTable)}>
          {showTable ? "Hide table" : "Show as table"}
        </button>
      </div>

      <div className="chart-plot" ref={wrap}>
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={`Calls per day, ${daily.length} days`}
          onMouseLeave={() => setActive(null)}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="chart-grid" />
              <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="chart-tick">
                {t.toLocaleString()}
              </text>
            </g>
          ))}
          {daily.map((d, i) => {
            const x = PAD.left + slot * i + (slot - barW) / 2;
            let base = y(0);
            const parts = SERIES.map((s) => ({ ...s, value: d[s.key] })).filter((p) => p.value > 0);
            return (
              <g key={d.date}>
                {parts.map((p, j) => {
                  const h = (p.value / top) * plotH;
                  const isTop = j === parts.length - 1;
                  // Leave the surface gap above every segment but the top one.
                  const drawH = Math.max(1, isTop ? h : h - GAP);
                  const yTop = base - h + (isTop ? 0 : GAP);
                  base -= h;
                  return isTop ? (
                    <path key={p.key} d={topRounded(x, yTop, barW, drawH, RADIUS)} fill={p.color} />
                  ) : (
                    <rect key={p.key} x={x} y={yTop} width={barW} height={drawH} fill={p.color} />
                  );
                })}
                {i % labelEvery === 0 && (
                  <text x={x + barW / 2} y={HEIGHT - 8} textAnchor="middle" className="chart-tick">
                    {dayLabel(d.date)}
                  </text>
                )}
                {/* Hit target: the whole day slot, not just the painted bar. */}
                <rect
                  x={PAD.left + slot * i}
                  y={PAD.top}
                  width={slot}
                  height={plotH}
                  className={`chart-hit ${active === i ? "active" : ""}`}
                  tabIndex={0}
                  aria-label={`${dayLabel(d.date, true)}: ${SERIES.map((s) => `${s.label} ${d[s.key]}`).join(", ")}`}
                  onMouseEnter={() => setActive(i)}
                  onFocus={() => setActive(i)}
                  onBlur={() => setActive(null)}
                />
              </g>
            );
          })}
          <line x1={PAD.left} x2={width - PAD.right} y1={y(0)} y2={y(0)} className="chart-baseline" />
        </svg>
        {activeDay && (
          <div
            className="chart-tooltip"
            style={{
              left: Math.min(Math.max(tipLeft, 90), width - 90),
              top: PAD.top,
            }}
            role="status"
          >
            <div className="chart-tooltip-title">{dayLabel(activeDay.date, true)}</div>
            {SERIES.map((s) => (
              <div key={s.key} className="chart-tooltip-row">
                <span className="line-key" style={{ background: s.color }} aria-hidden />
                <strong>{activeDay[s.key].toLocaleString()}</strong>
                <span className="muted">{s.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {showTable && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Day</th>
                {SERIES.map((s) => (
                  <th key={s.key} className="num">
                    {s.label}
                  </th>
                ))}
                <th className="num">Total</th>
              </tr>
            </thead>
            <tbody>
              {daily.map((d, i) => (
                <tr key={d.date}>
                  <td>{dayLabel(d.date, true)}</td>
                  {SERIES.map((s) => (
                    <td key={s.key} className="num">
                      {d[s.key].toLocaleString()}
                    </td>
                  ))}
                  <td className="num">{dayTotals[i]!.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
