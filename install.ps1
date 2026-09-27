# Iris installer for Windows 10/11.
#   powershell -ExecutionPolicy Bypass -File install.ps1                      no keys, no GPU (Iris Cloud)
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Brain claude-code   your Claude Code login
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Brain anthropic     your Anthropic API key
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Local               + Kev on your NVIDIA GPU
# Safe to run again: every step checks what is already there.
param(
    [ValidateSet("cloud", "claude-code", "anthropic", "gateway")] [string]$Brain = "cloud",
    [switch]$Local,
    [switch]$NoShortcut
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`n$text" -ForegroundColor Red; exit 1 }

Step "Checking the basics"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python package manager)..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "uv did not install; see https://docs.astral.sh/uv/" }
}

if ($Local) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git is missing: https://git-scm.com/download/win" }
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        Fail "No NVIDIA driver found. Local Kev needs an NVIDIA GPU with ~10 GB free; without -Local, Iris uses Jev in the cloud."
    }
    Write-Host "GPU: $((nvidia-smi --query-gpu=name,memory.total --format=csv,noheader) | Select-Object -First 1)"

    Step "Getting Kev (open-weights decision model)"
    if (-not (Test-Path "$root\kev\pyproject.toml")) { git clone https://github.com/jaredpalmer/kev.git "$root\kev" }
    Set-Location "$root\kev"
    uv sync --extra serve --python 3.13
    $py = "$root\kev\.venv\Scripts\python.exe"

    Step "Installing PyTorch with CUDA (the default one from PyPI is CPU-only on Windows)"
    $cuda = & $py -c "import torch; print(torch.cuda.is_available())" 2>$null
    if ($cuda -ne "True") {
        uv pip install --python $py "torch==2.8.0" --index-url https://download.pytorch.org/whl/cu128 --reinstall-package torch
        $cuda = & $py -c "import torch; print(torch.cuda.is_available())"
        if ($cuda -ne "True") { Fail "PyTorch still cannot see the GPU. Update the NVIDIA driver and run install.ps1 again." }
    }
    Set-Location $root
} else {
    Step "Creating Iris's Python environment"
    if (-not (Test-Path "$root\.venv\Scripts\python.exe")) { uv venv "$root\.venv" --python 3.13 }
    $py = "$root\.venv\Scripts\python.exe"
}

Step "Installing Iris"
uv pip install --python $py -r "$root\requirements-iris.txt"

Step "Settings (.env next to this script; never committed)"
$envFile = "$root\.env"
if (-not (Test-Path $envFile)) { New-Item -ItemType File $envFile | Out-Null }
$current = Get-Content $envFile -ErrorAction SilentlyContinue
function Has($name) { return [bool]($current | Where-Object { $_ -like "$name=*" }) }
function Put($name, $value) { Add-Content -Path $envFile -Value "$name=$value" -Encoding ascii }

if (-not (Has "IRIS_BRAIN")) { Put "IRIS_BRAIN" $Brain }
if ($Local -and -not (Has "IRIS_DECISIONS")) { Put "IRIS_DECISIONS" "kev" }
if ($Brain -eq "anthropic" -and -not (Has "ANTHROPIC_API_KEY")) {
    $k = Read-Host "ANTHROPIC_API_KEY"; if ($k) { Put "ANTHROPIC_API_KEY" $k.Trim() }
}
if ($Brain -eq "claude-code" -and -not (Has "CLAUDE_CODE_OAUTH_TOKEN")) {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host "Claude Code is not installed: https://claude.com/claude-code (then run install.ps1 again)" -ForegroundColor Yellow
    }
    $k = Read-Host "CLAUDE_CODE_OAUTH_TOKEN (from 'claude setup-token'; Enter to use your existing login)"
    if ($k) { Put "CLAUDE_CODE_OAUTH_TOKEN" $k.Trim() }
}
if ($Brain -eq "gateway" -and -not (Has "ASSEMBLYAI_API_KEY")) {
    $k = Read-Host "ASSEMBLYAI_API_KEY"; if ($k) { Put "ASSEMBLYAI_API_KEY" $k.Trim() }
}
if (-not (Test-Path "$root\protected_apps.txt")) { Copy-Item "$root\protected_apps.example.txt" "$root\protected_apps.txt" }

if (-not $NoShortcut) {
Step "Desktop shortcut"
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Iris.lnk"
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = "powershell.exe"
$s.Arguments = "-ExecutionPolicy Bypass -File `"$root\run_demo.ps1`""
$s.WorkingDirectory = $root
$s.Save()
}

Step "Done"
Write-Host @"
Start Iris with the 'Iris' shortcut on your desktop, put on headphones, wait for
"Hi, I'm Iris", and talk. Try: "Open the calculator", "What's on my screen?"

Change the brain with one line in .env:  IRIS_BRAIN=cloud | claude-code | anthropic | gateway
Optional: list apps Iris must never touch in protected_apps.txt
"@
