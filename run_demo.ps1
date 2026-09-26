# Starts the whole demo: Kev decision server, cursor overlay, observer panel, voice session.
# Needs ASSEMBLYAI_API_KEY in .env. Stop with Ctrl+C (the overlay and panel close separately).
$root = $PSScriptRoot
$py = "$root\kev\.venv\Scripts\python.exe"
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"

# 1. Kev on :8009 (skip if already up)
try { Invoke-WebRequest http://127.0.0.1:8009/docs -UseBasicParsing -TimeoutSec 2 | Out-Null }
catch {
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$root\start_kev.ps1`"" -WindowStyle Minimized
    Write-Host "waiting for Kev..."
    do { Start-Sleep 3; $up = $true; try { Invoke-WebRequest http://127.0.0.1:8009/docs -UseBasicParsing -TimeoutSec 2 | Out-Null } catch { $up = $false } } until ($up)
}

# 2. Cursor overlay + observer panel (visual only, clicks pass through; single instance)
Start-Process $py -ArgumentList "`"$root\voice\overlay.py`"" -WindowStyle Hidden

# 3. The observer panel is drawn by the overlay itself (always on top). The web version,
#    voice/panel.html, still works in any browser if you prefer a separate window.

# 4. Voice session (foreground, so its log stays visible)
Set-Location "$root\voice"
& $py iris.py        # main: AssemblyAI streaming STT + Claude + neural TTS
# & $py client.py    # second backend: everything on the AssemblyAI Voice Agent API
