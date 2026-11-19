# Windows upstream migration foundation

This Windows adaptation is based on upstream v5.3.52 (`c8c8326`), wire protocol
97 and Claude Agent SDK 0.2.174. Production activation still requires the repository deployment gate and
transaction procedure; a Git push alone is not a deployment receipt.

## Implemented foundation

- Native Windows process creation identity, account ownership and writable-file
  holder scans; uncertainty remains read-only rather than permitting takeover.
- Windows advisory file locks and one Wrapper per private state directory.
- Native DACL validation for private state and pairing credentials. Credentials
  are protected at file creation rather than after writing their contents.
- Binary descriptor I/O and signed SQLite representation of native device/inode
  identity, preserving equality without overflowing SQLite INTEGER columns.
- Real Windows directory handles and NT RootDirectory child reads. Renaming and
  replacing a directory path does not redirect reads through the original fd.
  Reparse points are rejected by no-follow opens. No global permission masking,
  fake directory descriptors or remembered-path emulation is used.
- Official shared Codex control support and explicit loopback WS compatibility.
- Large Claude history partial-write/read failures stay provisional; they never
  publish an authoritative empty page that erases the browser's history.
- Explicit environment HTTP proxy selection, reconnect announcement ordering,
  bounded queues and application heartbeat handling.
- Desktop attention policy for completion/questions when the browser window
  loses focus; notification settings include a bounded test and capability copy.
- DSH produced-file paths select the source Windows/POSIX namespace.

## Isolated Windows development

Use Python 3.13 and Node 24. Keep the checkout, virtual environment, ports,
account roots and private state separate from an existing installation.
Set `PYTHON_DOTENV_DISABLED=1` before importing cc-remote in a development or
verification process; otherwise python-dotenv may discover a parent's `.env`.
Do not copy model credentials into development state.

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
npm.cmd --prefix web ci
npm.cmd --prefix web run build
.venv/Scripts/python.exe -X utf8 scripts/verify-windows-loopback.py
```

The loopback check starts its own Relay and Wrapper with random credentials,
empty temporary account roots and an OS-selected local port. It checks HTTP
health, build/protocol identity, authenticated WebSocket and an empty session
catalog, then stops only its own processes. It does not submit a model turn.
This proves the isolated control link, not native model execution or deployment.

## Remaining acceptance

- Run the complete Python gate in a supported Linux environment. Windows
  collection still encounters Unix-only service/broker/retention tests (`pwd`,
  `fcntl` and PTY dependencies). Do not count collection failures as test passes.
- Native Windows descriptor-relative stat, enumeration, mkdir, atomic replace
  and unlink are covered by real handle tests, including directory replacement.
  Continue the end-to-end media/file-operation acceptance on the activated build.
- Claude's separate Unix peer-authenticated service, PTY broker and local session
  messaging are intentionally rejected on Windows. A Windows service transport
  needs its own authenticated design and SDK lifetime migration acceptance.
- Verify the daily Claude executable/sign-in, real interrupt-to-Result draining,
  queued input and native Codex shared ownership with explicit live acceptance.
  The default native Claude path ends in `claude.exe` on Windows.
- Check actual Edge/Chrome notifications for focus/minimize/multiple tabs and
  click routing. API acceptance does not prove that Windows displayed a toast.
  Closed-page notifications depend on Web Push, browser background operation,
  network and permission; an exited browser is not guaranteed to receive them.
- Classify and retain original local extensions before merging; this preview
  does not replace a customized installation wholesale.
- Before any deployment, read the repository skill and complete deployment
  contract, build/stage one immutable snapshot and verify all tier identities,
  SDK/service admission, rollback and expected Wrapper connectivity.

The original checkout and live services are outside this preview's activation.
