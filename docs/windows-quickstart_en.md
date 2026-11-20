# Windows quick start: try Claude Code locally

[中文](windows-quickstart.md) · [Home](../README_en.md) · [Windows compatibility](windows-upstream-migration.md)

This guide is for a first source installation of this fork. Connect the browser, Relay and Wrapper on one Windows computer before setting up phone or public access. It does not install system services or enable Work automatically.

**If an installation is already running, use its upgrade procedure.** Do not replace its configuration or start a second Wrapper.

## 1. Prepare your environment

Use **PowerShell on Windows**. Install:

- [Git for Windows](https://git-scm.com/downloads/win).
- [Python 3.13](https://www.python.org/downloads/), with PATH enabled during installation.
- [Node.js 24](https://nodejs.org/en/download).
- A signed-in [Claude Code](https://code.claude.com/docs/en/setup) installation that can already hold a conversation locally. The minimum CLI version is `2.1.263`; use the current stable version where possible.

Open a new PowerShell window and check:

```powershell
git --version
python --version
node --version
npm.cmd --version
claude --version
```

Python should report `3.13.x` and Node should report `v24.x`. Run `claude` in another terminal and confirm that it replies before continuing.

## 2. Download and install

Run these commands from the directory where you want the project:

```powershell
git clone https://github.com/123NIU-YEYE/cc-remote-windows.git
Set-Location cc-remote-windows

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: --no-binary=http-ece -r requirements.lock

npm.cmd --prefix web ci
npm.cmd --prefix web run build
```

Continue only after each command succeeds. Using `npm.cmd` avoids changing PowerShell's script execution policy. Dependencies come from the lock file; `http-ece` builds from its source distribution while the other dependencies use binary packages.

## 3. Generate local configuration

Run this complete block in the same project directory. It creates separate random browser login, session-signing and Wrapper credentials for the local trial. **It stops if `.env` already exists.**

```powershell
if (Test-Path -LiteralPath .env) {
    throw '.env already exists. Edit the existing configuration instead of replacing it.'
}

$projectPath = (Get-Location).Path.Replace('\', '/')
$loginPassword = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(24))"
if ($LASTEXITCODE -ne 0) { throw 'Could not generate login password.' }
$sessionSecret = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
if ($LASTEXITCODE -ne 0) { throw 'Could not generate session secret.' }
$wrapperToken = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
if ($LASTEXITCODE -ne 0) { throw 'Could not generate wrapper token.' }

$configuration = @"
RELAY_HOST=127.0.0.1
RELAY_PORT=8765
PUBLIC_ORIGIN=http://127.0.0.1:8765
RELAY_URL=ws://127.0.0.1:8765/ws
LOGIN_PASSWORD=$loginPassword
SESSION_SECRET=$sessionSecret
WRAPPER_TOKEN=$wrapperToken
WEB_STATIC_DIR=web/dist
CC_REMOTE_MACHINE_ID=windows-pc
CC_CWD="$projectPath"
CLAUDE_BIN=
ALLOW_PRIVATE_ORIGINS=0
ALLOW_INSECURE_HTTP=0
"@
[System.IO.File]::WriteAllText((Join-Path (Get-Location).Path '.env'), $configuration, [System.Text.UTF8Encoding]::new($false))

Write-Host "Browser login password: $loginPassword"
```

Save the displayed **Browser login password** for signing in. Do not upload the configuration, keys or screenshots of credentials to Git.

`CC_CWD` initially points to the project directory. You can choose your own project when creating a Code session. Leaving `CLAUDE_BIN` empty selects `.local/bin/claude.exe` under your user directory. If your CLI is installed elsewhere, edit `.env` and set its actual absolute executable path, for example `CLAUDE_BIN=C:/Tools/Claude/claude.exe`.

Your model account remains managed by the local Claude installation. The browser login password and model API key have different purposes; do not substitute one for the other.

## 4. Start Relay and Wrapper

Open two PowerShell terminals in the project directory. Run one command in each and leave both terminals open.

**Terminal one: serve the website and relay**

```powershell
.\.venv\Scripts\python.exe -X utf8 -m cc_remote.relay
```

**Terminal two: connect your local Claude installation**

```powershell
.\.venv\Scripts\python.exe -X utf8 -m cc_remote.wrapper
```

Both commands must run from the project directory containing `.env`. `-X utf8` avoids locale-dependent encoding problems without changing Windows settings.

## 5. Send your first message

1. Open [http://127.0.0.1:8765](http://127.0.0.1:8765) on this computer.
2. Sign in with the password generated in step 3.
3. Confirm the device is connected, then select **Claude → Code**.
4. Create a session, send a short message and confirm that a reply arrives. This step uses your model allowance.
5. Add images or files through the attachment button; use session files to browse the current directory.

`127.0.0.1` refers to the device you are using. Opening that address on your phone will not connect to your computer.

## 6. Connect from your phone or over the internet

Follow the [source deployment guide](installation_en.md#source-install) to install Relay and the matching Web build on a Linux server with HTTPS. Then connect Windows Wrapper to that service.

If you already have local configuration, finish running and queued tasks and stop the local trial processes before changing the connection:

| Setting | Meaning for public deployment |
|---|---|
| Relay's `PUBLIC_ORIGIN` | The actual browser HTTPS address, such as `https://remote.example.com` |
| Wrapper's `RELAY_URL` | Its WebSocket address, such as `wss://remote.example.com/ws` |
| Wrapper device credential | Enroll through the website's device center; legacy manual credentials must match Relay authorization |

Replace the example domain with your own address. **Do not upload the entire local `.env` to the server or overwrite an existing production configuration.** See the [installation guide](installation_en.md#source-install) for pairing.

When switching to device-center pairing, remove the `WRAPPER_TOKEN` and `CC_REMOTE_MACHINE_ID` lines from the Windows trial `.env` and clear any old values from the terminal environment. This lets Wrapper read the enrolled device credential. Empty entries still override pairing values; the local trial token does not authorize a device on the server.

A fixed domain is the usual setup. HTTPS on a public IP needs a certificate covering that IP. Temporary tunnel addresses can change. Starting from source and installing a managed background service are different steps; follow the [deployment procedure](../deploy/README.md) for service installation, upgrades and rollback.

## Troubleshooting

<a id="claude-work"></a>

### Code works, but Claude Work fails to start

Claude Work requires an OS sandbox. Claude currently supports sandboxing on macOS, Linux and WSL2, but not native Windows. Startup logs may contain `Sandbox required but unavailable`.

Choose **Code** with your existing Windows Wrapper. For Work, separately install, configure and run Wrapper in macOS, Linux or WSL2. Opening a WSL terminal does not change the Wrapper already running on Windows. Keep Work's required sandbox enabled.

See the [official Claude sandboxing guide](https://code.claude.com/docs/en/sandboxing).

<a id="connection-troubleshooting"></a>

### The device is offline or keeps reconnecting

Check in this order:

1. The computer is awake and the Wrapper terminal is open.
2. Relay is still running and its website is reachable.
3. For the local trial, use exactly `http://127.0.0.1:8765`. Switching to `localhost` also changes the browser Origin.
4. The public address has not changed, and `PUBLIC_ORIGIN` and `RELAY_URL` match it.
5. Relay, Web and Wrapper use compatible code, and a proxy or VPN is not blocking WebSockets.

Check task status before resending a message that might already have been accepted.

### Python or Claude cannot be found

Open a new terminal and check versions again. If `python` opens Microsoft Store, check Windows app execution aliases and your Python installation path. If Claude works in the terminal but cannot be found through Web, confirm that `.env` points `CLAUDE_BIN` to the actual `claude.exe`.

### Port 8765 is already in use

Check whether Relay is already running before starting another process. To use a different port, update `RELAY_PORT`, `PUBLIC_ORIGIN`, `RELAY_URL` and the address in your browser together.

### How do I connect Codex?

Install and sign in to the official Codex CLI first. Shared sessions require Wrapper and the terminal to connect to the same official app-server; Windows supports an explicit loopback WebSocket endpoint. See [desktop and Web sharing](../README_en.md#use-desktop-and-web-together) for the differences between the three engines.

<a id="codex-shared-entry"></a>

### Codex shared entry points on Windows

On a computer with shared launchers configured, **Codex Shared** opens App and **Codex CLI Shared** opens the terminal. Codex Code in Web must use the same account and daemon. These are local shortcut names, not official Codex commands, and `git clone` does not install them.

Complete [shared-control acceptance](../deploy/README.md#codex-code-shared-control-plane-acceptance) first, then use the repository's [`cc_remote.codex_windows_shared`](../cc_remote/codex_windows_shared.py) module. Let the old App's work finish and quit normally; preserve running App, terminal and daemon processes during setup.

Run the example below from this repository directory. It requires the official Codex App installed as a Windows application package and the current account's official daemon already reporting `running`. It selects a matching official CLI in the current PowerShell session and launches App through a loopback-only bridge:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$daemonInfo = codex app-server daemon version | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $daemonInfo.status -ne 'running') {
    throw 'Start the selected account daemon normally before configuring sharing.'
}
$package = Get-AppxPackage -Name OpenAI.Codex | Sort-Object Version -Descending | Select-Object -First 1
if (-not $package) { throw 'The official Codex App package is not installed.' }
$manifest = Get-AppxPackageManifest -Package $package.PackageFullName
$relativeApp = $manifest.Package.Applications.Application | Select-Object -First 1 -ExpandProperty Executable
$appPath = Join-Path $package.InstallLocation $relativeApp
$env:PYTHON_DOTENV_DISABLED = '1'
$env:CODEX_HOME = $codexHome
$env:CODEX_BIN = $daemonInfo.managedCodexPath
$selectedVersion = & $env:CODEX_BIN --version
if ($LASTEXITCODE -ne 0 -or $selectedVersion -ne ('codex-cli ' + $daemonInfo.appServerVersion)) {
    throw 'CLI and running daemon versions must match; finish active work before updating.'
}
.\.venv\Scripts\python.exe -X utf8 -m cc_remote.codex_windows_shared app --home $codexHome --port 8766 --app $appPath
```

These variables affect only this terminal and its children. From the same PowerShell session, check App's actual connection:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m cc_remote.codex_windows_shared check --home $codexHome --port 8766 --app $appPath
```

`state: verified` means that App currently connects to the selected daemon's local bridge. Use the matching CLI to explicitly connect to the same endpoint:

```powershell
& $env:CODEX_BIN --remote ws://127.0.0.1:8766
```

An operator can save these verified launch paths as **Codex Shared** and **Codex CLI Shared** shortcuts. Keep installation paths, Python locations and account directories local. Recheck after App, CLI or Wrapper updates and keep the bridge port on loopback.

Once App and terminal connections pass, verify that Wrapper's corresponding account reaches the same official service. Seeing the same history files alone does not establish three-client sharing; read-only connection checks also do not prove live model replies. This applies to **Codex Code** and preserves Work's private backend.

### Desktop notifications do not appear

Allow notifications for the site in your browser, enable alerts in the app and send a test notification. Use HTTPS publicly and check OS focus modes. Notifications after closing the page also need server-side Web Push configuration; see [configuration](configuration_en.md).
