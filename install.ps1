# Iris installer for Windows 10/11 with an NVIDIA GPU.
#   powershell -ExecutionPolicy Bypass -File install.ps1
# Safe to run again: every step checks what is already there.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`n$text" -ForegroundColor Red; exit 1 }

Step "Checking the basics"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git is missing: https://git-scm.com/download/win" }
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Fail "No NVIDIA driver found. Kev (the local decision model) needs an NVIDIA GPU with ~10 GB free."
}
$gpu = (nvidia-smi --query-gpu=name,memory.total --format=csv,noheader) | Select-Object -First 1
Write-Host "GPU: $gpu"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python package manager)..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "uv did not install; see https://docs.astral.sh/uv/" }
}

Step "Getting Kev (open-weights decision model)"
if (-not (Test-Path "$root\kev\pyproject.toml")) {
    git clone https://github.com/jaredpalmer/kev.git "$root\kev"
}
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
Write-Host "PyTorch sees the GPU."

Step "Installing Iris"
uv pip install --python $py -r "$root\requirements-iris.txt"
Set-Location $root

Step "Keys (stored in .env next to this script; never committed)"
$envFile = "$root\.env"
if (-not (Test-Path $envFile)) { New-Item -ItemType File $envFile | Out-Null }
$current = Get-Content $envFile -ErrorAction SilentlyContinue
function Has($name) { return [bool]($current | Where-Object { $_ -like "$name=*" }) }
function Put($name, $value) { Add-Content -Path $envFile -Value "$name=$value" -Encoding ascii }

if (-not (Has "ASSEMBLYAI_API_KEY")) {
    Write-Host "AssemblyAI key (free credits: https://www.assemblyai.com/dashboard/signup)"
    $k = Read-Host "ASSEMBLYAI_API_KEY"
    if ($k) { Put "ASSEMBLYAI_API_KEY" $k.Trim() }
}
if (-not (Has "CLAUDE_CODE_OAUTH_TOKEN") -and -not (Has "ANTHROPIC_API_KEY")) {
    Write-Host "`nClaude plans the tasks and looks at the screen. Choose one:"
    Write-Host "  1) Claude Code login on this PC (personal use; run 'claude setup-token' to get a token)"
    Write-Host "  2) Anthropic API key"
    $choice = Read-Host "1 or 2"
    if ($choice -eq "2") {
        $k = Read-Host "ANTHROPIC_API_KEY"
        if ($k) { Put "ANTHROPIC_API_KEY" $k.Trim(); Put "BRAIN_BACKEND" "api"; Put "VISION_BACKEND" "api" }
    } else {
        if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
            Write-Host "Claude Code is not installed: https://claude.com/claude-code (then run install.ps1 again)" -ForegroundColor Yellow
        }
        $k = Read-Host "CLAUDE_CODE_OAUTH_TOKEN (from 'claude setup-token')"
        if ($k) { Put "CLAUDE_CODE_OAUTH_TOKEN" $k.Trim() }
    }
}
if (-not (Test-Path "$root\protected_apps.txt")) { Copy-Item "$root\protected_apps.example.txt" "$root\protected_apps.txt" }

Step "Done"
Write-Host @"
Next:
  - Optional: connect Gmail  ->  $py voice\mcp_servers\mail.py login   (needs client_secret.json, see README)
  - Optional: list apps Iris must never touch in protected_apps.txt
  - Start (the first start downloads Kev, ~9 GB):
        powershell -ExecutionPolicy Bypass -File run_demo.ps1
    Put on headphones, wait for "Hi, I'm Iris", and talk.
"@
