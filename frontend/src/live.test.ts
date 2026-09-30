import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isLiveConnected, onLive, onLiveState, startLive, stopLive } from "./live";

class FakeEventSource {
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = 0;
  onerror: (() => void) | null = null;
  private handlers = new Map<string, ((e: MessageEvent) => void)[]>();
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.handlers.set(type, [...(this.handlers.get(type) ?? []), fn]);
  }
  emit(type: string, data: unknown = {}) {
    for (const fn of this.handlers.get(type) ?? []) fn({ data: JSON.stringify(data) } as MessageEvent);
  }
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

describe("live updates", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.useFakeTimers();
  });
  afterEach(() => {
    stopLive();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("calls only the listeners whose topics changed", () => {
    const calls = vi.fn();
    const phones = vi.fn();
    onLive(["calls"], calls);
    onLive(["phones", "status"], phones);
    startLive(() => undefined);
    const es = FakeEventSource.instances[0]!;
    expect(es.url).toBe("/api/events");
    es.emit("ready");
    expect(isLiveConnected()).toBe(true);
    es.emit("change", ["calls"]);
    expect(calls).toHaveBeenCalledTimes(1);
    expect(phones).not.toHaveBeenCalled();
    es.emit("change", ["status", "audit"]);
    expect(phones).toHaveBeenCalledTimes(1);
  });

  it("refreshes everything after reconnecting, in case events were missed", () => {
    const fn = vi.fn();
    onLive(["calls"], fn);
    startLive(() => undefined);
    const es = FakeEventSource.instances[0]!;
    es.emit("ready"); // first connect: nothing to catch up on
    expect(fn).not.toHaveBeenCalled();
    es.onerror?.(); // dropped; the browser reconnects by itself
    expect(isLiveConnected()).toBe(false);
    es.emit("ready");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("retries a refused connection and reports state", () => {
    const states: boolean[] = [];
    onLiveState((c) => states.push(c));
    startLive(() => undefined);
    const first = FakeEventSource.instances[0]!;
    first.emit("ready");
    first.readyState = FakeEventSource.CLOSED; // e.g. HTTP 502 while restarting
    first.onerror?.();
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(5000);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(states).toEqual([true, false]);
  });

  it("stops and signs out when the server ends the session", () => {
    const signedOut = vi.fn();
    startLive(signedOut);
    const es = FakeEventSource.instances[0]!;
    es.emit("signed-out");
    expect(signedOut).toHaveBeenCalledTimes(1);
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    vi.advanceTimersByTime(10000);
    expect(FakeEventSource.instances).toHaveLength(1); // no reconnect
  });
});
