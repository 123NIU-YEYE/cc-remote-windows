$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root ".runtime"
# This is the legacy `dsh web` entry point. It needs an installed @deepseek-ai/dsh
# CLI, which is NOT the same thing as the DeepSeek Harness desktop app; the
# desktop app is proxied by scripts/desktop-dsh-supervisor.mjs instead. Point
# CC_REMOTE_DSH_BIN at that CLI's lib/bin.js, and the remaining variables at the
# relay that terminates the tunnel.
$Node = if ($env:CC_REMOTE_NODE) { $env:CC_REMOTE_NODE } else { "C:\Program Files\nodejs\node.exe" }
$DshBin = $env:CC_REMOTE_DSH_BIN
$Ssh = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
$Vps = if ($env:CC_REMOTE_DSH_RELAY_TARGET) { $env:CC_REMOTE_DSH_RELAY_TARGET } else { "root@106.55.19.100" }
$TrustedHost = if ($env:CC_REMOTE_DSH_TRUSTED_HOST) { $env:CC_REMOTE_DSH_TRUSTED_HOST } else { "106.55.19.100" }
$PublicBaseUrl = if ($env:CC_REMOTE_DSH_PUBLIC_URL) { $env:CC_REMOTE_DSH_PUBLIC_URL } else { "http://106.55.19.100/dsh/" }
$DshPort = if ($env:CC_REMOTE_DSH_PORT) { [int]$env:CC_REMOTE_DSH_PORT } else { 3080 }
$RemoteTunnelPort = if ($env:CC_REMOTE_DSH_TUNNEL_PORT) { [int]$env:CC_REMOTE_DSH_TUNNEL_PORT } else { 3081 }
$LockFile = Join-Path $Runtime "dsh-remote-supervisor.lock"
$LogFile = Join-Path $Runtime "dsh-remote-supervisor.log"
$DshLogFile = Join-Path $Runtime "dsh-web.log"
$PublicUrlFile = Join-Path $Runtime "dsh-public-url.txt"

New-Item -ItemType Directory -Path $Runtime -Force | Out-Null

function Write-SupervisorLog([string]$Message) {
    Add-Content -LiteralPath $LogFile -Encoding UTF8 `
        -Value ("{0:o} {1}" -f (Get-Date), $Message)
}

if (Test-Path -LiteralPath $LockFile) {
    $lockPid = Get-Content -LiteralPath $LockFile -ErrorAction SilentlyContinue
    if ($lockPid -and (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) {
        Write-SupervisorLog "Another supervisor is running (PID $lockPid). Exiting."
        exit 0
    }
}
$PID | Set-Content -LiteralPath $LockFile -Encoding ASCII -Force

foreach ($requiredFile in @($Node, $DshBin, $Ssh)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required executable or script not found: $requiredFile"
    }
}

function Test-DshWeb {
    try {
        $response = Invoke-WebRequest `
            -Uri "http://127.0.0.1:$DshPort/favicon.svg" `
            -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-ExistingDshProcess {
    Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match '@deepseek-ai[\\/]dsh[\\/]lib[\\/]bin\.js' -and
            $_.CommandLine -match '(?:^|\s)web(?:\s|$)'
        } |
        Select-Object -First 1
}

function Start-DshJob {
    Remove-Item -LiteralPath $PublicUrlFile -Force -ErrorAction SilentlyContinue
    Write-SupervisorLog "Starting DSH Web on 127.0.0.1:$DshPort."

    Start-Job -ScriptBlock {
        param($node, $bin, $trustedHost, $publicBaseUrl, $logFile, $publicUrlFile)

        & $node $bin web --no-open --trusted-host $trustedHost 2>&1 |
            ForEach-Object {
                $line = [string]$_
                if ($line -match '^dsh web:\s+\S*[?&]token=(?<token>[^&\s]+)') {
                    $publicUrl = $publicBaseUrl + "?token=" + $Matches.token
                    Set-Content -LiteralPath $publicUrlFile -Encoding UTF8 `
                        -Value $publicUrl -Force
                    $line = $line -replace 'token=[^&\s]+', 'token=<redacted>'
                }
                Add-Content -LiteralPath $logFile -Encoding UTF8 -Value $line
            }
    } -ArgumentList $Node, $DshBin, $TrustedHost, $PublicBaseUrl, $DshLogFile, $PublicUrlFile
}

function Start-ReverseTunnel {
    Write-SupervisorLog "Starting reverse SSH tunnel on VPS port $RemoteTunnelPort."
    Start-Process -FilePath $Ssh -ArgumentList @(
        "-N",
        "-T",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=20",
        "-o", "ServerAliveCountMax=3",
        "-o", "ConnectTimeout=10",
        "-R", "127.0.0.1:${RemoteTunnelPort}:127.0.0.1:${DshPort}",
        $Vps
    ) -WindowStyle Hidden -PassThru
}

$existingDsh = Get-ExistingDshProcess
if ($existingDsh -and (Test-DshWeb)) {
    $dshJob = $null
    Write-SupervisorLog "Adopting existing DSH Web process (PID $($existingDsh.ProcessId))."
} else {
    $dshJob = Start-DshJob
}

$tunnel = $null
while ($true) {
    try {
        if ($dshJob -and $dshJob.State -ne "Running") {
            Write-SupervisorLog "DSH Web job stopped (state $($dshJob.State)); restarting."
            Receive-Job $dshJob -ErrorAction SilentlyContinue | Out-Null
            Remove-Job $dshJob -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            $dshJob = Start-DshJob
        } elseif (-not $dshJob -and -not (Test-DshWeb)) {
            Write-SupervisorLog "Adopted DSH Web process stopped; starting a managed instance."
            $dshJob = Start-DshJob
        }

        if (-not $tunnel -or $tunnel.HasExited) {
            if ($tunnel) {
                Write-SupervisorLog "Reverse SSH tunnel exited with code $($tunnel.ExitCode); reconnecting."
            }
            $tunnel = Start-ReverseTunnel
        }
    } catch {
        Write-SupervisorLog "Supervisor error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 10
}
