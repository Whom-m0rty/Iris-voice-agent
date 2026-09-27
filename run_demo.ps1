# Starts Iris: Kev decision server (only with a local install), cursor overlay and observer
# panel, voice session. Brain and keys come from .env (IRIS_BRAIN, see agent/cloud.py).
# Stop with Ctrl+C (the overlay closes separately).
$root = $PSScriptRoot
$py = if (Test-Path "$root\.venv\Scripts\python.exe") { "$root\.venv\Scripts\python.exe" } else { "$root\kev\.venv\Scripts\python.exe" }
$envText = if (Test-Path "$root\.env") { Get-Content "$root\.env" -Raw } else { "" }
$wantKev = (Test-Path "$root\kev\.venv") -and ($envText -notmatch "(?m)^IRIS_DECISIONS=(cloud|jev)") -and ($envText -notmatch "(?m)^SYSTEMONE_URL=")

# 1. Kev on :8009 when installed locally (skip if already up)
if ($wantKev) {
    try { Invoke-WebRequest http://127.0.0.1:8009/docs -UseBasicParsing -TimeoutSec 2 | Out-Null }
    catch {
        Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$root\start_kev.ps1`"" -WindowStyle Minimized
        Write-Host "waiting for Kev..."
        do { Start-Sleep 3; $up = $true; try { Invoke-WebRequest http://127.0.0.1:8009/docs -UseBasicParsing -TimeoutSec 2 | Out-Null } catch { $up = $false } } until ($up)
    }
}

# 2. Cursor overlay + observer panel (visual only, clicks pass through; single instance)
Start-Process $py -ArgumentList "`"$root\voice\overlay.py`"" -WindowStyle Hidden

# 3. Voice session (foreground, so its log stays visible)
Set-Location "$root\voice"
& $py iris.py        # main: AssemblyAI streaming STT + brain + neural TTS
# & $py client.py    # second backend: everything on the AssemblyAI Voice Agent API
