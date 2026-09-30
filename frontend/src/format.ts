export function formatMac(mac: string | null | undefined): string {
  if (!mac) return "";
  const hex = mac.replace(/[^0-9a-f]/gi, "").toLowerCase();
  if (hex.length !== 12) return mac;
  return hex.match(/../g)!.join(":");
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** API timestamps are UTC; SQLite may drop the offset, so assume UTC. */
export function parseTime(value: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "never";
  return parseTime(value).toLocaleString();
}

export function timeAgo(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return "never";
  const seconds = Math.round((now.getTime() - parseTime(value).getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} d ago`;
}

/** DHCP option 242 string for Avaya phones (tells them where to fetch settings). */
export function option242(serverIp: string, port: number): string {
  const parts = [`HTTPSRVR=${serverIp}`];
  if (port !== 80) parts.push(`HTTPPORT=${port}`);
  parts.push("SIG=2");
  return parts.join(",");
}

/** 75 -> "1:15", 3725 -> "1:02:05". */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

/** Exact talk time: "0 min", "45 min", "12 h 5 min". */
export function formatTalkTime(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** Compact talk time for a stat tile: "45 min", "4.9 h", "123 h". */
export function formatTalkTimeShort(seconds: number): string {
  const hours = seconds / 3600;
  if (hours < 1) return `${Math.round(seconds / 60)} min`;
  return hours < 100 ? `${hours.toFixed(1)} h` : `${Math.round(hours)} h`;
}

export type RangePreset = "today" | "7d" | "30d" | "90d" | "custom";

/** Local-midnight date range for a preset: [start, end). Custom uses YYYY-MM-DD strings. */
export function dateRange(
  preset: RangePreset,
  from?: string | null,
  to?: string | null,
  now: Date = new Date(),
): { start: Date; end: Date } {
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const today = midnight(now);
  const tomorrow = addDays(today, 1);
  if (preset === "custom" && from && to) {
    const [fy, fm, fd] = from.split("-").map(Number);
    const [ty, tm, td] = to.split("-").map(Number);
    const start = new Date(fy!, fm! - 1, fd!);
    const end = addDays(new Date(ty!, tm! - 1, td!), 1);
    if (end > start) return { start, end };
  }
  const days = { today: 1, "7d": 7, "30d": 30, "90d": 90, custom: 7 }[preset];
  return { start: addDays(today, -(days - 1)), end: tomorrow };
}

/** "2026-09-30" in local time. */
export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
