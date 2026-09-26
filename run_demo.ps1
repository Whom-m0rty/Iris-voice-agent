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

# 2. Cursor overlay (visual only, clicks pass through)
Start-Process $py -ArgumentList "`"$root\voice\overlay.py`"" -WindowStyle Hidden

# 3. Observer panel, docked on the right edge of a 1920x1080 screen
Start-Process $edge -ArgumentList "--app=file:///$($root -replace '\\','/')/voice/panel.html",
    "--user-data-dir=$env:TEMP\iris-panel-profile", "--no-first-run",
    "--window-position=1460,0", "--window-size=460,1040"

# 4. Voice session (foreground, so its log stays visible)
Set-Location "$root\voice"
& $py iris.py        # main: AssemblyAI streaming STT + Claude + neural TTS
# & $py client.py    # second backend: everything on the AssemblyAI Voice Agent API
