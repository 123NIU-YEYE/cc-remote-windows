$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Node = "C:\Program Files\nodejs\node.exe"
$Supervisor = Join-Path $PSScriptRoot "desktop-dsh-supervisor.mjs"
if (-not (Test-Path -LiteralPath $Node -PathType Leaf)) { throw "Node.js not found: $Node" }
if (-not (Test-Path -LiteralPath $Supervisor -PathType Leaf)) { throw "Desktop supervisor not found: $Supervisor" }

$psi = [Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $Node
$psi.Arguments = '"' + $Supervisor + '"'
$psi.WorkingDirectory = $Root
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.EnvironmentVariables["CC_REMOTE_ROOT"] = $Root
$process = [Diagnostics.Process]::Start($psi)
$process.WaitForExit(500)
Write-Output "Desktop Node supervisor started: $($process.Id)"
