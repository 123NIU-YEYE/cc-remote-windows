$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $PSScriptRoot "start-windows-wrapper.ps1"
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "cc-remote-wrapper.lnk"
$PowerShell = Join-Path $PSHOME "powershell.exe"

if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw "Wrapper launcher not found: $Launcher"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PowerShell
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
$shortcut.WorkingDirectory = $Root
$shortcut.WindowStyle = 7
$shortcut.Description = "Start and supervise cc-remote wrapper"
$shortcut.Save()

Write-Output $ShortcutPath
