import { appendFile, mkdir, readFile, writeFile, open } from "node:fs/promises";
import { closeSync, openSync, writeFileSync } from "node:fs";
import { createHash, createHmac, randomBytes } from "node:crypto";
import { join } from "node:path";
import { spawn, execFile } from "node:child_process";

const root = process.env.CC_REMOTE_ROOT ?? "D:\\cc-remote";
const runtime = join(root, ".runtime");
const node = process.execPath;
const proxyScript = join(root, "scripts", "desktop-dsh-proxy.mjs");
const ssh = join(process.env.WINDIR ?? "C:\\Windows", "System32", "OpenSSH", "ssh.exe");
const credentials = join(process.env.USERPROFILE ?? "", ".dsh", ".credentials.yaml");
const logFile = join(runtime, "dsh-desktop-remote-supervisor.log");
const lockFile = join(runtime, "dsh-desktop-node-supervisor.lock");
const publicTokenFile = join(runtime, "dsh-desktop-public-token.txt");
const publicUrlFile = join(runtime, "dsh-desktop-public-url.txt");
const proxyPort = Number(process.env.CC_REMOTE_DSH_PROXY_PORT ?? "3082");
const remoteTunnelPort = Number(process.env.CC_REMOTE_DSH_TUNNEL_PORT ?? String(proxyPort));
// Relay host and the public origin browsers use. Both must agree: the tunnel is
// opened to `relayTarget`, and `publicUrlBase` is the URL a phone can open. The
// origin is also what the proxy allowlists for its CSRF check, so a mismatch
// here is what makes the embedded panel fail to log in.
const relayTarget = process.env.CC_REMOTE_DSH_RELAY_TARGET ?? "root@106.55.19.100";
const publicUrlBase = process.env.CC_REMOTE_DSH_PUBLIC_URL ?? "http://106.55.19.100/dsh/";

let proxyProcess;
let tunnelProcess;
let targetPort = 0;
let targetPid = 0;
let stopped = false;

function b64url(bytes) {
  return Buffer.from(bytes).toString("base64").replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

async function log(message) {
  await appendFile(logFile, `${new Date().toISOString()} ${message}\n`, "utf8").catch(() => {});
}

// A lossy link makes the tunnel reconnect often. Logging every cycle filled the
// log with thousands of identical lines and buried the first useful diagnosis,
// so repeat offenders are summarised instead of repeated verbatim.
const repeatCounts = new Map();
async function logThrottled(key, message) {
  const seen = (repeatCounts.get(key) ?? 0) + 1;
  repeatCounts.set(key, seen);
  if (seen <= 3 || seen % 20 === 0) {
    await log(seen <= 3 ? message : `${message} (${String(seen)} occurrences so far)`);
  }
}

async function readOrCreateToken() {
  try {
    const token = (await readFile(publicTokenFile, "ascii")).trim();
    if (token !== "") { await restrictCredentialFile(publicTokenFile); return token; }
  } catch {}
  const token = b64url(randomBytes(32));
  await writeFile(publicTokenFile, token, { encoding: "ascii", mode: 0o600 });
  await restrictCredentialFile(publicTokenFile);
  return token;
}

function powershellPath() {
  return join(process.env.WINDIR ?? "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
}

function isRunningSupervisor(pid) {
  return new Promise((resolve) => {
    if (!Number.isSafeInteger(pid) || pid < 1) { resolve(false); return; }
    const script = `$p=Get-CimInstance Win32_Process -Filter \"ProcessId=${String(pid)}\" -ErrorAction SilentlyContinue; if($p -and $p.CommandLine -match 'desktop-dsh-supervisor\\.mjs'){exit 0}else{exit 1}`;
    execFile(powershellPath(), ["-NoProfile", "-NonInteractive", "-Command", script], { windowsHide: true, timeout: 5000 }, (error) => resolve(!error));
  });
}

function restrictCredentialFile(path) {
  return new Promise((resolve) => {
    const user = `${process.env.USERDOMAIN ?? ""}\\${process.env.USERNAME ?? ""}`;
    execFile(join(process.env.WINDIR ?? "C:\\Windows", "System32", "icacls.exe"),
      [path, "/inheritance:r", "/grant:r", `${user}:(F)`, "SYSTEM:(F)", "Administrators:(F)"],
      { windowsHide: true, timeout: 5000 }, () => resolve());
  });
}

function desktopHost() {
  return new Promise((resolve) => {
    // Resolve the Desktop Host by identity first, then confirm which loopback
    // port it actually owns.
    //
    // Three Windows-specific traps are load-bearing here:
    //   * Matching by command line alone is not enough. Any process whose
    //     command line merely CONTAINS the dsh-desktop-host path (a shell that
    //     mentions it, or a DSH subprocess echoing this very script) would win
    //     such a search, hold no listener, and make the supervisor log
    //     "Desktop Host stopped" forever. So the listener check is what makes
    //     the match authoritative.
    //   * Querying Get-CimInstance once per listener is far too slow: ~27
    //     loopback listeners means ~27 sequential WMI round trips, measured at
    //     9-15 s, which blows the execFile timeout and silently yields empty
    //     stdout (detection then never succeeds). One process query plus one
    //     listener query stays around 4-6 s.
    //   * `break` is invalid inside ForEach-Object (it is not a loop) and makes
    //     PowerShell exit non-zero, which execFile reports as an error even
    //     when valid JSON was written. Use foreach and parse stdout regardless.
    const script = "$procs=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'dsh-desktop-host.{0,4}lib.{0,4}index\\.js' }; foreach($x in $procs){ $l=Get-NetTCPConnection -State Listen -OwningProcess $x.ProcessId -ErrorAction SilentlyContinue | Where-Object {$_.LocalAddress -eq '127.0.0.1'} | Select-Object -First 1; if($l){ [pscustomobject]@{pid=$x.ProcessId;port=$l.LocalPort}|ConvertTo-Json -Compress; break } }";
    execFile(powershellPath(), ["-NoProfile", "-NonInteractive", "-Command", script], { windowsHide: true, timeout: 30000 }, (_error, stdout) => {
      try { const result = JSON.parse(String(stdout).trim()); resolve({ pid: Number(result.pid), port: Number(result.port) }); }
      catch { resolve(void 0); }
    });
  });
}

function upstreamCookie(port, credentialsText) {
  const match = /client-connection\/browser-session:\s*[\s\S]*?^\s+secret:\s*([A-Za-z0-9_-]+)\s*$/mu.exec(credentialsText);
  if (!match) throw new Error("Desktop DSH browser-session credential was not found");
  const secret = Buffer.from(match[1].replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - match[1].length % 4) % 4), "base64");
  const authority = `127.0.0.1:${String(port)}`;
  const issuedAt = Date.now();
  const payload = JSON.stringify({ version: 1, authority, issuedAt, expiresAt: issuedAt + 2592000000 });
  const body = b64url(Buffer.from(payload, "utf8"));
  const signature = b64url(createHmac("sha256", secret).update(body).digest());
  const name = `dsh-auth-${b64url(createHash("sha256").update(authority).digest())}`;
  return `${name}=v1.${body}.${signature}`;
}

// A child's stderr used to go to NUL (stdio:"ignore"). That is why a restart
// loop of thousands of cycles left no way to tell WHICH child died or WHY.
// Give each child a fresh per-role log so the most recent failure is readable.
function openChildLog(tag) {
  try {
    const path = join(runtime, `dsh-desktop-${tag}.log`);
    // Truncate per spawn rather than rotate: only the latest attempt matters,
    // and an append-only file would grow without bound across thousands of
    // restarts.
    writeFileSync(path, "");
    return { fd: openSync(path, "a"), path };
  } catch { return { fd: "ignore", path: "" }; }
}

function spawnDetached(file, args, env, tag) {
  const { fd, path } = tag ? openChildLog(tag) : { fd: "ignore", path: "" };
  const child = spawn(file, args, {
    env: { ...process.env, ...env }, cwd: root, detached: true, windowsHide: true,
    stdio: ["ignore", "ignore", fd],
  });
  // The child holds its own duplicate of the descriptor.
  if (typeof fd === "number") { try { closeSync(fd); } catch {} }
  child.childLogPath = path;
  child.unref();
  return child;
}

function stopChild(child) {
  if (!child || child.exitCode !== null || child.killed) return;
  try { child.kill(); } catch {}
}

function exitReason(child) {
  if (!child || child.exitCode === null) return "still running";
  const signal = child.signalCode ? `, signal ${child.signalCode}` : "";
  const where = child.childLogPath ? `; see ${child.childLogPath}` : "";
  return `exit code ${String(child.exitCode)}${signal}${where}`;
}

async function startProxy(host, token, credentialsText) {
  stopChild(proxyProcess);
  const cookie = upstreamCookie(host.port, credentialsText);
  proxyProcess = spawnDetached(node, [proxyScript], {
    DSH_PROXY_PORT: String(proxyPort), DSH_TARGET_PORT: String(host.port), DSH_UPSTREAM_COOKIE: cookie,
    DSH_PUBLIC_TOKEN: token, DSH_PUBLIC_COOKIE_NAME: "cc-remote-dsh", DSH_PUBLIC_COOKIE_VALUE: token,
    DSH_PUBLIC_ORIGIN: new URL(publicUrlBase).origin,
  }, "proxy");
}

async function startTunnel() {
  stopChild(tunnelProcess);
  // Tuned for a lossy uplink. The observed failure was
  //   ssh: connect to host ... port 22: Connection timed out
  // on roughly a third of attempts, because the SSH handshake is a multi-round
  // exchange and a 10 s ConnectTimeout gave up after a single dropped packet.
  //   * ConnectTimeout=30 rides out a lost SYN or two.
  //   * TCPKeepAlive=yes makes the OS notice a dead path instead of hanging.
  //   * ServerAliveInterval=15/CountMax=4 both detects a silent drop sooner and
  //     tolerates the keepalives themselves being lost.
  // The supervisor already retries, so the goal is fewer failed attempts, not
  // heroics on one attempt.
  const args = [
    "-N", "-T",
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ConnectTimeout=30",
    "-o", "TCPKeepAlive=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=4",
    "-R", `127.0.0.1:${String(remoteTunnelPort)}:127.0.0.1:${String(proxyPort)}`,
    relayTarget,
  ];
  tunnelProcess = spawnDetached(ssh, args, {}, "tunnel");
}

async function startServices(host, token, credentialsText) {
  await startProxy(host, token, credentialsText);
  await startTunnel();
  targetPort = host.port;
  targetPid = host.pid;
  await writeFile(publicUrlFile, `${publicUrlBase}?token=${token}`, { encoding: "utf8", mode: 0o600 });
  await restrictCredentialFile(publicUrlFile);
  await log(`Desktop Host detected (PID ${String(host.pid)}, port ${String(host.port)}); proxy and tunnel started.`);
}

async function main() {
  await mkdir(runtime, { recursive: true });
  let lock;
  try { lock = await open(lockFile, "wx"); }
  catch {
    const previousPid = Number((await readFile(lockFile, "ascii").catch(() => "")).trim());
    if (await isRunningSupervisor(previousPid)) { await log("Another Node desktop supervisor is already running; exiting."); return; }
    await import("node:fs/promises").then(({ unlink }) => unlink(lockFile)).catch(() => {});
    try { lock = await open(lockFile, "wx"); }
    catch { await log("Could not acquire the desktop supervisor lock; exiting."); return; }
  }
  await lock.writeFile(String(process.pid), "ascii");
  await lock.close().catch(() => {});
  const token = await readOrCreateToken();
  const credentialsText = await readFile(credentials, "utf8");
  process.on("SIGINT", () => { stopped = true; stopChild(proxyProcess); stopChild(tunnelProcess); });
  process.on("SIGTERM", () => { stopped = true; stopChild(proxyProcess); stopChild(tunnelProcess); });
  while (!stopped) {
    try {
      const host = await desktopHost();
      const hostChanged = Boolean(host?.port) && (host.port !== targetPort || host.pid !== targetPid);
      if (hostChanged) await startServices(host, token, credentialsText);
      if (!host?.port && targetPort) {
        stopChild(proxyProcess); stopChild(tunnelProcess);
        proxyProcess = void 0; tunnelProcess = void 0; targetPort = 0; targetPid = 0;
        await log("Desktop Host stopped; remote services paused.");
      }
      // Restart only what actually died. Tearing both children down because one
      // exited turned a single flaky tunnel into a full outage of the panel, and
      // is how thousands of reconnect cycles accumulated.
      if (host?.port && !hostChanged) {
        if (proxyProcess && proxyProcess.exitCode !== null) {
          await logThrottled("proxy-exited", `The DSH proxy exited (${exitReason(proxyProcess)}); restarting it alone.`);
          await startProxy(host, token, credentialsText);
        }
        if (tunnelProcess && tunnelProcess.exitCode !== null) {
          await logThrottled("tunnel-exited", `The SSH tunnel exited (${exitReason(tunnelProcess)}); restarting it alone.`);
          // A brief pause keeps a lossy link from being hammered the instant a
          // handshake fails, which was making the failure rate worse.
          await new Promise((resolve) => setTimeout(resolve, 2000));
          await startTunnel();
        }
      }
    } catch (error) { await log(`Supervisor error: ${error instanceof Error ? error.message : String(error)}`); }
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  await appendFile(logFile, `${new Date().toISOString()} Supervisor stopped.\n`, "utf8").catch(() => {});
  await import("node:fs/promises").then(({ unlink }) => unlink(lockFile)).catch(() => {});
}

main().catch(async (error) => { await log(`Fatal supervisor error: ${error instanceof Error ? error.message : String(error)}`); process.exitCode = 1; });
