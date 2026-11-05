# Run the Windows Wrapper from this checkout and its own virtual environment.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Missing .venv. Create it and install requirements-wrapper.txt first."
}
if (-not (Test-Path -LiteralPath (Join-Path $Root ".env") -PathType Leaf)) {
    throw "Missing .env. Copy .env.example and configure this machine first."
}
Set-Location -LiteralPath $Root

# The desktop exports a new port/token on restart. Refresh only a stale pairing.
$StateFile = Join-Path $env:USERPROFILE ".dsh\cc-remote-desktop.json"
$PairFile = if ($env:CC_REMOTE_DSH_CONNECTION_FILE) {
    $env:CC_REMOTE_DSH_CONNECTION_FILE
} else {
    Join-Path $env:USERPROFILE ".config\cc-remote\dsh-connection.json"
}
if ((Test-Path -LiteralPath $StateFile) -and (
    -not (Test-Path -LiteralPath $PairFile) -or
    (Get-Item -LiteralPath $StateFile).LastWriteTime -gt (Get-Item -LiteralPath $PairFile).LastWriteTime
)) {
    & $Python -m cc_remote.wrapper.dsh_pair --file $PairFile --desktop
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Desktop pairing could not refresh; using the saved connection."
    }
}
# Network and engine settings come from the operator's environment and .env.
& $Python -m cc_remote.wrapper
exit $LASTEXITCODE
