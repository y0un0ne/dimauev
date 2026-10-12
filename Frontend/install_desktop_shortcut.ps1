$ErrorActionPreference = "Stop"

$frontend = Split-Path -Parent $MyInvocation.MyCommand.Path
$launchVbs = Join-Path $frontend "Launch Dimensions Auto Evaluate.vbs"
$stopVbs = Join-Path $frontend "Stop Dimensions Auto Evaluate.vbs"
$desktop = [Environment]::GetFolderPath("Desktop")
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"

if (-not (Test-Path -LiteralPath $launchVbs)) {
    throw "Launcher not found: $launchVbs"
}

$shell = New-Object -ComObject WScript.Shell

$launchShortcut = $shell.CreateShortcut((Join-Path $desktop "Dimensions Auto Evaluate.lnk"))
$launchShortcut.TargetPath = $wscript
$launchShortcut.Arguments = "`"$launchVbs`""
$launchShortcut.WorkingDirectory = $frontend
$launchShortcut.Description = "Open Dimensions Auto Evaluate"
$launchShortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,15"
$launchShortcut.Save()

$stopShortcut = $shell.CreateShortcut((Join-Path $desktop "Stop Dimensions Auto Evaluate.lnk"))
$stopShortcut.TargetPath = $wscript
$stopShortcut.Arguments = "`"$stopVbs`""
$stopShortcut.WorkingDirectory = $frontend
$stopShortcut.Description = "Stop Dimensions Auto Evaluate local servers"
$stopShortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,93"
$stopShortcut.Save()

"Desktop shortcuts installed:"
"  $(Join-Path $desktop 'Dimensions Auto Evaluate.lnk')"
"  $(Join-Path $desktop 'Stop Dimensions Auto Evaluate.lnk')"
