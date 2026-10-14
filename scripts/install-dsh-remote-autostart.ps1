$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $PSScriptRoot "start-dsh-desktop-remote.ps1"
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "cc-remote-dsh.lnk"
$PowerShell = Join-Path $PSHOME "powershell.exe"
$TaskName = "cc-remote-dsh-desktop"

if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw "DSH remote launcher not found: $Launcher"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PowerShell
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
$shortcut.WorkingDirectory = $Root
$shortcut.WindowStyle = 7
$shortcut.Description = "Start and supervise DeepSeek Harness Desktop remote access"
$shortcut.Save()

# The scheduled-task experiment is not used; remove only this task if present.
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null

Write-Output $ShortcutPath
