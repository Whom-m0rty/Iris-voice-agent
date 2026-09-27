"""What is on screen (UI Automation) and how to act on it."""
import base64
import io
import os
import re
import time
from dataclasses import dataclass

import uiautomation as auto
from PIL import ImageGrab

ACTIONABLE = {
    "ButtonControl", "MenuItemControl", "EditControl", "TabItemControl",
    "ListItemControl", "HyperlinkControl", "CheckBoxControl", "ComboBoxControl",
    "SplitButtonControl", "RadioButtonControl", "TreeItemControl", "DocumentControl",
}
READABLE = {"TextControl"}
TYPEABLE = {"EditControl", "DocumentControl", "ComboBoxControl"}


@dataclass
class Element:
    key: str
    kind: str
    name: str
    ctrl: auto.Control
    password: bool = False
    offscreen: bool = False

    @property
    def typeable(self) -> bool:
        """From the snapshot, not the live control: web pages replace elements under us."""
        return f"{self.kind}Control" in TYPEABLE

    @property
    def label(self) -> str:
        kind = "PasswordField" if self.password else self.kind
        label = f"{kind} '{self.name}'" if self.name else f"{kind} (no label)"
        return label + (" (off screen)" if self.offscreen else "")


@dataclass
class Snapshot:
    title: str
    elements: dict[str, Element]
    texts: list[str]
    unlabeled: int

    def state(self) -> str:
        shown = "; ".join(t for t in self.texts[:15])
        return f"Window: {self.title}. Visible text: {shown or 'none'}."


def foreground() -> auto.WindowControl:
    return auto.GetForegroundControl().GetTopLevelControl()


IGNORED_WINDOWS = re.compile(r"^(Program Manager|Iris — observer|.*overlay.*|MSCTFIME UI|Default IME)$", re.I)


# ---- do-not-touch list -------------------------------------------------------------
# voice-hack/protected_apps.txt: one entry per line, matched (case-insensitive) against the
# program's full path, e.g. "AppData\Roaming\Telegram Desktop\Telegram.exe" for the user's
# own Telegram while a test account runs from a portable copy. Such windows are never listed,
# never resolved and never read or clicked.
PROTECTED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "protected_apps.txt")


class Protected(RuntimeError):
    """The window belongs to an app on the do-not-touch list."""


def _protected_entries() -> list[str]:
    try:
        with open(PROTECTED_FILE, encoding="utf-8") as f:
            return [ln.strip().lower() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return []


def process_path(pid: int) -> str:
    import ctypes
    from ctypes import wintypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def is_protected(window) -> bool:
    entries = _protected_entries()
    if not entries or window is None:
        return False
    try:
        path = process_path(window.ProcessId).lower()
    except Exception:
        return False
    return any(e in path for e in entries)


def guard(window) -> None:
    if is_protected(window):
        raise Protected(f"'{window.Name}' is on the do-not-touch list (protected_apps.txt)")


def _hwnd_protected(hwnd: int) -> bool:
    import ctypes
    from ctypes import wintypes
    entries = _protected_entries()
    if not entries or not hwnd:
        return False
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(_top_level(hwnd), ctypes.byref(pid))
    path = process_path(pid.value).lower()
    return any(e in path for e in entries)


def guard_foreground() -> None:
    """Keystrokes go to whatever window has the focus. If a protected app took it (a
    notification clicked, a window that popped up), nothing is typed."""
    import ctypes
    if _hwnd_protected(ctypes.windll.user32.GetForegroundWindow()):
        raise Protected("the window in front is on the do-not-touch list (protected_apps.txt)")


def guard_point(x: int, y: int) -> None:
    """Pixels at this screen point must not belong to a protected app."""
    import ctypes
    from ctypes import wintypes
    if _hwnd_protected(ctypes.windll.user32.WindowFromPoint(wintypes.POINT(x, y))):
        raise Protected(f"point ({x}, {y}) is on a window from the do-not-touch list")


def send_keys(keys: str, **kw) -> None:
    """uiautomation SendKeys, but never into a protected app."""
    guard_foreground()
    auto.SendKeys(keys, **kw)


def app_windows() -> list:
    """Visible top-level windows a person works in: not notifications, tooltips or toolbars
    (Telegram's pop-up notification is a 320x80 window titled 'TelegramDesktop')."""
    out = []
    for w in auto.GetRootControl().GetChildren():
        try:
            name = (w.Name or "").strip()
            r = w.BoundingRectangle
            if (not name or w.IsOffscreen or IGNORED_WINDOWS.match(name)
                    or r.width() < 400 or r.height() < 250 or "Tool" in (w.ClassName or "")
                    or is_protected(w)):
                continue
            out.append(w)
        except Exception:
            continue
    return out


def open_windows() -> list[str]:
    """Titles of the visible top-level windows, for the voice LLM to pick from."""
    titles = []
    for w in app_windows():
        if w.Name.strip() not in titles:
            titles.append(w.Name.strip())
    return titles


def resolve_window(name: str, timeout: float = 2) -> auto.WindowControl | None:
    """The window the user means: title match first, then the open window sharing the most
    words with `name` ("Mail" -> "Inbox - ...", "Gmail" -> "Inbox (3) - ... - Gmail - Google Chrome")."""
    # the app itself before a title that merely mentions it: "Telegram" is the Telegram window
    # (titled with the chat's name), not an Explorer window showing a "Telegram Desktop" folder
    named = {x for x in re.findall(r"\w+", name.lower()) if len(x) > 2}
    apps_named = [w for w in app_windows() if _process_name(w.ProcessId) in named]
    w = find_window(re.escape(name), timeout if not apps_named else 0) if name else None
    if apps_named and len(named) <= 2 and (w is None or _process_name(w.ProcessId) not in named):
        return apps_named[0]
    if w:
        return w
    words = {x for x in re.findall(r"\w+", name.lower()) if len(x) > 2}
    aliases = {"mail": {"inbox", "gmail", "mail"}, "email": {"inbox", "gmail", "mail"},
               "inbox": {"inbox", "gmail", "mail"}, "calculator": {"calculator", "калькулятор"}}
    for x in list(words):
        words |= aliases.get(x, set())
    # apps whose title is the open document or chat (Telegram shows the chat name): match the process
    by_process = [w for w in app_windows() if _process_name(w.ProcessId) in words]
    if by_process:
        return by_process[0]                  # topmost window of that app
    best, score = None, 0
    for title in open_windows():
        s = len(words & set(re.findall(r"\w+", title.lower())))
        if s > score:
            best, score = title, s
    return find_window(re.escape(best), timeout) if best else None


def _process_name(pid: int) -> str:
    """Executable name without .exe, lower case ("telegram", "chrome")."""
    path = process_path(pid)
    return os.path.splitext(os.path.basename(path))[0].lower() if path else ""


def find_window(title_re: str, timeout: float = 5) -> auto.WindowControl | None:
    """The largest app window whose title matches (never a notification or tooltip)."""
    import time
    pattern = re.compile(f".*({title_re}).*", re.I)
    end = time.perf_counter() + timeout
    while True:
        hits = [w for w in app_windows() if pattern.match(w.Name or "")]
        if hits:
            return hits[0]                    # topmost; "largest" once picked the user's other Telegram
        if time.perf_counter() >= end:
            return None
        time.sleep(0.2)


BROWSER_CLASSES = {"Chrome_WidgetWin_1"}      # Edge, Chrome and Electron apps


def page_root(window: auto.Control) -> auto.Control:
    """In a browser, the web page itself (its root document), without the browser's own
    toolbar, tabs and pop-up buttons; any other window is used whole."""
    if window.ClassName not in BROWSER_CLASSES:
        return window
    best, area = None, 0
    for ctrl, _ in auto.WalkControl(window, maxDepth=14):
        if ctrl.ControlTypeName == "DocumentControl" and (ctrl.Name or "").strip():
            r = ctrl.BoundingRectangle
            if r.width() * r.height() > area:
                best, area = ctrl, r.width() * r.height()
    return best or window


def snapshot(window: auto.Control, max_depth: int = 25, tries: int = 4) -> Snapshot:
    """A page that is navigating drops its tree mid-walk; take the snapshot again."""
    import time
    for attempt in range(tries):
        try:
            return _snapshot(window, max_depth)
        except Protected:
            raise
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(0.3)


OFFSCREEN_LIMIT = 80                            # labelled off-screen controls offered on a web page


def _snapshot(window: auto.Control, max_depth: int = 25) -> Snapshot:
    guard(window)
    elements, texts, unlabeled = {}, [], 0
    root = page_root(window)
    web = root is not window
    offscreen_added = 0
    for ctrl, _ in auto.WalkControl(root, maxDepth=max_depth):
        try:
            kind = ctrl.ControlTypeName
            ctrl.Name, ctrl.IsEnabled, ctrl.IsOffscreen    # touch now: a live page may drop it
        except Exception:
            continue
        if kind in READABLE:
            name = (ctrl.Name or "").strip()
            if len(name) > 1 and name not in texts:
                texts.append(name)
            continue
        if kind not in ACTIONABLE or not ctrl.IsEnabled:
            continue
        offscreen = ctrl.IsOffscreen and kind not in TYPEABLE
        if offscreen:
            # on a web page a labelled control out of view is still reachable (scrolled into view
            # before the click); elsewhere off-screen means hidden
            if not (web and (ctrl.Name or "").strip() and offscreen_added < OFFSCREEN_LIMIT):
                continue
            offscreen_added += 1
        name = (ctrl.Name or "").strip()
        if not name:
            unlabeled += 1
        password = kind in TYPEABLE and _is_password(ctrl)
        if kind in TYPEABLE and not password:     # a password field's content is never read
            content = _text_of(ctrl)
            if content:
                texts.append(f"{name or 'text field'} contains: {content[:120]}")
        el = Element(f"e{len(elements)}", kind.removesuffix("Control"), name, ctrl, password, offscreen)
        if name and _duplicate(el, elements.values()):
            continue                          # Qt nests same-named controls; offer each once
        elements[el.key] = el
    return Snapshot(window.Name, elements, texts, unlabeled)


def wait_for_change(window, before: str, timeout: float = 0.6, every: float = 0.05) -> bool:
    """Return as soon as the window's visible state differs from `before`."""
    import time
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        time.sleep(every)
        try:
            if _snapshot(window).state() != before:
                return True
        except Exception:
            return True                       # the page is being replaced: that is a change
    return False


def _duplicate(el: Element, seen) -> bool:
    """Same kind and name as an element already listed, with one rectangle inside the other."""
    r = el.ctrl.BoundingRectangle
    for other in seen:
        if other.kind == el.kind and other.name == el.name:
            o = other.ctrl.BoundingRectangle
            inside = r.left >= o.left and r.top >= o.top and r.right <= o.right and r.bottom <= o.bottom
            outside = o.left >= r.left and o.top >= r.top and o.right <= r.right and o.bottom <= r.bottom
            if inside or outside:
                return True
    return False


def _is_password(ctrl) -> bool:
    try:
        return bool(ctrl.IsPassword)
    except Exception:
        return False


def focused_is_password() -> bool:
    try:
        return _is_password(auto.GetFocusedControl())
    except Exception:
        return False


def _text_of(ctrl) -> str:
    for get in (lambda: ctrl.GetValuePattern().Value,
                lambda: ctrl.GetTextPattern().DocumentRange.GetText(200)):
        try:
            v = (get() or "").strip()
        except Exception:
            continue
        if v:
            return v
    return ""


def scroll_into_view(el: Element) -> None:
    try:
        p = el.ctrl.GetScrollItemPattern()
        if p:
            p.ScrollIntoView(waitTime=0)
    except Exception:
        pass


def is_browser(window) -> bool:
    return window.ClassName in BROWSER_CLASSES


def scroll(window, x: int, y: int, direction: str, notches: int = 5) -> None:
    """Mouse wheel over a point of the window."""
    auto.MoveTo(x, y, waitTime=0)
    wheel = auto.WheelUp if direction == "up" else auto.WheelDown
    wheel(wheelTimes=notches, waitTime=0)


def scroll_window(window, direction: str, notches: int = 6) -> None:
    """Scroll the main content of a window: the web page in a browser, else the window's middle."""
    r = page_root(window).BoundingRectangle
    scroll(window, (r.left + r.right) // 2, (r.top + r.bottom) // 2, direction, notches)


def click(el: Element) -> None:
    if el.offscreen:
        scroll_into_view(el)
        import time
        time.sleep(0.3)
    # waitTime=0: uiautomation otherwise sleeps 0.5 s after every pattern call;
    # wait_for_change() does the waiting instead, and only as long as needed
    c = el.ctrl
    actions = (("GetInvokePattern", "Invoke"), ("GetSelectionItemPattern", "Select"),
               ("GetTogglePattern", "Toggle"))
    for getter, method in actions:
        try:
            pattern = getattr(c, getter)()
        except Exception:
            pattern = None
        if pattern:
            getattr(pattern, method)(waitTime=0)
            return
    c.Click(simulateMove=False, waitTime=0)


# Windows 11 Notepad (and other apps with typing suggestions) drops or repeats characters
# typed faster than this: "I am running late" came out as "I am eeeeeeeeeeee" with no pause
TYPE_DELAY = float(os.environ.get("TYPE_DELAY_MS", "80")) / 1000


def type_secret(el: Element, value: str) -> None:
    """Fill a password field from the vault. Keystrokes only: the value never goes
    through ValuePattern, logs or any model."""
    el.ctrl.Click(simulateMove=False, waitTime=0)
    guard_foreground()
    for ch in value:              # char by char: no SendKeys escape syntax to get wrong
        auto.SendUnicodeChar(ch)
        time.sleep(TYPE_DELAY)


def type_keys(text: str) -> None:
    """Put text where the keyboard focus is: pasted (instant and exact), the user's clipboard
    restored after; key by key when the clipboard is busy. Short texts (a keypad's digits)
    are typed, since some keypads ignore a paste."""
    guard_foreground()
    if len(text) > 3:
        import clipboard
        saved = clipboard.get_text()
        if clipboard.set_text(text):
            auto.SendKeys("{Ctrl}v", waitTime=0)
            time.sleep(0.25)                          # the app reads the clipboard after the key
            if saved is not None:
                clipboard.set_text(saved, private=False)
            return
    for ch in text:
        auto.SendUnicodeChar(ch)
        time.sleep(TYPE_DELAY)


def type_text(el: Element, text: str) -> None:
    """Set the field's value; if the app did not take it (Qt apps such as Telegram report
    success but show nothing), click the field and type the characters instead."""
    scroll_into_view(el)
    try:
        vp = el.ctrl.GetValuePattern()
    except Exception:
        vp = None
    if vp and not vp.IsReadOnly:
        try:
            vp.SetValue(text, waitTime=0)
            if (vp.Value or "").strip() == text.strip():
                return
        except Exception:
            pass
    el.ctrl.Click(simulateMove=False, waitTime=0)
    send_keys("{Ctrl}a", waitTime=0)              # replace whatever the field held
    time.sleep(0.15)                              # let Ctrl go up, or the first letters become shortcuts
    type_keys(text)


# ---- pixels, for the vision fallback -----------------------------------------

class NotOnTop(RuntimeError):
    """The target window could not be brought to the front, so pixels would show another app."""


def _top_level(hwnd: int) -> int:
    import ctypes
    return ctypes.windll.user32.GetAncestor(hwnd, 2)          # GA_ROOT


def bring_to_front(window: auto.Control, timeout: float = 1.5) -> bool:
    """Restore and focus the window, and check that it really is the foreground window.
    Windows refuses SetForegroundWindow to background processes, hence the Alt tap."""
    import ctypes
    import time
    guard(window)
    user32 = ctypes.windll.user32
    hwnd = window.NativeWindowHandle
    end = time.perf_counter() + timeout
    if _top_level(user32.GetForegroundWindow()) == _top_level(hwnd):
        return True
    tries = 0
    while time.perf_counter() < end:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)                          # SW_RESTORE
        if tries:
            # only when the polite way failed: an Alt tap lets us take the foreground, but it
            # also highlights the menu of apps like Chrome
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            time.sleep(0.05)
            user32.keybd_event(0x1B, 0, 0, 0)                   # Esc: drop that menu highlight
            user32.keybd_event(0x1B, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
        window.SetActive()
        time.sleep(0.15)
        if _top_level(user32.GetForegroundWindow()) == _top_level(hwnd):
            return True
        tries += 1
    return False


MAX_PIXELS = 1_150_000


def capture(window: auto.Control, max_w: int = 1568) -> tuple[str, tuple[int, int, float]]:
    """PNG of the window as base64, plus (left, top, scale) to map image coords back to screen.
    Refuses when the window is not in front: the grab would show someone else's pixels."""
    guard(window)
    if not bring_to_front(window):
        raise NotOnTop(window.Name)
    r = window.BoundingRectangle
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
    # the Claude API shrinks images above ~1.15 megapixels (or 1568 px on a side) before the
    # model sees them, and the model then answers in the shrunken coordinates. Send an image it
    # will not resize, so x, y map back exactly.
    scale = min(1.0, max_w / img.width, max_w / img.height, (MAX_PIXELS / (img.width * img.height)) ** 0.5)
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode(), (r.left, r.top, scale)


def capture_region(x: int, y: int, half: int = 160, zoom: int = 2) -> tuple[str, tuple[int, int, float]]:
    """A (2*half)^2 px square around a screen point, enlarged `zoom` times: a second, closer
    look for the vision model when there is no control to snap to."""
    from PIL import Image
    guard_point(x, y)
    left, top = max(0, x - half), max(0, y - half)
    img = ImageGrab.grab(bbox=(left, top, left + 2 * half, top + 2 * half), all_screens=True)
    img = img.resize((img.width * zoom, img.height * zoom), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode(), (left, top, float(zoom))


def click_point(x: int, y: int, window: auto.Control | None = None) -> None:
    """Click an absolute screen point (guarded like click_at)."""
    click_at((0, 0, 1.0), x, y, window)


def snap_to_control(window, x: int, y: int, radius: int = 30):
    """The clickable control under a screen point, or the nearest one within `radius` px.
    A vision model's point on a small icon is often a few pixels off; the control is not."""
    best, best_d = None, radius + 1
    for el in _snapshot(window).elements.values():
        if el.typeable:
            continue
        try:
            r = el.ctrl.BoundingRectangle
        except Exception:
            continue
        if r.width() <= 0 or r.height() <= 0 or r.width() * r.height() > 250_000:
            continue                          # skip huge containers
        dx = max(r.left - x, 0, x - r.right)
        dy = max(r.top - y, 0, y - r.bottom)
        d = (dx * dx + dy * dy) ** 0.5
        if d < best_d or (d == best_d == 0 and best and r.width() * r.height() < best[1]):
            best, best_d = (el, r.width() * r.height()), d
    return best[0] if best else None


def click_at(origin: tuple[int, int, float], x: int, y: int, window: auto.Control | None = None) -> None:
    """Click a point from the screenshot. With `window`, the point must belong to that
    window - never click into whatever else happens to be on top."""
    import ctypes
    from ctypes import wintypes
    left, top, scale = origin
    sx, sy = int(left + x / scale), int(top + y / scale)
    guard_point(sx, sy)
    if window is not None:
        hit = ctypes.windll.user32.WindowFromPoint(wintypes.POINT(sx, sy))
        if _top_level(hit) != _top_level(window.NativeWindowHandle):
            raise NotOnTop(f"point ({sx}, {sy}) is covered by another window")
    auto.Click(sx, sy, waitTime=0)
