param(
    [string]$AppDir = "$PSScriptRoot\dist\DimensionsAutoEvaluate"
)

$ErrorActionPreference = "Stop"
$exe = Join-Path $AppDir "DimensionsAutoEvaluate.exe"
if (-not (Test-Path $exe)) { throw "Packaged executable not found: $exe" }
if (-not (Test-Path (Join-Path $AppDir "_internal\Frontend\client\dist\index.html"))) { throw "Bundled React assets missing." }
if (-not (Test-Path (Join-Path $AppDir "_internal\browsers"))) { throw "Bundled Playwright Chromium missing." }
if (-not (Test-Path (Join-Path $AppDir "_internal\docs\PRIVACY.md"))) { throw "Bundled privacy documentation missing." }

$tempRoot = Join-Path $env:TEMP "DimensionsAutoEvaluate-release-test"
Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $tempRoot | Out-Null
$env:DIMENSIONS_PRODUCTION_MODE = "1"
$env:DIMENSIONS_APP_DATA_ROOT = $tempRoot

$stdout = Join-Path $tempRoot "server.stdout.log"
$stderr = Join-Path $tempRoot "server.stderr.log"
$process = Start-Process -FilePath $exe -ArgumentList @("--server", "--no-browser", "--port", "18765") `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
try {
    $healthy = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) {
            $details = @(Get-Content $stdout, $stderr -ErrorAction SilentlyContinue) -join [Environment]::NewLine
            throw "Packaged server exited with code $($process.ExitCode).`n$details"
        }
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:18765/api/health" -TimeoutSec 1
            if ($health.ok -and $health.production_mode) { $healthy = $true; break }
        } catch { }
    }
    if (-not $healthy) {
        $details = @(Get-Content $stdout, $stderr -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        throw "Packaged server did not become healthy.`n$details"
    }
    $page = Invoke-WebRequest "http://127.0.0.1:18765" -UseBasicParsing
    if ($page.StatusCode -ne 200 -or $page.Content -notmatch '<div id="root">') { throw "Packaged React UI failed smoke test." }
    if ($page.Headers["X-Frame-Options"] -ne "DENY" -or $page.Headers["Content-Security-Policy"] -notmatch "frame-ancestors 'none'") {
        throw "Packaged security headers missing."
    }
    $shutdown = Invoke-RestMethod "http://127.0.0.1:18765/api/shutdown" -Method Post -TimeoutSec 5
    if (-not $shutdown.shutting_down) { throw "Packaged graceful shutdown failed." }
    if (-not $process.WaitForExit(10000)) { throw "Packaged process did not exit after shutdown." }

    $workerInfo = New-Object System.Diagnostics.ProcessStartInfo
    $workerInfo.FileName = $exe
    $workerInfo.Arguments = "--worker pipeline --help"
    $workerInfo.WorkingDirectory = $AppDir
    $workerInfo.UseShellExecute = $true
    $workerProcess = [System.Diagnostics.Process]::Start($workerInfo)
    if (-not $workerProcess.WaitForExit(30000)) {
        taskkill.exe /PID $workerProcess.Id /T /F 2>$null | Out-Null
        throw "Unredirected frozen worker did not exit."
    }
    if ($workerProcess.ExitCode -ne 0) { throw "Unredirected frozen worker exited with code $($workerProcess.ExitCode)." }

    $windowedInfo = New-Object System.Diagnostics.ProcessStartInfo
    $windowedInfo.FileName = $exe
    $windowedInfo.Arguments = "--server --no-browser --port 18766"
    $windowedInfo.WorkingDirectory = $AppDir
    $windowedInfo.UseShellExecute = $true
    $windowedProcess = [System.Diagnostics.Process]::Start($windowedInfo)
    try {
        $windowedHealthy = $false
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Milliseconds 500
            if ($windowedProcess.HasExited) { throw "Unredirected packaged server exited with code $($windowedProcess.ExitCode)." }
            try {
                $windowedHealth = Invoke-RestMethod "http://127.0.0.1:18766/api/health" -TimeoutSec 1
                if ($windowedHealth.ok -and $windowedHealth.production_mode) { $windowedHealthy = $true; break }
            } catch { }
        }
        if (-not $windowedHealthy) { throw "Unredirected packaged server did not become healthy." }
        $windowedShutdown = Invoke-RestMethod "http://127.0.0.1:18766/api/shutdown" -Method Post -TimeoutSec 5
        if (-not $windowedShutdown.shutting_down) { throw "Unredirected packaged server graceful shutdown failed." }
        if (-not $windowedProcess.WaitForExit(10000)) { throw "Unredirected packaged server did not exit after shutdown." }
    } finally {
        if (-not $windowedProcess.HasExited) { taskkill.exe /PID $windowedProcess.Id /T /F 2>$null | Out-Null }
    }
    "Packaged release smoke test passed."
} finally {
    if (-not $process.HasExited) { taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null }
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
