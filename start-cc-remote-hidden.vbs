' Resolve the checkout from this file so moving it does not break the launcher.
Dim shell, files, root, cmd
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\run-wrapper.ps1"""
shell.Run cmd, 0, False
