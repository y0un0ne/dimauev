$ErrorActionPreference = "SilentlyContinue"
$runtimeRoot = Join-Path $env:LOCALAPPDATA "DimensionsAutoEvaluate\runtime"
$pidFile = Join-Path $runtimeRoot "frontend.json"

if (-not (Test-Path -LiteralPath $pidFile)) {
    exit 0
}

try {
    $runtime = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    foreach ($processId in @($runtime.ui_pid, $runtime.api_pid)) {
        if ($processId) {
            & taskkill /PID ([int]$processId) /T /F 2>$null | Out-Null
        }
    }
    if ($runtime.host_pid -and [int]$runtime.host_pid -ne $PID) {
        & taskkill /PID ([int]$runtime.host_pid) /T /F 2>$null | Out-Null
    }
} finally {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
