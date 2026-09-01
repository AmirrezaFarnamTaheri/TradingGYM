# ============================================================================
# Trading Gym - Start GUI Services (Windows PowerShell)
# ============================================================================
#
# This script starts the Trading Gym API and Frontend servers.
# It assumes dependencies are already installed.
#
# For first-time setup, use: powershell -ExecutionPolicy Bypass -File setup-windows.ps1
#
# USAGE:
#   powershell -ExecutionPolicy Bypass -File scripts/start-gui.ps1
#
# ============================================================================

param(
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 3000,
    [switch]$SkipBrowser = $false,
    [switch]$DryRun = $false
)

$ErrorActionPreference = 'Stop'

# Colors for output
$colors = @{
    Success = 'Green'
    Error = 'Red'
    Warning = 'Yellow'
    Info = 'Cyan'
    Header = 'Magenta'
}

function Write-Header {
    param([string]$Message)
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor $colors.Header
    Write-Host $Message -ForegroundColor $colors.Header
    Write-Host ("=" * 70) -ForegroundColor $colors.Header
    Write-Host ""
}

function Write-Success {
    param([string]$Message)
    Write-Host ('✓ ' + $Message) -ForegroundColor $colors.Success
}

function Write-Info {
    param(
        [string]$Message,
        [string]$ForegroundColor = $colors.Info
    )
    Write-Host ('i ' + $Message) -ForegroundColor $ForegroundColor
}

function Write-Warning {
    param([string]$Message)
    Write-Host ('⚠ ' + $Message) -ForegroundColor $colors.Warning
}

function Write-Error {
    param([string]$Message)
    Write-Host ('✗ ' + $Message) -ForegroundColor $colors.Error
}

# ============================================================================
# VERIFY SETUP
# ============================================================================

Write-Header 'Trading Gym GUI - Startup'

# Check if we're in the project root
if (-not (Test-Path 'pyproject.toml')) {
    $errorMsg = 'pyproject.toml not found. Please run this script from the project root directory.'
    Write-Error $errorMsg
    exit 1
}

Write-Info 'Project root verified'

# Check if .env exists
if (-not (Test-Path '.env')) {
    Write-Warning '.env file not found. Using default API configuration.'
    Write-Warning 'For production use, configure .env with your API keys.'
}
else {
    Write-Info '.env found (configured)'
}

# Check if frontend/.env exists
if (-not (Test-Path 'frontend\.env')) {
    Write-Warning 'frontend/.env not found. Creating with defaults...'
    if ($DryRun) {
        Write-Info '[DRY-RUN] Would generate frontend/.env'
    }
    else {
        $viteApiUrl = 'http://localhost:' + $ApiPort
        $viteWsUrl = 'ws://localhost:' + $ApiPort
        $newline = [Environment]::NewLine
        $frontendEnvContent = 'VITE_API_URL=' + $viteApiUrl + $newline + 'VITE_WS_URL=' + $viteWsUrl
        $frontendEnvContent | Set-Content 'frontend\.env'
    }
}

# Determine Python command
$pythonCmd = 'python'
$pythonAvailable = $false
try {
    & $pythonCmd --version 2>&1 | Out-Null
    $pythonAvailable = $true
}
catch {
    $pythonCmd = 'python3'
    try {
        & $pythonCmd --version 2>&1 | Out-Null
        $pythonAvailable = $true
    }
    catch {
        $pythonAvailable = $false
    }
}

if (-not $pythonAvailable) {
    $errorMsg = 'Python executable not found in PATH. Please install Python 3.12+ and ensure python is available, then re-run this script.'
    Write-Error $errorMsg
    exit 1
}

# ============================================================================
# PORT AVAILABILITY CHECK
# ============================================================================

# Function to test if a port is free
function Test-PortFree {
    param([int]$Port)
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $listener.Stop()
        return $true
    }
    catch {
        return $false
    }
}

function Get-FreePort {
    param(
        [int]$PreferredPort,
        [int]$MaxAttempts = 1000
    )

    $port = $PreferredPort
    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt++) {
        if (Test-PortFree -Port $port) {
            return $port
        }
        $port++
    }

    return $null
}

Write-Info 'Checking port availability...'

$apiPortWasExplicit = $PSBoundParameters.ContainsKey('ApiPort')
if (-not (Test-PortFree -Port $ApiPort)) {
    if (-not $apiPortWasExplicit) {
        $newApiPort = Get-FreePort -PreferredPort ($ApiPort + 1)
        if ($null -eq $newApiPort) {
            $errorMsg = 'Unable to find a free API port near ' + $ApiPort + '.'
            Write-Error $errorMsg
            $errorMsg = 'Specify a port manually with: -ApiPort <port_number>'
            Write-Error $errorMsg
            exit 1
        }

        Write-Warning ('API port {0} is busy. Automatically switching to {1}.' -f $ApiPort, $newApiPort)
        $ApiPort = $newApiPort
    }
    else {
        $errorMsg = 'API port ' + $ApiPort + ' is already in use.'
        Write-Error $errorMsg
        $errorMsg = 'Choose a different port with: -ApiPort <port_number>'
        Write-Error $errorMsg
        exit 1
    }
}
$successMsg = 'API port ' + $ApiPort + ' is available'
Write-Success $successMsg

$frontendPortWasExplicit = $PSBoundParameters.ContainsKey('FrontendPort')
if (-not (Test-PortFree -Port $FrontendPort)) {
    if (-not $frontendPortWasExplicit) {
        $newFrontendPort = Get-FreePort -PreferredPort ($FrontendPort + 1)
        if ($null -eq $newFrontendPort) {
            $errorMsg = 'Unable to find a free frontend port near ' + $FrontendPort + '.'
            Write-Error $errorMsg
            $errorMsg = 'Specify a port manually with: -FrontendPort <port_number>'
            Write-Error $errorMsg
            exit 1
        }

        Write-Warning ('Frontend port {0} is busy. Automatically switching to {1}.' -f $FrontendPort, $newFrontendPort)
        $FrontendPort = $newFrontendPort
    }
    else {
        $errorMsg = 'Frontend port ' + $FrontendPort + ' is already in use.'
        Write-Error $errorMsg
        $errorMsg = 'Choose a different port with: -FrontendPort <port_number>'
        Write-Error $errorMsg
        exit 1
    }
}
$successMsg = 'Frontend port ' + $FrontendPort + ' is available'
Write-Success $successMsg

# ============================================================================
# START SERVICES
# ============================================================================

if ($DryRun) {
    Write-Header 'Dry-Run Summary'
    Write-Info 'Validation completed. No services were started.'
    Write-Info 'Run again without -DryRun to launch the API and frontend.'
    return
}

Write-Header 'Starting Trading Gym Services'

$global:processes = @()

# Create logs directory if it doesn't exist
$logsDir = 'logs'
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}

# Set up log file paths
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$apiLogOut = Join-Path $logsDir ('api_stdout_' + $timestamp + '.log')
$apiLogErr = Join-Path $logsDir ('api_stderr_' + $timestamp + '.log')

$logFilesMsg = 'Log files: ' + $apiLogOut + ', ' + $apiLogErr
Write-Info $logFilesMsg

# Start API Server
$apiStartMsg = 'Starting API server on http://localhost:' + $ApiPort + '...'
Write-Info $apiStartMsg
$apiProcess = Start-Process -FilePath $pythonCmd `
    -ArgumentList @('-m', 'uvicorn', 'apps.api.main:app', '--host', '0.0.0.0', '--port', $ApiPort) `
    -RedirectStandardOutput $apiLogOut `
    -RedirectStandardError $apiLogErr `
    -PassThru `
    -NoNewWindow

$global:processes += @{
    Name = 'API Server'
    Process = $apiProcess
}

$apiSuccessMsg = 'API server started (PID: ' + $apiProcess.Id + ')'
Write-Success $apiSuccessMsg

# Wait for API to be ready
Write-Info 'Waiting for API server to be ready...'
$apiReady = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $healthUrl = 'http://localhost:' + $ApiPort + '/health'
        $response = Invoke-WebRequest -Uri $healthUrl -Method Get -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Success 'API server is ready!'
            $apiReady = $true
            break
        }
    }
    catch {
        # API not ready yet
    }

    if ($i -eq 30) {
        $errorMsg = 'API server failed to start. Check configuration and try again.'
        Write-Error $errorMsg
        exit 1
    }

    $checkMsg = '  Checking... (attempt ' + $i + '/30)'
    Write-Info $checkMsg
    Start-Sleep -Seconds 1
}

# Start Frontend Server
$frontendStartMsg = 'Starting frontend development server on http://localhost:' + $FrontendPort + '...'
Write-Info $frontendStartMsg
$frontendProcess = Start-Process -FilePath 'npm' `
    -ArgumentList @('run', 'dev', '--', '--port', $FrontendPort) `
    -WorkingDirectory 'frontend' `
    -PassThru `
    -NoNewWindow

$global:processes += @{
    Name = 'Frontend Server'
    Process = $frontendProcess
}

$frontendSuccessMsg = 'Frontend server started (PID: ' + $frontendProcess.Id + ')'
Write-Success $frontendSuccessMsg

# Open browser
if (-not $SkipBrowser) {
    Write-Header 'Opening GUI in Browser'
    $openBrowserMsg = 'Opening http://localhost:' + $FrontendPort + '...'
    Write-Info $openBrowserMsg
    Start-Sleep -Seconds 2
    try {
        $browserUrl = 'http://localhost:' + $FrontendPort
        Start-Process $browserUrl
    }
    catch {
        $warningMsg = 'Could not open browser automatically. Please navigate to http://localhost:' + $FrontendPort
        Write-Warning $warningMsg
    }
}

# ============================================================================
# SUCCESS MESSAGE
# ============================================================================

Write-Header 'Trading Gym is Running!'

Write-Info ""
Write-Success 'All services started successfully!'
Write-Info ""
Write-Info 'Service Endpoints:' -ForegroundColor $colors.Info
$frontendUrl = '  Frontend (GUI):  http://localhost:' + $FrontendPort
Write-Info $frontendUrl -ForegroundColor White
$apiServerUrl = '  API Server:      http://localhost:' + $ApiPort
Write-Info $apiServerUrl -ForegroundColor White
$apiDocsUrl = '  API Docs:        http://localhost:' + $ApiPort + '/docs'
Write-Info $apiDocsUrl -ForegroundColor White
Write-Info ""
Write-Info 'Running Processes:' -ForegroundColor $colors.Info
foreach ($proc in $global:processes) {
    $procName = $proc.Name
    $procId = $proc.Process.Id
    $procMsg = '  ' + $procName + ' (PID: ' + $procId + ')'
    Write-Info $procMsg -ForegroundColor White
}
Write-Info ""
Write-Info 'Press Ctrl+C to stop all services' -ForegroundColor $colors.Warning
Write-Info ""

# Keep running
try {
    while ($true) {
        foreach ($proc in $global:processes) {
            if ($proc.Process.HasExited) {
                $procName = $proc.Name
                $errorMsg = $procName + ' exited unexpectedly!'
                Write-Error $errorMsg
                exit 1
            }
        }
        Start-Sleep -Seconds 5
    }
}
catch {
    Write-Header 'Shutdown Initiated'
    foreach ($proc in $global:processes) {
        try {
            if (-not $proc.Process.HasExited) {
                $procName = $proc.Name
                $procId = $proc.Process.Id
                $stopMsg = 'Stopping ' + $procName + ' (PID: ' + $procId + ')'
                Write-Info $stopMsg
                $proc.Process.CloseMainWindow()
                Start-Sleep -Milliseconds 2000
                if (-not $proc.Process.HasExited) {
                    $proc.Process.Kill()
                }
            }
        }
        catch {
            $procName = $proc.Name
            $warningMsg = 'Error stopping ' + $procName + ' : ' + $_.Exception.Message
            Write-Warning $warningMsg
        }
    }
    Write-Success 'All services stopped'
    exit 0
}
