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
