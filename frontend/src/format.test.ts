import { describe, expect, it } from "vitest";
import { errorMessage } from "./api";
import { formatBytes, formatMac, option242, parseTime, timeAgo } from "./format";

describe("format", () => {
  it("formats MAC addresses", () => {
    expect(formatMac("001B4FAABBCC")).toBe("00:1b:4f:aa:bb:cc");
    expect(formatMac("00-1b-4f-aa-bb-cc")).toBe("00:1b:4f:aa:bb:cc");
    expect(formatMac(null)).toBe("");
  });

  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("treats timestamps without an offset as UTC", () => {
    expect(parseTime("2026-09-30T12:00:00").toISOString()).toBe("2026-09-30T12:00:00.000Z");
    expect(parseTime("2026-09-30T12:00:00+00:00").toISOString()).toBe("2026-09-30T12:00:00.000Z");
  });

  it("says how long ago", () => {
    const now = new Date("2026-09-30T12:00:00Z");
    expect(timeAgo("2026-09-30T11:59:50Z", now)).toBe("just now");
    expect(timeAgo("2026-09-30T11:50:00Z", now)).toBe("10 min ago");
    expect(timeAgo("2026-09-30T09:00:00Z", now)).toBe("3 h ago");
    expect(timeAgo(null, now)).toBe("never");
  });

  it("builds the DHCP option 242 string", () => {
    expect(option242("10.0.0.5", 80)).toBe("HTTPSRVR=10.0.0.5,SIG=2");
    expect(option242("10.0.0.5", 8080)).toBe("HTTPSRVR=10.0.0.5,HTTPPORT=8080,SIG=2");
  });
});

describe("errorMessage", () => {
  it("uses a string detail as-is", () => {
    expect(errorMessage(409, { detail: "extension 101 already exists" })).toBe("extension 101 already exists");
  });

  it("flattens validation errors", () => {
    const body = {
      detail: [
        { loc: ["body", "number"], msg: "Value error, extension must be 2-6 digits" },
        { loc: ["body", "name"], msg: "Field required" },
      ],
    };
    expect(errorMessage(422, body)).toBe("number: extension must be 2-6 digits; name: Field required");
  });

  it("falls back to the status code", () => {
    expect(errorMessage(502, null)).toBe("Request failed (HTTP 502)");
  });
});
