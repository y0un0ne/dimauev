param(
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$buildVenv = Join-Path $PSScriptRoot ".build-venv"
$python = Join-Path $buildVenv "Scripts\python.exe"
$browserRoot = Join-Path $PSScriptRoot ".cache\ms-playwright"
$distRoot = Join-Path $PSScriptRoot "dist"
$workRoot = Join-Path $PSScriptRoot "work"
$releaseRoot = Join-Path $root "release"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.12 is required on the build machine." }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node.js/npm is required on the build machine." }

if (-not (Test-Path $python)) { python -m venv $buildVenv }
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE" }
& $python -m pip install -r (Join-Path $PSScriptRoot "requirements-runtime.lock")
if ($LASTEXITCODE -ne 0) { throw "runtime dependency install failed with exit code $LASTEXITCODE" }
& $python -m pip install -r (Join-Path $PSScriptRoot "requirements-build.lock")
if ($LASTEXITCODE -ne 0) { throw "build dependency install failed with exit code $LASTEXITCODE" }

if (-not $SkipTests) {
    $tests = @(
        "test_extraction_status.py",
        "test_evaluation_status.py",
        "test_pipeline_core.py",
        "test_dimensions_pipeline.py",
        "test_extractor_control.py",
        "test_mvp3_reports.py",
        "test_production_runtime.py",
        "Frontend\server\tests\test_server.py"
    )
    foreach ($test in $tests) {
        & $python (Join-Path $root $test)
        if ($LASTEXITCODE -ne 0) { throw "$test failed with exit code $LASTEXITCODE" }
    }
}

$frontendSrc = Join-Path $root "Frontend\client"
$frontendBuild = Join-Path $PSScriptRoot ".frontend-build"
Remove-Item -LiteralPath $frontendBuild -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $frontendBuild -Force | Out-Null
$exclude = @('node_modules', 'dist', '.git')
Get-ChildItem -LiteralPath $frontendSrc -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $frontendBuild -Recurse -Force
}
Remove-Item -LiteralPath (Join-Path $frontendBuild ".gitignore") -Force -ErrorAction SilentlyContinue
Push-Location $frontendBuild
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE" }
    $lintFiles = @(Get-ChildItem (Join-Path $frontendBuild "src") -Recurse -File -Include "*.ts", "*.tsx" | ForEach-Object FullName)
    & (Join-Path $frontendBuild "node_modules\.bin\oxlint.cmd") $lintFiles
    if ($LASTEXITCODE -ne 0) { throw "oxlint failed with exit code $LASTEXITCODE" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "npm build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Path $browserRoot -Force | Out-Null
$env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot
& $python -m playwright install chromium --no-shell
if ($LASTEXITCODE -ne 0) { throw "Playwright browser install failed with exit code $LASTEXITCODE" }

Remove-Item -LiteralPath $distRoot, $workRoot -Recurse -Force -ErrorAction SilentlyContinue
& $python -m PyInstaller --noconfirm --clean `
    --distpath $distRoot --workpath $workRoot `
    (Join-Path $PSScriptRoot "DimensionsAutoEvaluate.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$exe = Join-Path $distRoot "DimensionsAutoEvaluate\DimensionsAutoEvaluate.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller output missing: $exe" }

New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
$portable = Join-Path $releaseRoot "DimensionsAutoEvaluate-portable-win-x64.zip"
Remove-Item -LiteralPath $portable -Force -ErrorAction SilentlyContinue
& tar.exe -a -c -f $portable -C (Join-Path $distRoot "DimensionsAutoEvaluate") .
if ($LASTEXITCODE -ne 0) { throw "Portable ZIP creation failed with exit code $LASTEXITCODE" }

if (-not $SkipInstaller) {
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) { $iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe" }
    if (-not (Test-Path $iscc)) { $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
    if (-not (Test-Path $iscc)) {
        throw "Inno Setup 6 is not installed. Install it, then rerun or use -SkipInstaller. Portable ZIP was created."
    }
    & $iscc (Join-Path $PSScriptRoot "installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }
}

& (Join-Path $PSScriptRoot "verify_release.ps1")
if ($LASTEXITCODE -ne 0) { throw "Packaged smoke test failed with exit code $LASTEXITCODE" }

$checksumFile = Join-Path $releaseRoot "SHA256SUMS.txt"
Remove-Item -LiteralPath $checksumFile -Force -ErrorAction SilentlyContinue
Get-ChildItem $releaseRoot -File | Sort-Object Name | ForEach-Object {
    $hash = (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant()
    "$hash  $($_.Name)"
} | Set-Content -LiteralPath $checksumFile -Encoding ASCII
Get-Content $checksumFile
