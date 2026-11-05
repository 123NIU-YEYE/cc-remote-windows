/**
 * Publish this Host's loopback control origin and launch token for cc-remote.
 *
 * The DSH API is loopback-only and authenticated by a per-process, memory-only
 * 43-character token. A separately launched Wrapper cannot read it, so this tiny
 * extension writes it to a private file the Wrapper can pair against.
 *
 * Safety rules:
 *  - Never throw into Host startup: every step is wrapped, and failures are a
 *    quiet no-op (the Wrapper then simply reports "not paired").
 *  - Never log the token.
 *  - Write atomically, and record the owning pid + port so a reader can reject a
 *    stale file left behind by an earlier process.
 *  - Read-only with respect to DSH: no session, Agent, or file of DSH's own.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const name = 'cc-remote-desktop-url';
// `webServer` supplies the bound port; `connection` mints the authenticated URL.
export const inject = ['connection', 'webServer'];

const SCHEMA = 1;

function targetPath() {
  const override = process.env.CC_REMOTE_DSH_DESKTOP_FILE;
  if (override && override.trim() !== '') return path.resolve(override.trim());
  const home = process.env.DSH_HOME || path.join(os.homedir(), '.dsh');
  return path.join(home, 'cc-remote-desktop.json');
}

export function apply(ctx) {
  const write = () => {
    try {
      const port = ctx.webServer?.port;
      const authenticate = ctx.connection?.authenticatedUrl;
      if (!port || typeof authenticate !== 'function') return;
      const url = new URL(authenticate.call(ctx.connection, `http://127.0.0.1:${port}`));
      const token = url.searchParams.get('token');
      if (!token) return;

      const target = targetPath();
      const payload = JSON.stringify({
        schema: SCHEMA,
        pid: process.pid,
        port,
        origin: `http://127.0.0.1:${port}`,
        token,
      });
      fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
      const staging = `${target}.${process.pid}.tmp`;
      fs.writeFileSync(staging, payload + '\n', { mode: 0o600 });
      fs.renameSync(staging, target);
    } catch {
      /* never break Host startup */
    }
  };

  try {
    // The authenticated URL only exists once the tree is running; dsh-web-app
    // uses the same signal before announcing its own URL.
    const settled = ctx.get?.('loader')?.await?.();
    if (settled && typeof settled.then === 'function') {
      settled.then(write, () => {});
    } else {
      write();
    }
  } catch {
    /* ignore */
  }
}
