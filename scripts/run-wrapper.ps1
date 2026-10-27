# Run the cc-remote Wrapper on Windows.
#
# The portable Python on this machine ships a `python312._pth` file that disables
# PYTHONPATH and site-packages discovery, so dependencies installed under
# `.pydeps` must be added explicitly. Everything else comes from `.env`.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Deps = Join-Path $Root ".pydeps"

if (-not (Test-Path (Join-Path $Deps "claude_agent_sdk"))) {
    Write-Error "缺少依赖目录 .pydeps。请先运行: python -m pip install --only-binary :all: --target .pydeps -r requirements-wrapper.txt"
}
if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Error "缺少 $Root\.env"
}

# The desktop app mints a new port and launch token every time it starts, so a
# saved pairing goes stale across reboots. Re-pair from the extension's state
# file whenever it is newer than the pairing file; failure is non-fatal.
$StateFile = Join-Path $env:USERPROFILE ".dsh\cc-remote-desktop.json"
$PairFile = Join-Path $env:USERPROFILE ".config\cc-remote\dsh-connection.json"
$NeedsPair = $false
if (Test-Path $StateFile) {
    if (-not (Test-Path $PairFile)) {
        $NeedsPair = $true
    } elseif ((Get-Item $StateFile).LastWriteTime -gt (Get-Item $PairFile).LastWriteTime) {
        $NeedsPair = $true
    }
}
if ($NeedsPair) {
    Write-Host "re-pairing with the running desktop app..."
    try {
        & (Join-Path $PSScriptRoot "pair-desktop.ps1")
    } catch {
        Write-Host "pairing failed, continuing with the existing pairing: $_"
    }
}

# The relay is reached through a Cloudflare tunnel. This network resets direct
# TLS to that domain and to api.anthropic.com, while the local Clash proxy
# reaches both reliably. cc_remote reads its WebSocket proxy from the
# environment, and the Claude/Codex CLIs are Node programs: many Node HTTP
# clients honour only the lower-case variables, so export both spellings.
# NO_PROXY keeps loopback traffic (the local DSH API) off the proxy.
if (-not $env:HTTPS_PROXY) {
    $env:HTTPS_PROXY = "http://127.0.0.1:7897"
    $env:HTTP_PROXY = "http://127.0.0.1:7897"
    $env:NO_PROXY = "localhost,127.0.0.1,::1"
}
$env:https_proxy = $env:HTTPS_PROXY
$env:http_proxy = $env:HTTP_PROXY
$env:no_proxy = $env:NO_PROXY

# Claude Code is a Node program. Node's built-in fetch ignores proxy variables
# unless the runtime is told to honour them, and several libraries look only at
# ALL_PROXY. This network resets direct TLS to api.anthropic.com, so the Claude
# CLI fails with ECONNRESET unless it reaches the API through Clash.
$env:ALL_PROXY = "http://127.0.0.1:7897"
$env:all_proxy = "http://127.0.0.1:7897"
$env:NODE_USE_ENV_PROXY = "1"

# NOTE: no proxy for Claude, and no relay. Claude Code talks to the operator's
# own gateway (ANTHROPIC_BASE_URL in ~/.claude/settings.json), which is reachable
# directly but times out through the local proxy, so cc_remote blanks the proxy
# variables for that child (see claude_profile_child_env).

$bootstrap = @"
import site, sys, runpy
site.addsitedir(r'$Deps')
sys.path.insert(0, r'$Root')
runpy.run_module('cc_remote.wrapper', run_name='__main__', alter_sys=True)
"@

$bootstrap | python -
exit $LASTEXITCODE
