# Compatibility alias for the single Windows Wrapper entry point.
& (Join-Path $PSScriptRoot "run-wrapper.ps1")
exit $LASTEXITCODE
