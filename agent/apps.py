"""Open an app by the name a person says: "open Telegram", "start the calculator".

Where apps come from: Start menu shortcuts, the desktop (shortcuts, and programs one folder
down, such as a portable Telegram), and Store apps from Get-StartApps. An app on the
do-not-touch list (protected_apps.txt) is never started; when two apps share a name, the
one that is not protected wins. If the app is already open, it is brought to the front.
"""
import difflib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass

import screen

# what people say -> what the Start menu calls it
ALIASES = {
    "browser": ["google chrome", "microsoft edge"], "internet": ["google chrome", "microsoft edge"],
    "chrome": ["google chrome"], "edge": ["microsoft edge"],
    "calculator": ["calculator"], "calc": ["calculator"],
    "notes": ["notepad"], "text editor": ["notepad"],
    "files": ["file explorer"], "my files": ["file explorer"], "explorer": ["file explorer"],
    "settings": ["settings"], "control panel": ["control panel"],
    "word": ["word"], "excel": ["excel"], "mail": ["outlook", "mail"],
    "camera": ["camera"], "photos": ["photos"], "music": ["media player", "spotify"],
}
SKIP = re.compile(r"\b(uninstall|readme|help|license|release notes|website|documentation)\b", re.I)
EXE_SKIP = re.compile(r"(unins\d*|update|updater|crashpad|helper|setup|install)", re.I)


@dataclass
class App:
    name: str
    path: str = ""          # exe, shortcut or file to start
    aumid: str = ""         # Store / Start menu id, started through shell:AppsFolder
    target: str = ""        # what a shortcut points to, for the do-not-touch check

    @property
    def words(self) -> set[str]:
        return set(_words(self.name))


def _words(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)          # TelegramDesktopPortable -> words
    return [w for w in re.findall(r"[a-zа-яё0-9]+", text.lower()) if w not in ("the", "app", "application")]


_cache: list[App] | None = None


def _shell():
    import comtypes
    import comtypes.client
    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    return comtypes.client.CreateObject("WScript.Shell", dynamic=True)


def _lnk_target(shell, path: str) -> str:
    try:
        return shell.CreateShortcut(path).TargetPath or ""
    except Exception:
        return ""


def index(refresh: bool = False) -> list[App]:
    global _cache
    if _cache is not None and not refresh:
        return _cache
    apps: list[App] = []
    shell = _shell()
    start_dirs = [os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
                  os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs")]
    for d in start_dirs:
        for root, _, files in os.walk(d):
            for f in files:
                if f.lower().endswith(".lnk") and not SKIP.search(f):
                    p = os.path.join(root, f)
                    apps.append(App(f[:-4], path=p, target=_lnk_target(shell, p)))
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    try:
        for f in os.listdir(desktop):
            p = os.path.join(desktop, f)
            low = f.lower()
            if low.endswith((".lnk", ".url")) and not SKIP.search(f):
                apps.append(App(f[:-4], path=p, target=_lnk_target(shell, p) if low.endswith(".lnk") else p))
            elif low.endswith(".exe") and not EXE_SKIP.search(f):
                apps.append(App(f[:-4], path=p, target=p))
            elif os.path.isdir(p):                     # portable apps: Folder\Program.exe
                try:
                    for g in os.listdir(p):
                        if g.lower().endswith(".exe") and not EXE_SKIP.search(g):
                            q = os.path.join(p, g)
                            apps.append(App(g[:-4], path=q, target=q))
                except OSError:
                    pass
    except OSError:
        pass
    known = {a.name.lower() for a in apps}
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        rows = json.loads(out or "[]")
        for r in rows if isinstance(rows, list) else [rows]:
            name, aumid = r.get("Name") or "", r.get("AppID") or ""
            if name and aumid and not SKIP.search(name) and name.lower() not in known:
                apps.append(App(name, aumid=aumid))
    except Exception:
        pass
    _cache = apps
    return apps


def _protected(app: App, apps: list[App]) -> bool:
    entries = screen._protected_entries()
    if not entries:
        return False
    if app.target or app.path:
        return any(e in (app.target or app.path).lower() for e in entries)
    # a Start menu id with no path: protected if a shortcut with the same name is
    return any(o.name.lower() == app.name.lower() and (o.target or o.path)
               and any(e in (o.target or o.path).lower() for e in entries) for o in apps)


def _clean(query: str) -> str:
    return re.sub(r"^(the|my|a|an)\s+|\s+(app|application|program)$", "", query.lower().strip()).strip()


def _score(query: str, app: App) -> float:
    query = _clean(query)
    q = set(_words(query))
    names = [query] + ALIASES.get(query, [])
    best = 0.0
    for n in names:
        if app.name.lower() == n:
            best = max(best, 1.0)
    if q and q <= app.words:
        best = max(best, 0.9 - 0.02 * len(app.words - q))     # fewer extra words is closer
    for n in names:
        best = max(best, 0.8 * difflib.SequenceMatcher(None, n, app.name.lower()).ratio())
    return best


def find(query: str) -> tuple[App | None, str]:
    """The app the user means, and why not if there is none."""
    apps = index()
    ranked = sorted(((a, _score(query, a)) for a in apps), key=lambda x: -x[1])
    ranked = [(a, s) for a, s in ranked if s >= 0.7]   # 'photoshop' is not 'Photos'
    if not ranked:
        return None, f"I could not find an app called {query}."
    allowed = [(a, s) for a, s in ranked if not _protected(a, apps)]
    if not allowed:
        return None, f"{ranked[0][0].name} is on your do-not-touch list, so I will not open it."
    return allowed[0][0], ""


def _already_open(query: str, app: App):
    q = set(_words(query)) | app.words
    for w in screen.app_windows():                     # protected windows are never listed
        proc = screen._process_name(w.ProcessId)
        title = set(_words(w.Name or ""))
        if proc in q or (app.words and app.words <= title):
            return w
    return None


def open_app(query: str, timeout: float = 12) -> str:
    query = (query or "").strip()
    if not query:
        return "ERROR: say which app to open."
    app, why = find(query)
    if app is None:
        return f"ERROR: {why}"
    w = _already_open(query, app)
    if w is not None:
        ok = screen.bring_to_front(w)
        return f"{app.name} was already open; now in front: {w.Name}" if ok else \
            f"{app.name} is open ({w.Name}), but I could not bring it to the front."
    before = {w.NativeWindowHandle for w in screen.app_windows()}
    if app.aumid:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.aumid}"])
    else:
        os.startfile(app.path, cwd=os.path.dirname(app.path))
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        time.sleep(0.5)
        new = [w for w in screen.app_windows() if w.NativeWindowHandle not in before]
        if new:
            screen.bring_to_front(new[0])
            return f"Opened {app.name}; now in front: {new[0].Name}"
    return f"I started {app.name}, but no window has appeared yet. It may still be loading."
