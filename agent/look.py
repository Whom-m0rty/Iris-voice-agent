"""What is on the screen, told to someone who cannot see it.

The brain gets facts, not pixels, and turns them into one or two spoken sentences:
  brief     where you are, what the window says, pop-ups first
  actions   what can be done here (the labelled controls)
  pictures  Claude vision describes the photos and images in the window
  lost      where you are, which pop-ups are open, which windows you could go back to

The accessibility tree answers most of it in well under a second; only "pictures" looks
at the pixels. Password fields are named, never read. Apps on the do-not-touch list
(protected_apps.txt) are never read: not their windows, pop-ups or focused control.
"""
import time

import uiautomation as auto

import screen
import vision

DESCRIBE_GOAL = (
    "DO NOT ACT. Look only at the photos and pictures in this window, not at the buttons, text "
    "or layout around them. Answer for someone who cannot see: {question} Reply with action "
    "\"done\" and put the answer in \"say\": at most three short, plain sentences, the most "
    "important thing first. Never say image, screenshot, window or screen. Name who is in a "
    "photo only if the screen says it (a sender name, a caption); otherwise describe what you "
    "see. If there is no photo or picture, say so in one sentence.")


def popups(window) -> list[str]:
    """Dialogs a person might be stuck in: standard Windows dialogs (#32770) anywhere, and
    windows inside the app window (web and app pop-ups). Each with its buttons."""
    found = []
    try:
        tops = [w for w in auto.GetRootControl().GetChildren()
                if w.ClassName == "#32770" and not w.IsOffscreen and w.NativeWindowHandle != window.NativeWindowHandle
                and not screen.is_protected(w)]
    except Exception:
        tops = []
    inner = []
    try:
        for ctrl, _ in auto.WalkControl(window, maxDepth=3):
            if ctrl.ControlTypeName == "WindowControl" and (ctrl.Name or "").strip() and not ctrl.IsOffscreen:
                inner.append(ctrl)
    except Exception:
        pass
    for d in tops + inner:
        try:
            buttons = [c.Name.strip() for c, _ in auto.WalkControl(d, maxDepth=6)
                       if c.ControlTypeName == "ButtonControl" and (c.Name or "").strip()][:6]
            texts = [c.Name.strip() for c, _ in auto.WalkControl(d, maxDepth=6)
                     if c.ControlTypeName == "TextControl" and (c.Name or "").strip()][:3]
        except Exception:
            continue
        line = f'"{d.Name.strip()}"'
        if texts:
            line += f" says: {' '.join(texts)[:200]}"
        if buttons:
            line += f" (buttons: {', '.join(buttons)})"
        if line not in found:
            found.append(line)
    return found[:3]


def images(window, limit: int = 8) -> list[str]:
    """Names of the pictures in the window; an empty name means an unlabeled picture."""
    out = []
    try:
        for ctrl, _ in auto.WalkControl(screen.page_root(window), maxDepth=25):
            if ctrl.ControlTypeName == "ImageControl" and not ctrl.IsOffscreen:
                out.append((ctrl.Name or "").strip())
                if len(out) >= limit:
                    break
    except Exception:
        pass
    return out


def focused() -> str:
    try:
        c = auto.GetFocusedControl()
        if screen.is_protected(c.GetTopLevelControl()):
            return ""
        kind = c.ControlTypeName.removesuffix("Control")
        if screen._is_password(c):
            return "a password field"
        return f"{kind} '{c.Name.strip()}'" if (c.Name or "").strip() else f"a {kind.lower()}"
    except Exception:
        return ""


def describe(window, question: str, emit=None) -> str:
    """Claude vision's description of the window, for pictures the tree cannot describe."""
    if not screen.bring_to_front(window):     # first, so the overlay is still hidden when we grab
        return "I could not see that window: another one is covering it."
    if emit:                                  # our own overlay must not end up in the screenshot
        emit({"kind": "overlay_hide", "seconds": 1.5})
        time.sleep(0.12)
    try:
        png, _ = screen.capture(window)
    except screen.NotOnTop:
        return "I could not see that window: another one is covering it."
    act, _ = vision.backend().ask(png, DESCRIBE_GOAL.format(question=question))
    return (act.get("say") or "").strip() or "I could not make out the pictures."


def overview(window, detail: str = "brief", question: str = "", emit=None) -> str:
    """Facts for the brain to speak from. See the module docstring for the detail levels."""
    screen.guard(window)                      # raises Protected for a do-not-touch app
    title = (window.Name or "").strip()
    lines = [f"Window: {title}"]
    pops = popups(window)
    if pops:
        lines.append("Pop-ups open (mention these first): " + "; ".join(pops))
    if detail == "lost":
        lines.append(f"Focused: {focused() or 'nothing'}")
        lines.append("Other windows the user could go back to: " + "; ".join(
            t for t in screen.open_windows()[:10] if t != title))
        return "\n".join(lines)
    snap = screen.snapshot(window)
    if detail == "actions":
        shown = [e for e in snap.elements.values() if e.name and not e.offscreen]
        lines.append("Controls (most useful are usually near the top): " + "; ".join(
            ("password field" if e.password else f"{e.kind} '{e.name}'") for e in shown[:40]))
        if snap.unlabeled:
            lines.append(f"{snap.unlabeled} controls have no label (vision can still find them).")
        return "\n".join(lines)
    if detail == "pictures":
        pics = images(window)
        if pics:
            named = [p for p in pics if p]
            lines.append(f"{len(pics)} pictures; labels: {', '.join(named) or 'none'}")
        lines.append("What the pictures show: " + describe(
            window, question or "What do the photos and pictures in this window show?", emit))
        return "\n".join(lines)
    lines.append("Text: " + " | ".join(snap.texts[:25])[:1800])
    pics = images(window)
    if pics:
        lines.append(f"There are {len(pics)} pictures (describe_screen with detail pictures says what they show).")
    f = focused()
    if f:
        lines.append(f"Focused: {f}")
    return "\n".join(lines)
