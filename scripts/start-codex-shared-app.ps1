# Launch the installed official Codex App against the same native daemon as
# ordinary `codex resume` and the Wrapper. Never stop an existing App.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Package = Get-AppxPackage -Name OpenAI.Codex | Sort-Object Version -Descending | Select-Object -First 1
if (-not $Package) { throw "The official Codex App is not installed for this user." }
$Manifest = Get-AppxPackageManifest -Package $Package.PackageFullName
$Relative = $Manifest.Package.Applications.Application | Select-Object -First 1 -ExpandProperty Executable
$App = Join-Path $Package.InstallLocation $Relative
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Missing project .venv." }
Set-Location -LiteralPath $Root
$ErrorActionPreference = "Continue"
$Output = & $Python -m cc_remote.codex_windows_shared app --app $App 2>&1
$Result = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($Result -ne 0) {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show(
        "请先完成现有 Codex App 任务并正常退出，再从共享入口打开。现有进程未被关闭。`n`n$($Output -join "`n")",
        "Codex Shared")
}
exit $Result
