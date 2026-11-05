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
$SourceRoot = $Root
$ActiveFile = Join-Path $Root ".runtime\active-wrapper.json"
if (Test-Path -LiteralPath $ActiveFile -PathType Leaf) {
    $Activation = Get-Content -LiteralPath $ActiveFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Activation.schema -ne 1 -or -not ($Activation.source -is [string]) -or
        [System.IO.Path]::IsPathRooted($Activation.source)) {
        throw "Invalid Wrapper activation record."
    }
    $Candidate = [System.IO.Path]::GetFullPath((Join-Path $Root $Activation.source))
    $ReleasePrefix = [System.IO.Path]::GetFullPath((Join-Path $Root ".runtime\releases")) + "\"
    if (-not $Candidate.StartsWith($ReleasePrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath (Join-Path $Candidate "cc_remote\wrapper\__main__.py") -PathType Leaf)) {
        throw "Activated Wrapper source is outside the release directory or incomplete."
    }
    $SourceRoot = $Candidate
}
Set-Location -LiteralPath $SourceRoot

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
