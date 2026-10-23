param([int]$ApiPort=8000, [int]$FrontendPort=3000, [switch]$SkipBrowser, [switch]$DryRun)
$ErrorActionPreference="Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$guiArgs = @("start", "--api-port", "$ApiPort", "--frontend-port", "$FrontendPort")
if ($SkipBrowser) { $guiArgs += "--skip-browser" }
if ($DryRun) { $guiArgs += "--dry-run" }
& python (Join-Path $projectRoot "scripts/gui.py") @guiArgs
exit $LASTEXITCODE
