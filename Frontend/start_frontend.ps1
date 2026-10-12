param(
    [switch]$HiddenLaunch
)

$ErrorActionPreference = "Stop"
$frontend = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $frontend "server"
$client = Join-Path $frontend "client"
$venv = Join-Path $server ".venv"
$python = Join-Path $venv "Scripts\python.exe"
$appData = Join-Path $env:LOCALAPPDATA "DimensionsAutoEvaluate"
$logRoot = Join-Path $appData "logs"
$runtimeRoot = Join-Path $appData "runtime"
$pidFile = Join-Path $runtimeRoot "frontend.json"
$launcherLog = Join-Path $logRoot "launcher.log"
$apiOut = Join-Path $logRoot "api.log"
$apiErr = Join-Path $logRoot "api-error.log"
$uiOut = Join-Path $logRoot "ui.log"
$uiErr = Join-Path $logRoot "ui-error.log"
$appUrl = "http://127.0.0.1:5173"
$healthUrl = "http://127.0.0.1:8000/api/health"

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

function Write-LauncherLog([string]$message) {
    $line = "$(Get-Date -Format o) $message"
    Add-Content -LiteralPath $launcherLog -Value $line -Encoding UTF8
    if (-not $HiddenLaunch) { $line }
}

function Test-AppHealthy {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
        if (-not $response.ok -or $response.frontend_api_version -ne 4) { return $false }
        foreach ($name in @("extract", "evaluate", "report")) {
            if (-not $response.dependencies.$name.ready) { return $false }
        }
        return $true
    } catch {
        return $false
    }
}

function Test-AnyApi {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
        return [bool]$response.ok
    } catch {
        return $false
    }
}

function Show-StartupError([string]$message) {
    Write-LauncherLog "ERROR: $message"
    if ($HiddenLaunch) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            "$message`n`nLauncher log:`n$launcherLog",
            "Dimensions Auto Evaluate",
            "OK",
            "Error"
        ) | Out-Null
    } else {
        Write-Error $message
    }
}

if (Test-AppHealthy) {
    Write-LauncherLog "Existing app detected; opening browser."
    Start-Process $appUrl
    exit 0
}

if (Test-AnyApi) {
    Write-LauncherLog "Outdated or incomplete app instance detected; stopping it before restart."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $frontend "stop_frontend.ps1")
    Start-Sleep -Seconds 2
    if (Test-AnyApi) {
        Show-StartupError "An older app instance is using port 8000 and could not be stopped. Use the Stop desktop shortcut, then retry."
        exit 1
    }
}

$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\DimensionsAutoEvaluateFrontend", [ref]$created)
if (-not $created) {
    Write-LauncherLog "Another launcher is starting the app."
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-AppHealthy) { Start-Process $appUrl; exit 0 }
    }
    Show-StartupError "Another launcher is active, but the app did not become healthy."
    exit 1
}

$api = $null
$ui = $null
try {
    Write-LauncherLog "Starting application setup."
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python is not installed or not available on PATH."
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm is not installed or not available on PATH."
    }

    if (-not (Test-Path -LiteralPath $python)) {
        Write-LauncherLog "Creating Python virtual environment."
        python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create Python virtual environment." }
    }

    Write-LauncherLog "Checking Python dependencies."
    & $python -m pip install -r (Join-Path $server "requirements.txt") --quiet
    if ($LASTEXITCODE -ne 0) { throw "Could not install Python dependencies." }

    Write-LauncherLog "Checking Playwright Chromium."
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Could not install Playwright Chromium for MVP 1." }

    & $python -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Jakarta'); print('Timezone ready')"
    if ($LASTEXITCODE -ne 0) { throw "Asia/Jakarta timezone data is unavailable. Could not prepare MVP 1." }

    if (-not (Test-Path -LiteralPath (Join-Path $client "node_modules"))) {
        Write-LauncherLog "Installing npm dependencies."
        & npm install --prefix $client
        if ($LASTEXITCODE -ne 0) { throw "Could not install npm dependencies." }
    }

    Remove-Item -LiteralPath $apiOut, $apiErr, $uiOut, $uiErr -Force -ErrorAction SilentlyContinue
    $api = Start-Process -FilePath $python -ArgumentList @(
        "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"
    ) -WorkingDirectory $server -WindowStyle Hidden -RedirectStandardOutput $apiOut `
      -RedirectStandardError $apiErr -PassThru

    $ui = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev") `
      -WorkingDirectory $client -WindowStyle Hidden -RedirectStandardOutput $uiOut `
      -RedirectStandardError $uiErr -PassThru

    @{
        host_pid = $PID
        api_pid = $api.Id
        ui_pid = $ui.Id
        started_at = (Get-Date).ToUniversalTime().ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8

    $healthy = $false
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-AppHealthy) { $healthy = $true; break }
        if ($api.HasExited -or $ui.HasExited) { break }
    }
    if (-not $healthy) {
        throw "Application did not become healthy. Check api-error.log and ui-error.log."
    }

    Write-LauncherLog "Application healthy. API PID=$($api.Id), UI PID=$($ui.Id)."
    Start-Process $appUrl
    if (-not $HiddenLaunch) {
        "Dimensions Auto Evaluate is running."
        "UI:  $appUrl"
        "API: http://127.0.0.1:8000"
        "Press Ctrl+C to stop both processes."
    }

    while (-not $api.HasExited -and -not $ui.HasExited) {
        Start-Sleep -Seconds 1
    }
    if ($api.HasExited) { Write-LauncherLog "API process exited with code $($api.ExitCode)." }
    if ($ui.HasExited) { Write-LauncherLog "UI process exited with code $($ui.ExitCode)." }
} catch {
    Show-StartupError $_.Exception.Message
    exit 1
} finally {
    foreach ($process in @($ui, $api)) {
        if ($process -and -not $process.HasExited) {
            & taskkill /PID $process.Id /T /F 2>$null | Out-Null
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    if ($mutex) { $mutex.ReleaseMutex(); $mutex.Dispose() }
    Write-LauncherLog "Launcher stopped."
}
