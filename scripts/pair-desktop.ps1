# Pair with the running DSH desktop using this checkout's Python environment.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Missing .venv. Install requirements-wrapper.txt first."
}
$PairFile = if ($env:CC_REMOTE_DSH_CONNECTION_FILE) {
    $env:CC_REMOTE_DSH_CONNECTION_FILE
} else {
    Join-Path $env:USERPROFILE ".config\cc-remote\dsh-connection.json"
}
Set-Location -LiteralPath $Root
& $Python -m cc_remote.wrapper.dsh_pair --file $PairFile --desktop
exit $LASTEXITCODE
