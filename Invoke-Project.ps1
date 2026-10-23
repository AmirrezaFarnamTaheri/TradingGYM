Param(
    [Parameter(Position=0)]
    [string]$Task,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$TaskArguments
)

$ErrorActionPreference = 'Stop'
$Runner = Join-Path $PSScriptRoot 'scripts/project.py'

if ($Task -in @('ObservabilityUp', 'ObservabilityDown')) {
    Write-Error 'No observability Compose manifest is shipped. Configure an operator-managed monitoring deployment instead.'
    exit 2
}
if ($Task -eq 'Clean') {
    Write-Error 'Automatic recursive cleanup is disabled to protect virtual environments. Remove project-generated caches deliberately.'
    exit 2
}

$RunnerArguments = @($Runner)
if ($Task) {
    $NormalizedTask = $Task.ToLowerInvariant()
    if ($NormalizedTask -eq 'typecheckfull') { $NormalizedTask = 'typecheck-full' }
    $RunnerArguments += $NormalizedTask
}
if ($TaskArguments) { $RunnerArguments += $TaskArguments }

# Arrays preserve spaces and keep task arguments out of expression evaluation.
& python @RunnerArguments
exit $LASTEXITCODE
