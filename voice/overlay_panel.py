"""Observer panel drawn inside the overlay: always on top of every app, click-through.
Same content as panel.html: status, current task and its steps with who did each one,
the pending confirmation, the last lines of the conversation, and the running numbers."""
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QConicalGradient, QFont, QFontMetrics, QPainter, QPen

WIDTH = 430
BG = QColor(15, 16, 20, 232)
CARD = QColor(26, 27, 33, 240)
LINE = QColor(44, 46, 58)
TEXT = QColor(242, 242, 245)
MUTED = QColor(154, 156, 171)
BADGE = {"kev": QColor(46, 204, 113), "vision": QColor(155, 89, 255), "mcp": QColor(52, 152, 219),
         "confirm": QColor(231, 76, 60), "plan": QColor(60, 62, 76), "keys": QColor(46, 204, 113)}


class PanelModel:
    def __init__(self):
        self.reset()

    def reset(self):
        self.status, self.status_kind = "listening", "live"
        self.goal = ""
        self.steps: list[tuple[str, str, str]] = []      # (badge kind, badge text, text)
        self.planned: list[str] = []
        self.result, self.result_ok = "", True
        self.confirm_q, self.heard = "", ""
        self.chat: list[tuple[str, str]] = []            # (who, text)
        self.n = {"kev": 0, "vision": 0, "mcp": 0, "ask": 0, "no": 0}
        self.kev_ms: list[int] = []
        self.visible = False
        self.confirm_until = 0.0

    def on_event(self, ev: dict):
        k = ev.get("kind")
        if k == "ready":
            self.reset()
            self.visible = True
        elif k == "user" and ev.get("text"):
            self.chat.append(("You", ev["text"]))
        elif k == "agent" and ev.get("text"):
            self.chat.append(("Iris", ev["text"]))
        elif k == "task_start":
            self.goal = ev.get("goal") or ""
            self.steps, self.result = [], ""
            self.planned = [s.get("do", "") + (" (stored login)" if s.get("secret") else "")
                            for s in (ev.get("steps") or [])]
            self.status, self.status_kind = "working", "work"
        elif k == "step":
            if self.planned:
                self.planned.pop(0)
            if ev.get("via") == "vision":
                self.n["vision"] += 1
                badge = ("vision", f"Claude {ev.get('ms', 0) / 1000:.1f} s")
            else:
                self.n["kev"] += 1
                if ev.get("ms"):
                    self.kev_ms.append(ev["ms"])
                badge = ("kev", f"Kev {ev.get('ms', 0)} ms" if ev.get("ms") else "Kev · keys")
            self.steps.append((badge[0], badge[1], f"{ev.get('action', '')} {ev.get('target', '')}"))
        elif k == "confirm":
            self.n["ask"] += 1
            self.confirm_q, self.heard = ev.get("question", ""), "Listening for your answer…"
            self.confirm_until = 0.0
            self.status, self.status_kind = "waiting for your yes", "wait"
        elif k == "answer":
            self.heard = f"Heard: “{ev.get('said', '')}”"
            self.confirm_until = time.time() + 1.5
            self.status, self.status_kind = "working", "work"
        elif k == "mcp":
            self.n["mcp"] += 1
            ok = ev.get("ok")
            if ev.get("result") == "cancelled by user":
                self.n["no"] += 1
            ms = ev.get("ms")
            self.steps.append(("mcp" if ok else "confirm", f"API {ms} ms" if ms is not None else "API",
                               ev.get("tool", "").replace("__", " › ") + ("" if ok else " — not done")))
        elif k == "task_done":
            self.planned = []
            summary = str(ev.get("summary", "")).split(" Screen now shows:")[0]
            if "Cancelled" in summary or "NOT sent" in summary:
                self.n["no"] += 1
            self.result = summary + (f"  ({ev['seconds']} s)" if ev.get("seconds") else "")
            self.result_ok = bool(ev.get("ok"))
            self.confirm_q = ""
            self.status, self.status_kind = "listening", "live"
        elif k == "stop":
            self.status, self.status_kind = "stopping", "wait"
        self.chat = self.chat[-6:]
        self.steps = self.steps[-7:]


def _wrap(p: QPainter, rect: QRectF, text: str, font: QFont, color: QColor, max_lines: int) -> float:
    """Draw wrapped text limited to max_lines; returns the height used."""
    p.setFont(font)
    p.setPen(color)
    fm = QFontMetrics(font)
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if fm.horizontalAdvance(trial) <= rect.width():
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fm.elidedText(lines[-1] + " …", Qt.ElideRight, int(rect.width()))
    y = rect.top()
    for ln in lines:
        p.drawText(QRectF(rect.left(), y, rect.width(), fm.height()), Qt.AlignLeft | Qt.AlignVCenter, ln)
        y += fm.height()
    return y - rect.top()


def draw(p: QPainter, m: PanelModel, screen_w: float, screen_h: float, phase: float):
    if not m.visible:
        return
    x0 = screen_w - WIDTH
    p.setPen(Qt.NoPen)
    p.setBrush(BG)
    p.drawRect(QRectF(x0, 0, WIDTH, screen_h))
    pad, x, w = 16, x0 + 16, WIDTH - 32
    y = 16.0
    f_title = QFont("Segoe UI", 17, QFont.DemiBold)
    f_body = QFont("Segoe UI", 11)
    f_big = QFont("Segoe UI", 14, QFont.DemiBold)
    f_small = QFont("Segoe UI", 9)
    f_badge = QFont("Segoe UI", 9, QFont.Bold)

    # header: shimmering logo, name, status dot
    g = QConicalGradient(x + 17, y + 17, phase)
    for i, c in enumerate(["#D97757", "#F4A261", "#B388FF", "#7AA2F7", "#D97757"]):
        g.setColorAt(i / 4, QColor(c))
    p.setBrush(g)
    p.drawEllipse(QRectF(x, y, 34, 34))
    p.setFont(f_title)
    p.setPen(TEXT)
    p.drawText(QRectF(x + 44, y, 120, 34), Qt.AlignVCenter, "Iris")
    dot = {"live": BADGE["kev"], "work": QColor("#d97757"), "wait": BADGE["confirm"]}[m.status_kind]
    if m.status_kind != "live" and int(phase / 20) % 2:
        dot = QColor(dot.red(), dot.green(), dot.blue(), 110)
    p.setFont(f_body)
    sw = QFontMetrics(f_body).horizontalAdvance(m.status)
    p.setPen(MUTED)
    p.drawText(QRectF(x + w - sw, y, sw, 34), Qt.AlignVCenter, m.status)
    p.setPen(Qt.NoPen)
    p.setBrush(dot)
    p.drawEllipse(QRectF(x + w - sw - 18, y + 12, 10, 10))
    y += 50

    def card(top: float, height: float, border: QColor = LINE):
        p.setPen(QPen(border, 2 if border != LINE else 1))
        p.setBrush(CARD)
        p.drawRoundedRect(QRectF(x0 + 10, top, WIDTH - 20, height), 12, 12)

    # confirmation card
    if m.confirm_q and (not m.confirm_until or time.time() < m.confirm_until):
        pulse = 150 + int(105 * abs(((phase * 2) % 360) - 180) / 180)
        h = 36 + 22 * 3 + 26
        card(y, h, QColor(231, 76, 60, pulse))
        _wrap(p, QRectF(x + 6, y + 10, w - 12, 18), "WAITING FOR A SPOKEN YES", f_small, BADGE["confirm"], 1)
        used = _wrap(p, QRectF(x + 6, y + 32, w - 12, 66), m.confirm_q, f_big, TEXT, 3)
        _wrap(p, QRectF(x + 6, y + 36 + used, w - 12, 20), m.heard, f_body, MUTED, 1)
        y += h + 12

    # doing now: goal, finished steps with badges, planned steps, result
    rows = len(m.steps) + len(m.planned[:4])
    h = 40 + 44 + rows * 26 + (44 if m.result else 0)
    card(y, h)
    _wrap(p, QRectF(x + 6, y + 10, w, 18), "DOING NOW", f_small, MUTED, 1)
    used = _wrap(p, QRectF(x + 6, y + 30, w - 12, 44), m.goal or "Nothing yet — just ask.", f_big,
                 TEXT if m.goal else MUTED, 2)
    yy = y + 36 + max(used, 22)
    fm_b = QFontMetrics(f_badge)
    for kind, text, label in m.steps + [("plan", "planned", s) for s in m.planned[:4]]:
        bw = max(96, fm_b.horizontalAdvance(text) + 18)
        p.setPen(Qt.NoPen)
        p.setBrush(BADGE[kind])
        p.drawRoundedRect(QRectF(x + 6, yy + 2, bw, 20), 10, 10)
        p.setFont(f_badge)
        p.setPen(QColor(15, 16, 20) if kind == "kev" else (MUTED if kind == "plan" else TEXT))
        p.drawText(QRectF(x + 6, yy + 2, bw, 20), Qt.AlignCenter, text)
        _wrap(p, QRectF(x + 14 + bw, yy + 2, w - bw - 20, 20), label, f_body,
              MUTED if kind == "plan" else TEXT, 1)
        yy += 26
    if m.result:
        _wrap(p, QRectF(x + 6, yy + 6, w - 12, 40), ("✓ " if m.result_ok else "✕ ") + m.result, f_body,
              BADGE["kev"] if m.result_ok else BADGE["confirm"], 2)
    y += h + 12

    # conversation, newest last
    h = 36 + 6 * 44
    card(y, h)
    _wrap(p, QRectF(x + 6, y + 10, w, 18), "CONVERSATION", f_small, MUTED, 1)
    yy = y + 32
    for who, text in m.chat:
        _wrap(p, QRectF(x + 6, yy, w, 14), who, f_small, BADGE["kev"] if who == "Iris" else MUTED, 1)
        yy += 14 + _wrap(p, QRectF(x + 6, yy + 14, w - 12, 30), text, f_body, TEXT, 2) + 2
    y += h + 12

    # numbers
    avg = f"{sum(m.kev_ms) // len(m.kev_ms)} ms" if m.kev_ms else "–"
    cells = [(str(m.n["kev"]), "steps by Kev"), (str(m.n["vision"]), "by Claude vision"), (avg, "avg Kev"),
             (str(m.n["mcp"]), "API calls"), (str(m.n["ask"]), "confirmations"), (str(m.n["no"]), "cancelled")]
    card(y, 130)
    cw = (WIDTH - 20) / 3
    for i, (big, small) in enumerate(cells):
        cx, cy = x0 + 10 + (i % 3) * cw, y + 10 + (i // 3) * 58
        p.setFont(f_big)
        p.setPen(TEXT)
        p.drawText(QRectF(cx, cy, cw, 26), Qt.AlignCenter, big)
        p.setFont(f_small)
        p.setPen(MUTED)
        p.drawText(QRectF(cx, cy + 26, cw, 18), Qt.AlignCenter, small)
