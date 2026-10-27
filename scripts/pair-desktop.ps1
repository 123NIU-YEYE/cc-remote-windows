# Pair cc-remote with the running DeepSeek Harness *desktop* app on Windows.
#
# Reads the state file published by integrations/dsh/cc-remote-desktop-url.mjs,
# validates it against the live local DSH API, and writes a private pairing file.
#
# Usage:  .\scripts\pair-desktop.ps1
# Env:    CC_REMOTE_DSH_CONNECTION_FILE  output pairing file
#         CC_REMOTE_DSH_DESKTOP_FILE     state file written by the DSH extension
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Deps = Join-Path $Root ".pydeps"
$Conn = if ($env:CC_REMOTE_DSH_CONNECTION_FILE) {
    $env:CC_REMOTE_DSH_CONNECTION_FILE
} else {
    Join-Path $env:USERPROFILE ".config\cc-remote\dsh-connection.json"
}

if (-not (Test-Path (Join-Path $Deps "claude_agent_sdk"))) {
    Write-Error "缺少依赖目录 .pydeps。请先运行: python -m pip install --only-binary :all: --target .pydeps -r requirements-wrapper.txt"
}

# The portable Python used on Windows ships a ._pth file that disables
# PYTHONPATH, so extend sys.path explicitly instead.
$bootstrap = @"
import site, sys
site.addsitedir(r'$Deps')
sys.path.insert(0, r'$Root')
sys.argv = ['dsh_pair', '--file', r'$Conn', '--desktop']
from cc_remote.wrapper.dsh_pair import main
raise SystemExit(main())
"@

$bootstrap | python -
exit $LASTEXITCODE
