# cc-remote-windows

**Continue local Claude Code, Codex and DeepSeek Harness sessions from your phone or browser.**

[中文](README.md) · Product version: v4.0.29 · protocol v81

Native session identity · Multiple devices and sessions · Live tools · Collaboration rooms · Web / PWA / TUI

This branch combines Windows support, DSH desktop integration and collaboration rooms. Model authentication, tool execution and native history stay on the device. cc-remote provides remote control, history projections and the interface.

[Windows quick start](#windows-quick-start) · [Shared sessions](#how-web-and-local-clients-share-a-session) · [Engine setup](#engine-setup) · [Deployment](#deployment-and-upgrades) · [Troubleshooting](#troubleshooting) · [Development](#development-and-validation)

![Interface example: multiple sessions and tool activity](assets/readme-claude-multisession.jpg)

## What you can do

- Continue tasks remotely, inspect replies and tools, answer native questions and approvals, then return to the official client.
- Organize, search, pin and switch sessions across devices without stopping background work.
- Read the device's transcript, rollout or DSH log, with paged history and on-demand process details.
- Use native models, reasoning, plans, Goals, steering, queues, subagents and extensions where supported.
- Dispatch with `@member` in collaboration rooms, quote results, hand off messages and open members' full sessions.
- Use the Claude/Codex Code and Work terminal workspace. Press `Space e` for the session tree; see the [TUI guide](docs/tui.md).

Controls depend on the device's native engine, account and version. DSH currently supports Code; Claude/Codex Work uses separate projects and control planes.

## How Web and local clients share a session

Sharing preserves one engine's conversation across Web and official local clients. Each engine retains its own log format and session IDs.

| Engine | Shared identity and history | Control |
|---|---|---|
| Claude Code | Same UUID, original cwd and transcript | A native terminal owns its transcript; Web mirrors read-only until it exits or the user explicitly hands over control |
| Codex Code | Same thread ID, account home, official daemon and rollout | Wrapper and ordinary CLI use the same daemon; App sharing uses a separate launcher and actual connection verification |
| DSH | Web `dsh@<native-ID>` maps to the desktop Host's session | Official local control API; DSH owns execution and storage |

Open **会话信息与本地续接** (session information and local continuation) to inspect the full ID, original cwd, device, account and control state. On mobile, use More settings. Applicable continuation commands are shown there.

Resume Claude with `claude --resume <UUID>` in its original cwd, or Codex with `codex resume <thread-ID>`. Select the matching native session in the paired DSH desktop. Uncertain ownership remains read-only; do not terminate unknown processes to unlock it.

## Architecture and data

```text
Phone / browser / PWA
        | HTTPS + Cookie / WebSocket
        v
Relay + Web (VPS with Caddy, or a tunnel)
        | /ws; Wrapper authenticates with a Bearer credential
        v
Local Wrapper
        +-- Claude Code: daily official CLI + pinned Agent SDK
        +-- Codex: official app-server daemon / proxy
        +-- DSH: paired loopback control API

Local engine -- its own authentication and settings --> model service
```

Relay forwards device-scoped traffic and does not proxy model APIs. Native logs stay local; browser projections and the SQLite history index are rebuildable. Preserve login, device authorization and Push state when moving the corresponding service.

## Windows quick start

This path connects to an **existing Relay**. Otherwise, start with [Deployment and upgrades](#deployment-and-upgrades).

### 1. Prepare dependencies

| Dependency | Requirement |
|---|---|
| Python | Standard or uv-managed Python 3.13 recommended; deployment runtime pinned in [python-version.txt](deploy/python-version.txt) |
| Git | Fetch the source |
| Node.js | Node 24 when building Web; see [.nvmrc](.nvmrc). Wrapper-only devices do not need a Web build |
| Claude Code | Signed-in daily official CLI, version `>=2.1.263` |
| Codex | Signed-in official CLI, aligned with the daemon; verified baseline `0.166.0` |
| DSH, optional | Desktop `0.2.1-rc.2` / `0.2.1-rc.5`, paired below |

```powershell
git clone https://github.com/123NIU-YEYE/cc-remote-windows.git
Set-Location cc-remote-windows
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-wrapper.lock
Copy-Item .env.example .env
```

Preserve an existing `.env`. Use the repository's `.venv`; the old portable-Python `.pydeps` layout is not the current installation path.

### 2. Configure and enroll the device

Edit `.env` as UTF-8 without BOM. For **Device Center pairing**, use this example and replace CLI paths with real absolute paths:

```ini
# Empty values use private paired-device configuration
RELAY_URL=
WRAPPER_TOKEN=
CC_REMOTE_MACHINE_ID=

CLAUDE_BIN=C:/path/to/claude.exe
CODEX_BIN=C:/path/to/codex.exe
CC_REMOTE_CODEX_DAEMON=auto

# Optional existing default project directory
# CC_CWD=C:/projects/workspace
```

Enable device enrollment in the Relay's Device Center, obtain a one-time code, then run:

```powershell
.\.venv\Scripts\python.exe -m cc_remote.device pair https://relay.example.com XXXXX-XXXXX-XXXXX-XXXXX --name "Windows PC"
```

Pairing saves credentials in a private user-home file. Nonempty environment values take precedence: remove stale `RELAY_URL`, `WRAPPER_TOKEN` and `CC_REMOTE_MACHINE_ID` overrides from the launch environment too.

Existing manual installations can keep those values explicit. `RELAY_URL` must end in `/ws`; the machine ID and credential must match Relay authorization. Keep codes, tokens and real deployment configuration out of Git.

### 3. Start and verify

```powershell
.\scripts\run-wrapper.ps1
# Alternatively, open start-cc-remote.cmd
```

The launcher checks `.venv` and `.env`. A private `.runtime/active-wrapper.json` selects a frozen release when present; otherwise it runs the checkout. It also attempts to refresh stale DSH pairing.

Select the device and engine in Web. Verify native identity, cwd, control state and history. A healthy `/healthz` endpoint or model catalog alone does not establish session acceptance.

## Engine setup

### Claude Code

Set `CLAUDE_BIN` to the daily official CLI's real absolute path. Wrapper uses locked `claude-agent-sdk==0.2.157` and checks SDK and CLI versions at startup.

Model accounts and endpoints use native settings. The current SDK child policy clears inherited proxy variables; changing Wrapper's `HTTPS_PROXY` alone does not change that model route. If a model service requires a proxy, inspect [child_env.py](cc_remote/wrapper/child_env.py) and native configuration first.

Keep single-writer protection while a terminal owns the transcript. A fork, copied log or different cwd is not a continuation of the same conversation.

### Codex and Codex App

Use `CC_REMOTE_CODEX_DAEMON=auto` for CLI and Wrapper to share the account's official daemon. Explicit `off` uses private stdio and does not establish sharing. An existing controlled loopback WebSocket setup may set `CODEX_APP_SERVER_WS_URL`; non-loopback endpoints are rejected.

```powershell
codex app-server daemon version
codex resume <thread-ID>
```

Verify the daemon, account home and CLI version. Align versions through the original installation channel after updates, preserving active tasks.

For App sharing, finish active work, exit normally, then use the separate launcher:

```powershell
.\scripts\start-codex-shared-app.ps1
.\scripts\start-codex-shared-app.ps1 -Action check
```

Only `verified` establishes an actual shared App connection. The launcher preserves the official application and default account, and never forces an existing App to exit. Recheck after updates; see [Windows App acceptance](docs/codex-desktop-windows.md).

### DSH desktop pairing

Load both extensions in the desktop profile's patch configuration, using real checkout or active-release paths:

```yaml
- insert:
    - id: cc-remote-desktop-url
      name: C:/path/to/cc-remote-windows/integrations/dsh/cc-remote-desktop-url.mjs
    - id: cc-remote-metadata
      name: C:/path/to/cc-remote-windows/integrations/dsh/cc-remote.mjs
```

The default patch is `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml`; locate it through native settings for a custom `DSH_HOME` or profile. Finish active work, restart DSH normally, then pair:

```powershell
.\scripts\pair-desktop.ps1
```

The extension exports private state; pairing checks the process and native contract before saving control credentials. After port or credential rotation, finish work before re-pairing and restarting Wrapper. Preserve the desktop Agent's lifecycle.

Never paste tokens, connection files or token-bearing login URLs into Git, chat or browser parameters. See [DSH integration](docs/dsh-integration.md) for native capabilities and versions.

## Collaboration rooms

A room binds a directory and members. Dispatch with `@member`, `@all` or `@全体`; quote results, hand off a message, select models and open full sessions.

**Each member has a separate native session.** Bounded recent context and explicit quotes transfer information. The current room adapter requires an existing `dsh@<native-ID>` for DSH members; ordinary DSH chat can still create sessions.

Defaults are six recent messages and a 6000-character budget, configured with `CC_REMOTE_ROOM_CONTEXT_DEPTH` and `CC_REMOTE_ROOM_CONTEXT_BUDGET`. Inspect tools in members' full sessions. Automatic adviser/executor workflows are not implemented. See the [design](docs/collaboration-rooms.md) and [roadmap](docs/collaboration-rooms-optimization.md).

## Deployment and upgrades

Follow [deploy/README.md](deploy/README.md) and the [installation guide](docs/installation_en.md). Windows Wrapper uses the source launcher above. Follow the relevant guide for Linux/macOS or published upstream packages, checking that the selected artifact includes this branch's features.

Linux releases live under `/opt/cc-remote/releases/`, activated atomically through `/opt/cc-remote/current`. Create release-local environments instead of copying a venv that references an old interpreter. Preserve external configuration, private state and a complete rollback set.

Relay, Web and every Wrapper must use one tested source snapshot or coordinated artifacts. Equal product versions do not guarantee equal features. Protocol changes require coordinated upgrades across all three tiers.

```text
Check protocol/build -> stage every target -> drain tasks and queues
-> snapshot state -> activate atomically -> verify public and native session paths
```

VPS + Caddy supports a fixed HTTPS entry point. Quick tunnels suit temporary checks but may change address after restart. Manage domains, origins and credentials through the deployment guide. Browser Origin must match `PUBLIC_ORIGIN` exactly.

## Troubleshooting

Separate the **control link** (browser ↔ Relay ↔ Wrapper) from the **model link** (engine ↔ model service). The launcher writes to its terminal; file logs depend on the actual service or launcher.

| Symptom | Check |
|---|---|
| `4406 protocol upgrade required` | Compare Relay `/healthz`, Web build and Wrapper protocol; upgrade all three together |
| WebSocket 401/403 | Check login, device authorization and exact Origin; sign in again after Relay restart. Tokens never belong in URLs |
| Immediate socket closure | Inspect close reasons and protocol validation; custom clients must not send undefined fields |
| `Connection dropped` / `ECONNRESET` | Identify the link. If native clients also fail, check model host, port, direct/proxy route and `NO_PROXY` |
| Read-only / unknown ownership | Preserve tasks; check process visibility, private-file ACLs and dependencies. Incomplete checks keep protection active |
| `already has an active writer` | Check ID, account, cwd and clients; finish work and exit normally or explicitly hand over control |
| Configuration appears ineffective | Check BOM, environment overrides, active frozen source and paired-device settings |
| Missing or duplicated history | Verify native ID, original cwd, account and source logs before projection/SQLite errors; preserve originals |
| Stale DSH pairing | Check the process and extensions; finish work before re-pairing and restarting Wrapper |

History reads do not create model turns. Run live acceptance separately and inspect native receipts before repeating a mutation with an unknown outcome.

## Boundaries and security

- Windows private-file checks use real ownership and DACLs. Codex checks process generations, file identity and write access; incomplete inspection remains read-only.
- The independent Windows Viewer resource channel has not passed complete platform acceptance. It is separate from ordinary previews and can be disabled with `CC_REMOTE_VIEWER_CHANNEL=off`; see [Viewer boundaries](docs/remote-viewer.md).
- DSH currently has no Work, session deletion, worktree migration or rollback adapter. Controls reflect native capabilities.
- Keep `.env`, device/pairing files, account homes and `.runtime` out of Git. Browsers use cookies; Wrappers use Bearer credentials at upgrade. Authorization is scoped by `machine_id`.
- Use HTTPS/WSS publicly. Explicit plain HTTP exposes passwords, cookies and session traffic and requires separate configuration.

## Development and validation

Use Python 3.13, Node 24 and dependency locks. Install the complete development environment:

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm --prefix web ci
.\.venv\Scripts\python.exe -m pytest
npm --prefix web run build
npm --prefix web run test:reliability
npm --prefix web run lint
```

The complete gate, including Ruff, ShellCheck and Bash checks, is in [AGENTS.md](AGENTS.md#commit-and-pr-gate). GitHub CI builds Web and runs Windows/Linux pytest for PRs and `main` / `master` pushes. Unit tests do not call models; [live probes](scripts/live/) run separately and may spend tokens.

The verified baseline on 2026-11-05, `dec6bbb`, passed 5685 Windows tests and 6710 Linux tests, with 594 and 14 platform skips. Checks covered all three native histories, App shared transport, public live replies, reconnects and desktop/mobile session information. This records tested scope and does not replace acceptance of a new deployment.

## Documentation

| Topic | Guide |
|---|---|
| Installation, upgrades, rollback and acceptance | [Installation](docs/installation_en.md) · [Deployment contract](deploy/README.md) |
| Windows Codex App | [Shared launcher and acceptance](docs/codex-desktop-windows.md) |
| DSH native capabilities | [Integration](docs/dsh-integration.md) |
| Terminal workspace | [TUI English](docs/tui.md) · [中文](docs/tui_zh.md) |
| Collaboration | [Design](docs/collaboration-rooms.md) · [Roadmap](docs/collaboration-rooms-optimization.md) |
| Independent Claude session service | [Lifecycle and migration](docs/claude-session-service.md) |
| Upstream reference | [README.upstream.md](README.upstream.md) |
| Changes and license text | [CHANGELOG.md](CHANGELOG.md) · [LICENSE](LICENSE) |
