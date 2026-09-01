# ============================================================================
# Trading Gym - Stop GUI Services (Windows PowerShell)
# ============================================================================
#
# This script gracefully stops all Trading Gym services running on Windows.
#
# USAGE:
#   powershell -ExecutionPolicy Bypass -File scripts/stop-gui.ps1
#
# ============================================================================

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Magenta
Write-Host "Stopping Trading Gym GUI Services" -ForegroundColor Magenta
Write-Host "======================================================================" -ForegroundColor Magenta
Write-Host ""

$stopped = @()
$notRunning = @()

# Function to get process command line using WMI/CIM
function Get-ProcessCommandLine {
    param([int]$ProcessId)
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
        if ($process) {
            return $process.CommandLine
        }
    }
    catch {
        # Fallback to WMI if CIM is not available
        try {
            $process = Get-WmiObject Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
            if ($process) {
                return $process.CommandLine
            }
        }
        catch {
            return $null
        }
    }
    return $null
}

# Function to stop process by name pattern
function Stop-ProcessByPattern {
    param(
        [string]$Pattern,
        [string]$DisplayName
    )

    $processes = @()

    # Get processes matching the pattern (more specific matching)
    try {
        if ($Pattern -eq "uvicorn") {
            # Match uvicorn processes running apps.api.main
            $allPythonProcs = Get-Process python -ErrorAction SilentlyContinue
            $processes = @()
            foreach ($proc in $allPythonProcs) {
                $cmdLine = Get-ProcessCommandLine -ProcessId $proc.Id
                if ($cmdLine -and ($cmdLine -match "uvicorn" -or $cmdLine -match "apps\.api\.main")) {
                    $processes += $proc
                }
            }
        }
        elseif ($Pattern -eq "npm-dev") {
            # Match npm run dev / vite processes
            $allNodeProcs = Get-Process node,npm -ErrorAction SilentlyContinue
            $processes = @()
            foreach ($proc in $allNodeProcs) {
                $cmdLine = Get-ProcessCommandLine -ProcessId $proc.Id
                if ($cmdLine -and $cmdLine -match "run\s+dev") {
                    $processes += $proc
                }
            }
        }
        else {
            # Generic pattern matching for Python processes
            $allPythonProcs = Get-Process python -ErrorAction SilentlyContinue
            $processes = @()
            foreach ($proc in $allPythonProcs) {
                $cmdLine = Get-ProcessCommandLine -ProcessId $proc.Id
                if ($cmdLine -and $cmdLine -match $Pattern) {
                    $processes += $proc
                }
            }
        }
    }
    catch {
        $warningMsg = '  Warning: Error while searching for processes: ' + $_.Exception.Message
        Write-Host $warningMsg -ForegroundColor Yellow
        return
    }

    if ($processes) {
        Write-Host "Stopping $DisplayName..." -ForegroundColor Yellow
        try {
            foreach ($proc in $processes) {
                Write-Host "  Terminating PID $($proc.Id)..." -ForegroundColor Cyan
                Stop-Process -InputObject $proc -Force -ErrorAction SilentlyContinue
                $script:stopped += $DisplayName
            }
            $msg = '  [OK] ' + $DisplayName + ' stopped'
            Write-Host $msg -ForegroundColor Green
        }
        catch {
            $errorMsg = "Could not stop $DisplayName : $($_.Exception.Message)"
            Write-Host "  Warning: $errorMsg" -ForegroundColor Yellow
        }
    }
    else {
        $script:notRunning += $DisplayName
    }
}

# Stop API server (uvicorn process running apps.api.main)
Stop-ProcessByPattern -Pattern "uvicorn" -DisplayName "API Server"

# Stop frontend (npm run dev / vite)
Stop-ProcessByPattern -Pattern "npm-dev" -DisplayName "Frontend Server"

# Stop any training processes
Stop-ProcessByPattern -Pattern "apps\.trainer\.main" -DisplayName "Training Process"

# Stop any trading processes
Stop-ProcessByPattern -Pattern "apps\.(paper|live)\.main" -DisplayName "Trading Process"

# Summary
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Magenta
Write-Host "Shutdown Summary" -ForegroundColor Magenta
Write-Host "======================================================================" -ForegroundColor Magenta
Write-Host ""

if ($stopped.Count -gt 0) {
    Write-Host '[OK] Stopped services:' -ForegroundColor Green
    foreach ($service in $stopped) {
        Write-Host "  - $service" -ForegroundColor Green
    }
}
else {
    Write-Host "No services were running" -ForegroundColor Yellow
}

if ($notRunning.Count -gt 0) {
    Write-Host ""
    Write-Host "i Services not running:" -ForegroundColor Cyan
    foreach ($service in $notRunning) {
        Write-Host "  - $service" -ForegroundColor Cyan
    }
}

Write-Host ""
Write-Host '[OK] All services stopped' -ForegroundColor Green
Write-Host ""
