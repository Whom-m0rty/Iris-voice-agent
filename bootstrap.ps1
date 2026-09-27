# One-line install, no git and no keys needed:
#   irm https://raw.githubusercontent.com/Whom-m0rty/Iris-voice-agent/main/bootstrap.ps1 | iex
# With your own Claude instead of the default cloud brain:
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Whom-m0rty/Iris-voice-agent/main/bootstrap.ps1))) -Brain claude-code
param([string]$Brain = "cloud", [switch]$Local)
$ErrorActionPreference = "Stop"
$home_ = Join-Path $env:LOCALAPPDATA "Iris"
$app = Join-Path $home_ "app"
$zip = Join-Path $env:TEMP "iris-main.zip"
Write-Host "Downloading Iris..." -ForegroundColor Cyan
Invoke-WebRequest "https://github.com/Whom-m0rty/Iris-voice-agent/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
$tmp = Join-Path $env:TEMP "iris-unpack"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
Expand-Archive $zip $tmp
New-Item -ItemType Directory -Force $app | Out-Null
# keep the user's settings and lists across updates
Copy-Item "$tmp\Iris-voice-agent-main\*" $app -Recurse -Force -Exclude ".env", "protected_apps.txt"
Remove-Item $tmp -Recurse -Force; Remove-Item $zip -Force
$args_ = @("-ExecutionPolicy", "Bypass", "-File", "$app\install.ps1", "-Brain", $Brain)
if ($Local) { $args_ += "-Local" }
& powershell @args_
