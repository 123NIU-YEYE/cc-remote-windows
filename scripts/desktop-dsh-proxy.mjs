import http from "node:http";
import net from "node:net";
import { timingSafeEqual } from "node:crypto";

const listenPort = Number(process.env.DSH_PROXY_PORT ?? "3082");
const targetPort = Number(process.env.DSH_TARGET_PORT ?? "0");
const upstreamCookie = process.env.DSH_UPSTREAM_COOKIE ?? "";
const publicToken = process.env.DSH_PUBLIC_TOKEN ?? "";
const publicCookieName = process.env.DSH_PUBLIC_COOKIE_NAME ?? "cc-remote-dsh";
const publicCookieValue = process.env.DSH_PUBLIC_COOKIE_VALUE ?? "";
const publicOrigin = new URL(process.env.DSH_PUBLIC_ORIGIN ?? "http://106.55.19.100").origin;
// Reuse the cc-remote browser login so the panel opens straight from the page
// that embeds it, instead of asking the user to paste the DSH link again. The
// check runs here rather than in Caddy: this proxy already terminates the
// browser session, and Caddy's forward_auth does not forward the Cookie header
// reliably (a session that /api/session accepts directly was rejected through
// it), nor does it treat WebSocket upgrades consistently.
const ccRemoteSessionUrl = new URL(
  process.env.DSH_CC_REMOTE_SESSION_URL ?? `${publicOrigin}/api/session`);
const ccRemoteCookieName = process.env.DSH_CC_REMOTE_COOKIE ?? "cc_remote_session";
const ccRemoteHost = process.env.DSH_CC_REMOTE_HOST ?? ccRemoteSessionUrl.host;

if (!Number.isInteger(listenPort) || listenPort < 1 || listenPort > 65535) throw new Error("invalid proxy port");
if (!Number.isInteger(targetPort) || targetPort < 1 || targetPort > 65535) throw new Error("invalid desktop Host port");
if (upstreamCookie === "" || publicToken === "" || publicCookieValue === "") throw new Error("proxy credentials are missing");

const staticPrefixes = ["/plugins/", "/assets/", "/favicon", "/manifest", "/robots.txt"];
const isStatic = (pathname) => staticPrefixes.some((prefix) => pathname.startsWith(prefix));

function sameSecret(actual, expected) {
  const a = Buffer.from(actual);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

function hasSession(headers) {
  const raw = headers.cookie;
  if (typeof raw !== "string") return false;
  for (const part of raw.split(";")) {
    const at = part.indexOf("=");
    if (at >= 0 && part.slice(0, at).trim() === publicCookieName
      && sameSecret(part.slice(at + 1).trim(), publicCookieValue)) return true;
  }
  return false;
}

function cookieValue(headers, name) {
  const raw = headers.cookie;
  if (typeof raw !== "string") return "";
  for (const part of raw.split(";")) {
    const at = part.indexOf("=");
    if (at >= 0 && part.slice(0, at).trim() === name) return part.slice(at + 1).trim();
  }
  return "";
}

// Ask the relay whether this browser holds a live cc-remote session. Only the
// boolean verdict crosses back; the session cookie itself is never logged.
function ccRemoteSessionValid(headers) {
  const session = cookieValue(headers, ccRemoteCookieName);
  if (session === "") return Promise.resolve(false);
  return new Promise((resolve) => {
    const probe = http.request({
      protocol: ccRemoteSessionUrl.protocol,
      hostname: ccRemoteSessionUrl.hostname,
      port: ccRemoteSessionUrl.port || 80,
      method: "GET",
      path: `${ccRemoteSessionUrl.pathname}${ccRemoteSessionUrl.search}`,
      // The relay validates Host/Origin against its configured PUBLIC_ORIGIN.
      headers: { host: ccRemoteHost, cookie: `${ccRemoteCookieName}=${session}` },
    }, (upstream) => {
      upstream.resume();
      resolve(upstream.statusCode === 200);
    });
    // Fail closed: an unreachable relay must not unlock the panel.
    probe.setTimeout(8000, () => probe.destroy(new Error("timeout")));
    probe.once("error", () => resolve(false));
    probe.end();
  });
}

function trustedOrigin(request) {
  return request.headers.origin === publicOrigin
    || request.headers.origin === `http://127.0.0.1:${listenPort}`;
}

function loginPage(response, status = 200, message = "") {
  response.writeHead(status, {
    "cache-control": "no-store", "content-type": "text/html; charset=utf-8",
    // Native form POSTs with no-referrer can send Origin: null. Keep the
    // same-origin source visible to our CSRF check without leaking it off-site.
    "referrer-policy": "same-origin", "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'self'",
  });
  response.end(`<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>DeepSeek 桌面版远程登录</title>
<style>body{font:16px system-ui;background:#f5f6fa;color:#202530;margin:0;padding:24px}main{max-width:440px;margin:8vh auto;background:white;padding:24px;border-radius:16px}h1{font-size:23px}p{line-height:1.7}input,button{box-sizing:border-box;width:100%;padding:14px;margin-top:12px;border:1px solid #ccd1dc;border-radius:8px;font:inherit}button{background:#3664e9;color:white;border:0}small{display:block;line-height:1.7;color:#596174}.error{color:#b42318}</style>
<main><h1>DeepSeek 桌面版远程登录</h1><p>这是桌面版的独立登录，不使用 cc-remote 密码或 DeepSeek API Key。</p>
<form method="post" action=".remote-login"><label for="access">登录链接或访问密钥</label>
<input id="access" name="access" type="password" required maxlength="4096" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="粘贴桌面上的完整登录链接">
<button type="submit">登录桌面版</button></form><p class="error" role="alert">${message}</p>
<small>电脑桌面：DSH桌面版手机登录链接.txt。复制其中的完整链接到这里即可。登录后请继续使用同一个手机浏览器；微信内置浏览器和系统浏览器的登录状态不互通。</small>
<p><small>当前公网 HTTP 不加密，请勿分享登录链接。电脑和 DeepSeek 桌面版需要保持运行。</small></p></main></html>`);
}

function authenticate(response) {
  // Verify Desktop readiness without forwarding the public key to its Host.
  const check = http.get({ hostname: "127.0.0.1", port: targetPort,
    path: "/", headers: upstreamHeaders({}) }, (upstream) => {
    upstream.resume();
    if (upstream.statusCode !== 200) {
      loginPage(response, 502, "桌面版暂时不可用，请确认电脑上的 DeepSeek 桌面版正在运行。");
      return;
    }
    response.writeHead(303, {
      location: "./", "cache-control": "no-store", "referrer-policy": "same-origin",
      "set-cookie": `${publicCookieName}=${publicCookieValue}; Max-Age=2592000; Path=/; HttpOnly; SameSite=Lax${publicOrigin.startsWith("https:") ? "; Secure" : ""}`,
    });
    response.end();
  });
  check.setTimeout(10000, () => check.destroy(new Error("timeout")));
  check.once("error", () => {
    if (!response.headersSent) loginPage(response, 502, "桌面版连接失败，请稍后重试。");
  });
}

async function passwordLogin(request, response) {
  if (!trustedOrigin(request)) {
    loginPage(response, 403, "登录页面来源已失效，请重新打开 /dsh/ 页面后再登录。");
    return;
  }
  if (!request.headers["content-type"]?.startsWith("application/x-www-form-urlencoded")) {
    loginPage(response, 415, "请使用此页面的登录按钮。"); return;
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 8192) { loginPage(response, 413, "登录内容过长。"); request.resume(); return; }
    chunks.push(chunk);
  }
  const values = new URLSearchParams(Buffer.concat(chunks).toString("utf8")).getAll("access");
  let token = values.length === 1 ? values[0].trim() : "";
  if (/^https?:\/\//u.test(token)) {
    try {
      const tokens = new URL(token).searchParams.getAll("token");
      token = tokens.length === 1 ? tokens[0] : "";
    } catch { token = ""; }
  }
  if (!sameSecret(token, publicToken)) { loginPage(response, 401, "密钥不正确，请复制桌面上的最新登录链接。"); return; }
  authenticate(response);
}

function upstreamHeaders(headers) {
  const result = { ...headers };
  for (const name of ["host", "origin", "cookie", "sec-fetch-site", "x-forwarded-host", "x-forwarded-proto"]) delete result[name];
  result.host = `127.0.0.1:${String(targetPort)}`;
  result.cookie = upstreamCookie;
  return result;
}

function proxyHttp(request, response, url, onResponse = (upstream) => {
  const headers = { ...upstream.headers };
  // The locally signed native Desktop credential must never reach the phone.
  delete headers["set-cookie"];
  response.writeHead(upstream.statusCode ?? 502, headers);
  upstream.pipe(response);
}) {
  const upstream = http.request({
    hostname: "127.0.0.1",
    port: targetPort,
    method: request.method,
    path: `${url.pathname}${url.search}`,
    headers: upstreamHeaders(request.headers),
  }, onResponse);
  upstream.once("error", (error) => {
    if (!response.headersSent) response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    response.end(`desktop Host unavailable: ${error.message}`);
  });
  request.pipe(upstream);
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://dsh-remote.invalid");
  if (request.method === "POST" && url.pathname === "/.remote-login") {
    passwordLogin(request, response).catch(() => {
      if (!response.headersSent) loginPage(response, 400, "登录请求失败，请重试。");
    });
    return;
  }
  const tokenLogin = request.method === "GET" && url.pathname === "/"
    && url.searchParams.getAll("token").length === 1 && sameSecret(url.searchParams.get("token"), publicToken);

  if (tokenLogin) {
    authenticate(response);
    return;
  }

  const safeMethod = request.method === "GET" || request.method === "HEAD";
  const isEntryDocument = safeMethod && ["/", "/index.html"].includes(url.pathname);

  // Single sign-on: the panel is embedded in the cc-remote page, so a browser
  // that already holds a live cc-remote session should never be asked to paste
  // the DSH link again. Mint the DSH session cookie and bounce back to the
  // panel; the redirect target then authenticates with that cookie.
  if (isEntryDocument && !hasSession(request.headers)) {
    void ccRemoteSessionValid(request.headers).then((valid) => {
      if (valid) { authenticate(response); return; }
      if (!response.headersSent) loginPage(response);
    }).catch(() => {
      if (!response.headersSent) loginPage(response);
    });
    return;
  }

  if (!hasSession(request.headers) && !(safeMethod && isStatic(url.pathname))) {
    if (isEntryDocument) {
      loginPage(response); return;
    }
    response.writeHead(401, { "cache-control": "no-store", "content-type": "text/plain; charset=utf-8" });
    response.end("desktop DSH authentication required; open /dsh/ to sign in.");
    return;
  }
  if ((!safeMethod || request.headers.origin !== undefined) && !trustedOrigin(request)) {
    response.writeHead(403); response.end("Forbidden origin");
    return;
  }

  proxyHttp(request, response, url);
});

server.on("upgrade", (request, socket, head) => {
  const url = new URL(request.url ?? "/", "http://dsh-remote.invalid");
  if (!hasSession(request.headers)) {
    socket.end("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
    return;
  }
  if (!trustedOrigin(request)) {
    socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n"); return;
  }
  const upstream = net.connect({ host: "127.0.0.1", port: targetPort }, () => {
    const lines = [`${request.method ?? "GET"} ${url.pathname}${url.search} HTTP/1.1`];
    const headers = upstreamHeaders(request.headers);
    for (const [name, value] of Object.entries(headers)) {
      if (Array.isArray(value)) for (const item of value) lines.push(`${name}: ${item}`);
      else if (value !== undefined) lines.push(`${name}: ${value}`);
    }
    upstream.write(`${lines.join("\r\n")}\r\n\r\n`);
    if (head.length > 0) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  });
  upstream.once("error", () => socket.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n"));
  socket.once("error", () => upstream.destroy());
});

server.headersTimeout = 15000;
server.requestTimeout = 0;
server.listen(listenPort, "127.0.0.1", () => process.stdout.write(`desktop-dsh-proxy listening on 127.0.0.1:${String(listenPort)} -> 127.0.0.1:${String(targetPort)}\n`));
