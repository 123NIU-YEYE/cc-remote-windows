import assert from "node:assert/strict";
import { RelayWs } from "../src/ws.ts";
import { loadStartupReplayState, type StartupReplayState } from "../src/startup-replay.ts";

// Browser timers can be suspended and close() need not deliver onclose. Exercise
// those failures with the real socket client and a deterministic browser clock.
const originals = {
  setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout,
  setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval,
  now: Date.now, fetch: globalThis.fetch,
};
let now = 1000;
let timerId = 0;
const timers = new Map<number, { at: number; callback: () => void; interval?: number }>();
const schedule = (callback: () => void, ms: number, interval?: number): number => {
  const id = ++timerId;
  timers.set(id, { at: now + ms, callback, interval });
  return id;
};
Object.assign(globalThis, {
  setTimeout: (callback: () => void, ms = 0) => schedule(callback, ms),
  clearTimeout: (id: number) => timers.delete(id),
  setInterval: (callback: () => void, ms: number) => schedule(callback, ms, ms),
  clearInterval: (id: number) => timers.delete(id),
});
Date.now = () => now;

function tick(ms: number): void {
  now += ms;
  // A suspended interval runs once on wake, not once per missed interval.
  for (const [id, timer] of [...timers]) {
    if (timer.at > now || !timers.has(id)) continue;
    if (timer.interval) timer.at = now + timer.interval;
    else timers.delete(id);
    timer.callback();
  }
}
async function flush(): Promise<void> {
  for (let n = 0; n < 12; n++) await Promise.resolve();
}

class Events {
  readonly listeners = new Map<string, Set<() => void>>();
  addEventListener(type: string, callback: () => void): void {
    const callbacks = this.listeners.get(type) ?? new Set();
    callbacks.add(callback);
    this.listeners.set(type, callbacks);
  }
  removeEventListener(type: string, callback: () => void): void {
    this.listeners.get(type)?.delete(callback);
  }
  fire(type: string): void { this.listeners.get(type)?.forEach(callback => callback()); }
  count(): number { return [...this.listeners.values()].reduce((n, set) => n + set.size, 0); }
}
const browser = Object.assign(new Events(), {
  location: { protocol: "https:", host: "relay.test", reload: () => {} },
});
const document = Object.assign(new Events(), { visibilityState: "visible" });
class Socket {
  static readonly OPEN = 1;
  static readonly instances: Socket[] = [];
  readyState = 0;
  readonly sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) { Socket.instances.push(this); }
  send(raw: string): void { this.sent.push(raw); }
  close(): void { this.readyState = 3; } // deliberately no close callback
  open(): void { this.readyState = Socket.OPEN; this.onopen?.(); }
  pong(): void { this.onmessage?.({ data: JSON.stringify({ type: "pong", n: 1 }) }); }
}
Object.assign(globalThis, { window: browser, document, WebSocket: Socket });
const latest = (): Socket => Socket.instances.at(-1)!;
const makeClient = (authFail = () => {}): RelayWs => new RelayWs({
  onEvent: () => {}, onConnState: () => {}, onAuthFail: authFail,
});
const checkStopped = (): void => {
  assert.equal(timers.size, 0, "stop must clear all connection timers");
  assert.equal(browser.count() + document.count(), 0, "stop must detach wake listeners");
};

try {
  const client = makeClient();
  client.start();
  const stalled = latest();
  const count = Socket.instances.length;
  client.start();
  assert.equal(Socket.instances.length, count, "start is idempotent");
  tick(15000);
  assert.equal(stalled.readyState, 3, "opening handshake has a finite deadline");
  tick(1000);
  assert.equal(Socket.instances.length, count + 1,
    "reconnect must proceed even when close() never dispatches onclose");
  const current = latest();
  stalled.open();
  assert.equal(stalled.sent.length, 0, "late open must not send recovery on a retired socket");
  current.open();
  assert.ok(current.sent.some(raw => JSON.parse(raw).type === "hello"));
  client.stop();
  checkStopped();

  const sleeping = makeClient();
  sleeping.start();
  const healthy = latest();
  healthy.open();
  tick(120000);
  assert.equal(healthy.readyState, 1, "sleep must probe before declaring a connection dead");
  assert.equal(JSON.parse(healthy.sent.at(-1)!).type, "ping");
  healthy.pong();
  tick(15000);
  assert.equal(healthy.readyState, 1, "a wake pong must cancel the recovery deadline");
  // A grace timer may itself be suspended while the device sleeps again.
  browser.fire("pageshow");
  tick(120000);
  assert.equal(healthy.readyState, 1, "a suspended probe needs a fresh grace period");
  healthy.pong();
  sleeping.stop();
  checkStopped();

  const halfOpen = makeClient();
  halfOpen.start();
  const silent = latest();
  silent.open();
  tick(20000); tick(20000); tick(20000);
  assert.equal(silent.readyState, 3, "regular missing heartbeats retire a half-open socket");
  const beforeOnline = Socket.instances.length;
  browser.fire("online");
  assert.equal(Socket.instances.length, beforeOnline + 1, "online bypasses reconnect backoff");
  halfOpen.stop();
  checkStopped();

  const wakeDead = makeClient();
  wakeDead.start();
  const dead = latest();
  dead.open();
  document.fire("visibilitychange");
  tick(15000);
  assert.equal(dead.readyState, 3, "an unanswered wake probe must rebuild the socket");
  wakeDead.stop();
  checkStopped();

  let authRequests = 0;
  Object.assign(globalThis, { fetch: async () => { authRequests++; return { ok: true, status: 200 }; } });
  const established = makeClient();
  established.start();
  latest().open();
  latest().onclose?.({ code: 1006 });
  await flush();
  assert.equal(authRequests, 0, "a previously open socket must reconnect without an HTTP probe delay");
  established.stop();
  checkStopped();

  let resolveAuth!: (value: { ok: boolean; status: number }) => void;
  Object.assign(globalThis, { fetch: () => new Promise(resolve => { resolveAuth = resolve; }) });
  let authFailures = 0;
  const probing = makeClient(() => authFailures++);
  probing.start();
  latest().onclose?.({ code: 1006 });
  browser.fire("online");
  latest().open();
  latest().onclose?.({ code: 1006 });
  resolveAuth({ ok: false, status: 401 });
  await flush();
  assert.equal(authFailures, 0, "a stale auth result cannot log out a newer connection generation");
  probing.stop();
  checkStopped();

  Object.assign(globalThis, { fetch: async () => ({ ok: false, status: 401 }) });
  const unauthorized = makeClient(() => authFailures++);
  unauthorized.start();
  latest().onclose?.({ code: 1006 });
  await flush();
  assert.equal(authFailures, 1, "an actual handshake auth rejection still requests login");
  const unauthorizedCount = Socket.instances.length;
  browser.fire("online");
  assert.equal(Socket.instances.length, unauthorizedCount);
  unauthorized.stop();
  checkStopped();

  let protocolProbes = 0;
  Object.assign(globalThis, { fetch: async () => {
    protocolProbes++;
    return { ok: true, json: () => new Promise(() => {}) };
  } });
  const mismatch = makeClient();
  mismatch.start();
  latest().onclose?.({ code: 4406 });
  await flush();
  assert.equal(protocolProbes, 2);
  tick(5000);
  await flush();
  tick(1000);
  await flush();
  assert.equal(protocolProbes, 4, "a stalled protocol response body must not freeze rollout recovery");
  mismatch.stop();
  tick(5000); // in-flight bounded probes finish after stop, without scheduling another poll
  await flush();
  checkStopped();

  const cached: StartupReplayState = {
    cursors: { session: 17 }, generations: { session: "generation" }, controls: {},
  };
  assert.deepEqual(await loadStartupReplayState(async () => cached), cached);
  assert.deepEqual(await loadStartupReplayState(async () => { throw Error("IDB unavailable"); }),
    { cursors: {}, generations: {}, controls: {} });
  let finishCache!: (value: StartupReplayState) => void;
  const blockedCache = loadStartupReplayState(() => new Promise(resolve => { finishCache = resolve; }));
  await flush();
  tick(250);
  const fallback = await blockedCache;
  assert.deepEqual(fallback, { cursors: {}, generations: {}, controls: {} },
    "blocked cache must allow first socket startup after its short budget");
  finishCache(cached);
  await flush();
  assert.deepEqual(fallback, { cursors: {}, generations: {}, controls: {} },
    "late cache data cannot overwrite a live replay cursor");
  assert.equal(timers.size, 0);
} finally {
  Object.assign(globalThis, originals);
  Date.now = originals.now;
}
console.log("connection lifecycle tests passed");
