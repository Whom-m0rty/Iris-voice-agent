"""Do-not-touch list: keystrokes, pixel clicks and zoomed grabs are refused when they would
reach a protected app. Pretends the app in front is protected; nothing is clicked or typed.
    python tests/protected_guard_test.py
"""
import ctypes
import os
import sys
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
import screen  # noqa: E402

user32 = ctypes.windll.user32
fg = user32.GetForegroundWindow()
pid = wintypes.DWORD()
user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
path = screen.process_path(pid.value)
rect = wintypes.RECT()
user32.GetWindowRect(fg, ctypes.byref(rect))
cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
print("app in front:", path)


def refused(fn) -> bool:
    try:
        fn()
    except screen.Protected:
        return True
    return False


checks = []
screen._protected_entries = lambda: [path.lower()]           # the app in front is "protected"
checks += [("keys refused", refused(screen.guard_foreground)),
           ("send_keys refused", refused(lambda: screen.send_keys(""))),
           ("type_keys refused", refused(lambda: screen.type_keys(""))),
           ("pixel click refused", refused(lambda: screen.click_at((0, 0, 1.0), cx, cy))),
           ("zoomed grab refused", refused(lambda: screen.capture_region(cx, cy)))]
screen._protected_entries = lambda: ["c:\\no\\such\\app.exe"]
checks += [("keys allowed otherwise", not refused(screen.guard_foreground)),
           ("grab allowed otherwise", not refused(lambda: screen.capture_region(cx, cy)))]

for name, ok in checks:
    print("PASS" if ok else "FAIL", name)
sys.exit(0 if all(ok for _, ok in checks) else 1)
