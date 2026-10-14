import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import http from "node:http";
import net from "node:net";
import { test, before, after } from "node:test";

let host, child, base, port;
const token = "test-only-public-key-not-a-real-credential";
const nativeCookie = "native-desktop=test-only-private";
let lastRequest;
before(async () => {
  host = http.createServer((req, res) => {
    lastRequest = { url: req.url, headers: req.headers };
    res.writeHead(200, { "content-type": "text/html", "set-cookie": "native-secret=must-not-leak" });
    res.end("<h1>Mock Desktop ready</h1>");
  });
  host.on("upgrade", (req, socket) => { socket.end("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"); });
  host.listen(0, "127.0.0.1"); await once(host, "listening");
  const reserve = net.createServer(); reserve.listen(0, "127.0.0.1"); await once(reserve, "listening");
  port = reserve.address().port; await new Promise((resolve) => reserve.close(resolve));
  base = `http://127.0.0.1:${port}`;
  child = spawn(process.execPath, [new URL("./desktop-dsh-proxy.mjs", import.meta.url).pathname.replace(/^\/([A-Z]:)/u, "$1")], {
    env: { ...process.env, DSH_PROXY_PORT: String(port), DSH_TARGET_PORT: String(host.address().port),
      DSH_UPSTREAM_COOKIE: nativeCookie, DSH_PUBLIC_TOKEN: token, DSH_PUBLIC_COOKIE_VALUE: token, DSH_PUBLIC_ORIGIN: base },
    stdio: ["ignore", "pipe", "pipe"],
  });
  await Promise.race([once(child.stdout, "data"), once(child, "exit").then(() => { throw new Error("proxy failed to start"); })]);
});
after(async () => { child?.kill(); host?.closeAllConnections(); await new Promise((resolve) => host?.close(resolve)); });
const session = { cookie: `cc-remote-dsh=${token}` };
async function login(access, origin = base) {
  return fetch(`${base}/.remote-login`, { method: "POST", redirect: "manual",
    headers: { origin, "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ access }) });
}

test("clean menu entry presents login and never embeds the credential", async () => {
  const r = await fetch(base); const html = await r.text();
  assert.equal(r.status, 200); assert.match(html, /登录链接或访问密钥/u);
  assert.ok(!html.includes(token)); assert.ok(!html.includes(nativeCookie));
  assert.match(r.headers.get("content-security-policy"), /frame-ancestors 'self'/u);
  // no-referrer makes native form submissions opaque (Origin: null), which
  // would fail our exact-origin check even on the legitimate login page.
  assert.equal(r.headers.get("referrer-policy"), "same-origin");
});
test("protected API refuses missing or incorrect session", async () => {
  for (const cookie of ["", "cc-remote-dsh=obsolete"]) assert.equal((await fetch(`${base}/api/private`, { headers: { cookie } })).status, 401);
});
test("wrong key returns a recoverable form, no cookie", async () => {
  const r = await login("wrong"); assert.equal(r.status, 401); assert.equal(r.headers.get("set-cookie"), null);
  assert.match(await r.text(), /密钥不正确/u);
});
test("key POST sets a Lax HttpOnly session and removes key from redirect", async () => {
  const r = await login(token); assert.equal(r.status, 303); assert.equal(r.headers.get("location"), "./");
  assert.match(r.headers.get("set-cookie"), /HttpOnly; SameSite=Lax/u);
  assert.equal(r.headers.get("referrer-policy"), "same-origin");
});
test("pasting full desktop link works; duplicate tokens fail", async () => {
  // The pasted link's host is irrelevant: only its single token is extracted.
  assert.equal((await login(`http://106.55.19.100/dsh/?token=${token}`)).status, 303);
  assert.equal((await login(`${base}/?token=${token}&token=${token}`)).status, 401);
});
test("legacy token link remains compatible with browser cross-site navigation", async () => {
  const r = await fetch(`${base}/?token=${token}`, { redirect: "manual" });
  assert.equal(r.status, 303); assert.match(r.headers.get("set-cookie"), /SameSite=Lax/u);
});
test("authorized requests inject private desktop cookie without leaking it", async () => {
  const r = await fetch(base, { headers: session }); assert.equal(r.status, 200);
  assert.equal(lastRequest.headers.cookie, nativeCookie); assert.equal(lastRequest.url, "/");
  assert.equal(r.headers.get("set-cookie"), null);
});
test("stale duplicate cookie cannot shadow valid current session", async () => {
  const r = await fetch(base, { headers: { cookie: `cc-remote-dsh=obsolete; ${session.cookie}` } }); assert.equal(r.status, 200);
});
test("foreign origins cannot log in, mutate or read via CORS", async () => {
  assert.equal((await login(token, "http://evil.invalid")).status, 403);
  assert.equal((await fetch(`${base}/api/private`, { method: "POST", headers: { ...session, origin: "http://evil.invalid" } })).status, 403);
  assert.equal((await fetch(base, { headers: { ...session, origin: "http://evil.invalid" } })).status, 403);
  assert.equal((await fetch(`${base}/assets/entry.js`, { method: "POST" })).status, 401);
});
test("opaque login origins remain blocked and receive a recoverable form", async () => {
  const r = await login(token, "null");
  assert.equal(r.status, 403);
  assert.equal(r.headers.get("set-cookie"), null);
  assert.equal(r.headers.get("referrer-policy"), "same-origin");
  assert.match(await r.text(), /重新打开 \/dsh\//u);
  assert.equal((await fetch(`${base}/api/private`, { method: "POST", headers: { ...session, origin: "null" } })).status, 403);
});
async function upgrade(path, headers = {}) {
  const socket = net.connect(port, "127.0.0.1"); await once(socket, "connect");
  socket.setTimeout(3000, () => socket.destroy(new Error("upgrade timeout")));
  socket.write(`GET ${path} HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n${Object.entries(headers).map(([k, v]) => `${k}: ${v}\r\n`).join("")}\r\n`);
  const [data] = await once(socket, "data"); socket.destroy(); return String(data).split("\r\n")[0];
}
test("WebSocket requires session and trusted Origin, including static paths", async () => {
  assert.match(await upgrade("/ws"), /401/u);
  assert.match(await upgrade("/assets/fake", { origin: base }), /401/u);
  assert.match(await upgrade("/ws", { ...session, origin: "http://evil.invalid" }), /403/u);
  assert.match(await upgrade("/ws", { ...session, origin: base }), /101/u);
});
