// Thin client for the PhoneSystem admin API.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type ValidationItem = { loc?: (string | number)[]; msg?: string };

/** Turn FastAPI error bodies into one readable sentence. */
export function errorMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return (detail as ValidationItem[])
        .map((d) => {
          const field = d.loc?.filter((p) => p !== "body").join(".");
          const msg = (d.msg ?? "invalid value").replace(/^Value error, /, "");
          return field ? `${field}: ${msg}` : msg;
        })
        .join("; ");
    }
  }
  return `Request failed (HTTP ${status})`;
}

// Listeners told about anything that may have changed the generated config.
const changeListeners = new Set<() => void>();
export function onConfigChange(fn: () => void): () => void {
  changeListeners.add(fn);
  return () => changeListeners.delete(fn);
}
function notifyConfigChange() {
  changeListeners.forEach((fn) => fn());
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "PhoneSystem" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/auth/")) onUnauthorized?.();
    throw new ApiError(res.status, errorMessage(res.status, data));
  }
  if (method !== "GET") notifyConfigChange();
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<void>("DELETE", path),
};

// ---------------------------------------------------------------- types

export interface AuthStatus {
  setup_required: boolean;
  user: { username: string } | null;
}

export interface Extension {
  id: number;
  number: string;
  name: string;
  enabled: boolean;
  phone_id: number | null;
  phone_mac: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExtensionDetail extends Extension {
  sip_password: string;
}

export interface Phone {
  id: number;
  mac: string;
  label: string;
  model: string;
  extension_id: number | null;
  extension_number: string | null;
  extension_name: string | null;
  last_ip: string | null;
  last_seen_at: string | null;
  user_agent: string | null;
  firmware_version: string | null;
  discovered: boolean;
  created_at: string;
}

export interface ProvisioningRequest {
  at: string;
  ip: string;
  mac: string | null;
  method: string;
  path: string;
  status: number;
  size: number;
  user_agent: string | null;
}

export interface LogLine {
  at: string;
  ip: string;
  message: string;
}

export interface Settings {
  server_ip: string | null;
  sip_domain: string | null;
  sip_tcp_port: number;
  phone_admin_password: string;
  ntp_server: string | null;
  gmt_offset: string;
  dst_offset: number;
  dst_start: string;
  dst_stop: string;
  register_interval: number;
  inter_digit_timeout: number;
  dialplan_override: string;
  phone_syslog: boolean;
  extra_46xx_settings: string;
  detected_server_ip: string | null;
  provisioning_port: number;
  syslog_port: number;
}

export interface FirmwareFile {
  name: string;
  size: number;
  modified: string;
  served: boolean;
}

export interface FirmwareInfo {
  files: FirmwareFile[];
  upgrade_script: string;
  upgrade_script_source: "uploaded" | "generated";
}

export interface ConfigVersion {
  id: number;
  created_at: string;
  created_by: string;
  checksum: string;
  status: "applied" | "failed" | "superseded" | "pending";
  message: string;
}

export interface ConfigStatus {
  pending: boolean;
  live_checksum: string | null;
  latest: ConfigVersion | null;
}

export interface Registration {
  endpoint: string;
  uri: string;
  status: string;
  user_agent: string;
  via_address: string;
  roundtrip_usec: string;
}

export interface LiveStatus {
  asterisk: { reachable: boolean; message: string };
  endpoints?: Record<string, string>;
  registrations: Registration[];
}

export interface AuditEntry {
  at: string;
  username: string;
  action: string;
  target: string;
  detail: Record<string, unknown> | null;
}
