"""Browser shortcuts for the brain: open an address, go back, read what the page says.

Going straight to a URL (a search URL, a product page) replaces a dozen clicks through
menus. Works on Chrome and Edge through the keyboard, in the user's own logged-in browser.
"""
import re
import time
from urllib.parse import quote_plus

import uiautomation as auto

import screen

BROWSERS = ("chrome", "msedge")
SEARCH = {  # common "search on X" addresses the brain can fill in
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "amazon.it": "https://www.amazon.it/s?k={q}",
    "amazon.com": "https://www.amazon.com/s?k={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
}


def browser_window(name: str = ""):
    """The browser window the user means, or the topmost browser window."""
    if name:
        w = screen.resolve_window(name, 1)
        if w and screen._process_name(w.ProcessId) in BROWSERS:
            return w
    for w in screen.app_windows():
        if screen._process_name(w.ProcessId) in BROWSERS:
            return w
    return None


def search_url(site: str, query: str) -> str | None:
    tpl = SEARCH.get(site.lower().removeprefix("www."))
    return tpl.format(q=quote_plus(query)) if tpl else None


def open_url(url: str, window=None, timeout: float = 8.0) -> str:
    """Open the address in a NEW tab of the user's browser and wait for its title.
    Never in the current tab: that is the page the person is looking at (a test replaced the
    user's GitHub settings page this way)."""
    if not re.match(r"^https?://", url):
        url = "https://" + url
    w = window or browser_window()
    if w is None:
        return "No browser window is open."
    if not screen.bring_to_front(w):
        return "I could not bring the browser to the front."
    before = w.Name
    auto.SendKeys("{Ctrl}t", waitTime=0.4)           # new tab; its address bar has the focus
    screen.type_keys(url)
    auto.SendKeys("{Enter}", waitTime=0)             # navigation only: nothing is sent or bought
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        time.sleep(0.3)
        w2 = browser_window()
        if w2 is not None and w2.Name != before:
            time.sleep(0.8)                          # let the page settle
            return f"Opened {url}. Page title: {w2.Name}"
    return f"Opened {url}, but the page title did not change yet (it may still be loading)."


def back(window=None) -> str:
    w = window or browser_window()
    if w is None or not screen.bring_to_front(w):
        return "No browser window to go back in."
    auto.SendKeys("{Alt}{Left}", waitTime=1.2)
    return f"Went back. Page title: {(browser_window() or w).Name}"


def read(window=None, limit: int = 2500) -> str:
    """What the page or window shows, as text, for the brain to answer from."""
    w = window or screen.foreground()
    snap = screen.snapshot(w)
    links = [e.name for e in snap.elements.values() if e.name and not e.offscreen][:40]
    text = " | ".join(snap.texts)
    return (f"Window: {w.Name}\nText: {text[:limit]}\n"
            f"Things to click: {'; '.join(links)[:800]}")
