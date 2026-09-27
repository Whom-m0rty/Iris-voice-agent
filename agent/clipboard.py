"""Paste text the way a person would, then put the user's clipboard back.

Typing a message key by key is slow and some apps (Windows 11 Notepad with typing
suggestions) drop or repeat characters typed quickly. A paste is instant and exact. What we
paste is marked so it stays out of the clipboard history (Win+V) and cloud clipboard.
Never used for passwords: those are typed from the vault, key by key.
"""
import ctypes
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32.OpenClipboard.argtypes = [wintypes.HWND]
u32.GetClipboardData.restype = wintypes.HANDLE
u32.GetClipboardData.argtypes = [wintypes.UINT]
u32.SetClipboardData.restype = wintypes.HANDLE
u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
u32.RegisterClipboardFormatW.restype = wintypes.UINT
u32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
k32.GlobalAlloc.restype = wintypes.HGLOBAL
k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
k32.GlobalLock.restype = wintypes.LPVOID
k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

_EXCLUDE = u32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")


def _open() -> bool:
    for _ in range(20):                    # another app may hold it for a moment
        if u32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_text() -> str | None:
    if not u32.IsClipboardFormatAvailable(CF_UNICODETEXT) or not _open():
        return None
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def _global(data: bytes):
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    return h


def set_text(text: str, private: bool = True) -> bool:
    if not _open():
        return False
    try:
        u32.EmptyClipboard()
        ok = bool(u32.SetClipboardData(CF_UNICODETEXT, _global((text + "\0").encode("utf-16-le"))))
        if private and _EXCLUDE:
            u32.SetClipboardData(_EXCLUDE, _global(b"\0\0\0\0"))
        return ok
    finally:
        u32.CloseClipboard()
