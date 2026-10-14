$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root ".runtime"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Codex = "D:\Codex\app\resources\codex.exe"
$AppServerUrl = "http://127.0.0.1:8766/healthz"
$LockFile = Join-Path $Runtime "wrapper-supervisor.lock"

New-Item -ItemType Directory -Path $Runtime -Force | Out-Null

# Single-instance lock
if (Test-Path $LockFile) {
    $lockPid = Get-Content $LockFile -ErrorAction SilentlyContinue
    if ($lockPid -and (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) {
        Write-Host "Another supervisor is running (PID $lockPid). Exiting."
        exit 0
    }
}
$PID | Out-File $LockFile -Force

function Test-AppServer {
    try {
        $response = Invoke-WebRequest -Uri $AppServerUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-AppServerProcess {
    Get-CimInstance Win32_Process -Filter "Name='codex.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'app-server.*8766' } |
        Select-Object -First 1
}

function Clear-StalePortHolders {
    # Kill any process still holding port 8766 (dead backend's orphans: conhost, inherited handles)
    $holders = Get-NetTCPConnection -LocalPort 8766 -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($holderPid in $holders) {
        if ($holderPid -le 4) { continue }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$holderPid" -ErrorAction SilentlyContinue
        if ($proc) {
            $msg = "{0:o} Killing stale port holder: PID {1} ({2})" -f (Get-Date), $holderPid, $proc.Name
            Add-Content (Join-Path $Runtime "wrapper-supervisor.log") -Value $msg
            Stop-Process -Id $holderPid -Force -ErrorAction SilentlyContinue
            # Also kill its conhost children which inherit socket handles
            Get-CimInstance Win32_Process -Filter "ParentProcessId=$holderPid" -ErrorAction SilentlyContinue |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
    if ($holders) { Start-Sleep 2 }
}

function Start-AppServer {
    if (Test-AppServer) {
        return (Get-AppServerProcess).ProcessId
    }

    Clear-StalePortHolders

    if (-not (Test-Path -LiteralPath $Codex -PathType Leaf)) {
        throw "Codex executable not found: $Codex"
    }

    $proc = Start-Process -FilePath $Codex `
        -ArgumentList @("app-server", "--listen", "ws://127.0.0.1:8766") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Runtime "app-server.stdout.log") `
        -RedirectStandardError (Join-Path $Runtime "app-server.stderr.log") `
        -PassThru

    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (Test-AppServer) {
            return $proc.Id
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Codex app-server did not become healthy within 20s"
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "cc-remote Python environment not found: $Python"
}

# Start Python wrapper in background (non-blocking)
$wrapperJob = Start-Job -ScriptBlock {
    param($py, $root, $runtime)
    Set-Location $root
    & $py -m cc_remote.wrapper 2>&1 |
        Out-File (Join-Path $runtime "wrapper-job.log") -Append
} -ArgumentList $Python, $Root, $Runtime

$msg = "{0:o} Supervisor started. Python wrapper job: {1}" -f (Get-Date), $wrapperJob.Id
Add-Content (Join-Path $Runtime "wrapper-supervisor.log") -Value $msg

# Backend watchdog loop
$backendPid = $null
while ($true) {
    try {
        # Check if backend is healthy
        if (-not (Test-AppServer)) {
            $msg = "{0:o} Backend unhealthy. Restarting..." -f (Get-Date)
            Add-Content (Join-Path $Runtime "wrapper-supervisor.log") -Value $msg

            # Kill stale backend if exists
            $stale = Get-AppServerProcess
            if ($stale) {
                Stop-Process -Id $stale.ProcessId -Force -ErrorAction SilentlyContinue
                Start-Sleep 2
            }

            $backendPid = Start-AppServer
            $msg = "{0:o} Backend restarted (PID {1})" -f (Get-Date), $backendPid
            Add-Content (Join-Path $Runtime "wrapper-supervisor.log") -Value $msg
        }

        # Check if Python wrapper job is still running
        if ($wrapperJob.State -ne 'Running') {
            $msg = "{0:o} Python wrapper job stopped (State: {1}). Restarting..." -f (Get-Date), $wrapperJob.State
            Add-Content (Join-Path $Runtime "wrapper-supervisor.log") -Value $msg
            Remove-Job $wrapperJob -Force -ErrorAction SilentlyContinue

            $wrapperJob = Start-Job -ScriptBlock {
                param($py, $root, $runtime)
                Set-Location $root
                & $py -m cc_remote.wrapper 2>&1 |
                    Out-File (Join-Path $runtime "wrapper-job.log") -Append
            } -ArgumentList $Python, $Root, $Runtime
        }

    } catch {
        $msg = "{0:o} Supervisor error: {1}" -f (Get-Date), $_.Exception.Message
        Add-Content (Join-Path $Runtime "wrapper-supervisor.log") -Value $msg
    }

    Start-Sleep -Seconds 10
}
