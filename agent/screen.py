"""What is on screen (UI Automation) and how to act on it."""
import base64
import io
import re
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

    @property
    def label(self) -> str:
        kind = "PasswordField" if self.password else self.kind
        return f"{kind} '{self.name}'" if self.name else f"{kind} (no label)"


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


def open_windows() -> list[str]:
    """Titles of the visible top-level windows, for the voice LLM to pick from."""
    titles = []
    for w in auto.GetRootControl().GetChildren():
        name = (w.Name or "").strip()
        if name and not w.IsOffscreen and not IGNORED_WINDOWS.match(name) and name not in titles:
            titles.append(name)
    return titles


def resolve_window(name: str, timeout: float = 2) -> auto.WindowControl | None:
    """The window the user means: title match first, then the open window sharing the most
    words with `name` ("Mail" -> "Inbox - ...", "Gmail" -> "Inbox (3) - ... - Gmail - Google Chrome")."""
    w = find_window(re.escape(name), timeout) if name else None
    if w:
        return w
    words = {x for x in re.findall(r"\w+", name.lower()) if len(x) > 2}
    aliases = {"mail": {"inbox", "gmail", "mail"}, "email": {"inbox", "gmail", "mail"},
               "inbox": {"inbox", "gmail", "mail"}, "calculator": {"calculator", "калькулятор"}}
    for x in list(words):
        words |= aliases.get(x, set())
    best, score = None, 0
    for title in open_windows():
        s = len(words & set(re.findall(r"\w+", title.lower())))
        if s > score:
            best, score = title, s
    return find_window(re.escape(best), timeout) if best else None


def find_window(title_re: str, timeout: float = 5) -> auto.WindowControl | None:
    w = auto.WindowControl(searchDepth=1, RegexName=f".*({title_re}).*")
    return w if w.Exists(timeout) else None


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


def snapshot(window: auto.Control, max_depth: int = 25) -> Snapshot:
    elements, texts, unlabeled = {}, [], 0
    for ctrl, _ in auto.WalkControl(page_root(window), maxDepth=max_depth):
        kind = ctrl.ControlTypeName
        if kind in READABLE:
            name = (ctrl.Name or "").strip()
            if len(name) > 1 and name not in texts:
                texts.append(name)
            continue
        if kind not in ACTIONABLE or not ctrl.IsEnabled or ctrl.IsOffscreen:
            continue
        name = (ctrl.Name or "").strip()
        if not name:
            unlabeled += 1
        password = kind in TYPEABLE and _is_password(ctrl)
        if kind in TYPEABLE and not password:     # a password field's content is never read
            content = _text_of(ctrl)
            if content:
                texts.append(f"{name or 'text field'} contains: {content[:120]}")
        key = f"e{len(elements)}"
        elements[key] = Element(key, kind.removesuffix("Control"), name, ctrl, password)
    return Snapshot(window.Name, elements, texts, unlabeled)


def wait_for_change(window, before: str, timeout: float = 0.6, every: float = 0.05) -> bool:
    """Return as soon as the window's visible state differs from `before`."""
    import time
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        time.sleep(every)
        if snapshot(window).state() != before:
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


def click(el: Element) -> None:
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


def type_secret(el: Element, value: str) -> None:
    """Fill a password field from the vault. Keystrokes only: the value never goes
    through ValuePattern, logs or any model."""
    el.ctrl.Click(simulateMove=False, waitTime=0)
    for ch in value:              # char by char: no SendKeys escape syntax to get wrong
        auto.SendUnicodeChar(ch)


def type_text(el: Element, text: str) -> None:
    try:
        vp = el.ctrl.GetValuePattern()
    except Exception:
        vp = None
    if vp and not vp.IsReadOnly:
        vp.SetValue(text, waitTime=0)
    else:
        el.ctrl.Click(simulateMove=False, waitTime=0)
        auto.SendKeys(text, interval=0.01, waitTime=0)


# ---- pixels, for the vision fallback -----------------------------------------

def capture(window: auto.Control, max_w: int = 1280) -> tuple[str, tuple[int, int, float]]:
    """PNG of the window as base64, plus (left, top, scale) to map image coords back to screen."""
    window.SetActive()  # a screen grab shows whatever is on top, so bring the window forward
    import time
    time.sleep(0.25)
    r = window.BoundingRectangle
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
    scale = min(1.0, max_w / img.width)
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode(), (r.left, r.top, scale)


def click_at(origin: tuple[int, int, float], x: int, y: int) -> None:
    left, top, scale = origin
    auto.Click(int(left + x / scale), int(top + y / scale), waitTime=0)
