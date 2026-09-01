Param(
    [Parameter(Position=0,Mandatory=$false)]
    [ValidateSet('Setup','Lint','Typecheck','Test','Train','Paper','Live','ObservabilityUp','ObservabilityDown','Smoke','Clean')]
    [string]$Task
)

$ErrorActionPreference = 'Stop'

# Show help if no task is provided
if (-not $Task) {
    Write-Host ""
    Write-Host "======================================================================" -ForegroundColor Magenta
    Write-Host "Trading Gym - Invoke Project Tasks" -ForegroundColor Magenta
    Write-Host "======================================================================" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "USAGE:" -ForegroundColor Cyan
    Write-Host "  .\Invoke-Project.ps1 -Task <TaskName>" -ForegroundColor Yellow
    Write-Host "  .\Invoke-Project.ps1 <TaskName>" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "AVAILABLE TASKS:" -ForegroundColor Cyan
    Write-Host "  Setup              - Install Python dependencies (Poetry or requirements.txt)"
    Write-Host "  Lint               - Run Ruff linter"
    Write-Host "  Typecheck          - Run MyPy type checker"
    Write-Host "  Test               - Run pytest tests"
    Write-Host "  Train              - Train the model"
    Write-Host "  Paper              - Run paper trading"
    Write-Host "  Live               - Run live trading"
    Write-Host "  ObservabilityUp    - Start observability stack (Docker)"
    Write-Host "  ObservabilityDown  - Stop observability stack (Docker)"
    Write-Host "  Smoke              - Run smoke test (verify dependencies)"
    Write-Host "  Clean              - Clean Python cache files"
    Write-Host ""
    Write-Host "EXAMPLES:" -ForegroundColor Cyan
    Write-Host "  .\Invoke-Project.ps1 -Task Setup" -ForegroundColor Yellow
    Write-Host "  .\Invoke-Project.ps1 -Task Smoke" -ForegroundColor Yellow
    Write-Host "  .\Invoke-Project.ps1 Setup" -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

function Exec($cmd, $arguments) {
    $displayArgs = if ($arguments -is [string]) { $arguments } else { ($arguments -join ' ') }
    Write-Host "`n> $cmd $displayArgs" -ForegroundColor Cyan
    if ($arguments -is [string]) {
        # Use Invoke-Expression to properly handle argument strings with spaces
        Invoke-Expression "$cmd $arguments"
    } elseif ($arguments -is [array]) {
        & $cmd $arguments
    } elseif ($null -ne $arguments) {
        & $cmd $arguments
    } else {
        & $cmd
    }
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

switch ($Task) {
    'Setup' {
        if (Test-Path 'poetry.lock') {
            Exec python '-m pip install -U pip'
            Exec python '-m pip install poetry'
            Exec poetry 'config virtualenvs.create false'
            Exec poetry 'install'
        } else {
            Exec python '-m pip install -U pip'
            Exec python '-m pip install -r requirements.txt'
        }
    }
    'Lint' { Exec ruff 'check .' }
    'Typecheck' { Exec mypy 'apps' }
    'Test' { Exec pytest '-q' }
    'Train' { Exec python '-m apps.trainer.main train --config config/app.yaml --timeframe 5m' }
    'Paper' { Exec python '-m apps.paper.main run --config config/app.yaml --model-path checkpoints/final_model.zip' }
    'Live' { Exec python '-m apps.live.main run --config config/app.yaml --model-path checkpoints/final_model.zip --live' }
    'ObservabilityUp' {
        if (Get-Command docker -ErrorAction SilentlyContinue) {
            Exec docker 'compose -f docker/docker-compose.observability.yml up -d'
        } else { Write-Warning 'Docker not found. Skipping.' }
    }
    'ObservabilityDown' {
        if (Get-Command docker -ErrorAction SilentlyContinue) {
            Exec docker 'compose -f docker/docker-compose.observability.yml down'
        } else { Write-Warning 'Docker not found. Skipping.' }
    }
    'Smoke' { 
        Write-Host "`n> python -c `"import torch, gymnasium as gym, numpy, pyarrow; print('SMOKE OK')`"" -ForegroundColor Cyan
        python -c "import torch, gymnasium as gym, numpy, pyarrow; print('SMOKE OK')"
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
            throw "Command failed with exit code $LASTEXITCODE"
        }
    }
    'Clean' {
        Write-Host 'Cleaning caches and compiled artifacts...'
        Get-ChildItem -Recurse -Force -Include __pycache__ -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -Recurse -Force -Include *.pyc,*.pyo -File | Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

