# Windows Codex App shared-session acceptance

The separate launcher connects the installed official App to the account's
official native daemon, also used by ordinary `codex resume` and cc-remote.
The loopback bridge forwards bytes through the official `app-server proxy`;
it does not start another private app-server or own a model turn.

This App launch override is version-dependent. A launcher, a healthy bridge,
matching history files, or a configured environment variable does not establish
an actual App connection. Recheck after App/CLI upgrades.

## Prepare and open

Use the existing configured default Code account. On a multi-account host,
select the intended account explicitly with the Python helper's `--home` option;
do not merge account homes. Work retains its separate control plane.

1. Install the repository's locked Windows dependencies in `.venv` and verify
   ordinary CLI and Wrapper sharing using the deployment guide.
2. Finish active App tasks and exit the App normally. The shared launcher
   refuses an already running App and never terminates it or the daemon.
3. From the checkout, run:

   ```powershell
   .\scripts\start-codex-shared-app.ps1
   ```

The script resolves the installed `OpenAI.Codex` package for the current user.
Only the new App process receives the selected home and root-loopback WebSocket
endpoint. The official package, default launcher, global environment and native
credentials are preserved. Port 8766 is the helper's default; unrelated listeners
are rejected rather than replaced.

## Verify the actual connection

After the App opens, from an independent terminal run:

```powershell
.\scripts\start-codex-shared-app.ps1 -Action check
```

This read-only check emits bounded JSON and exits zero only for `verified`.
It checks the bridge executable/argv, owner, selected home and native daemon
identity, then the App's account/endpoint, both ends of an established TCP
connection, and unchanged process generations. It sends no model message.

`awaiting_reopen` means an installed App is running without a verified shared
connection. `not_running` means no matching App process was found.
`unavailable` means the selected bridge could not be verified. Preserve active
work, inspect the result, and do not force takeover or stop native processes to
make the check pass. Report App state separately from Relay/Web and CLI health.

A model-message round trip is a separate acceptance check requiring permission
to spend tokens. Use an isolated session and verify its native ID and actual
messages in both clients. Transport acceptance alone does not claim that test.

## Return to the default App

Exit the shared App normally and use the original official launcher. No global
override is installed. Retain the bridge while a shared App connection is live;
never stop the official daemon merely to remove the separate launch entry.
