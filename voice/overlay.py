"""Demo overlay: a large "ghost" cursor with a shimmering glow that glides to where the
agent acts, a ripple on each click, and a frame around the target coloured by who decided
(Kev = green, Claude vision = violet, waiting for a spoken yes = red). While the mic is
muted, a large red MUTED badge sits at the top of the screen.

It is only a visual: the real clicks go through UI Automation. The window covers the whole
desktop, stays on top and lets every mouse event through.

Run:  python overlay.py        (listens to ws://127.0.0.1:8770, see events.py)
"""
import json
import math
import os
import sys
import time

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")   # draw in physical pixels, like UI Automation

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QThread, QTimer, QVariantAnimation, Signal  # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QGuiApplication, QPainter,  # noqa: E402
                           QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

import overlay_panel  # noqa: E402

SHOW_PANEL = os.environ.get("OVERLAY_PANEL", "1") != "0"
SHOW_CURSOR = os.environ.get("OVERLAY_CURSOR", "1") != "0"   # 0: frames and clicks only, no ghost cursor


URL = "ws://127.0.0.1:8770"
GLIDE_MS = int(os.environ.get("CURSOR_GLIDE_MS", "450"))
SCALE = float(os.environ.get("CURSOR_SCALE", "3.4"))
IDLE_HIDE_S = 10
PANEL_IDLE_S = 120

VIA = {  # frame / ripple colour per decision path
    "kev": QColor(46, 204, 113),
    "vision": QColor(155, 89, 255),
    "confirm": QColor(231, 76, 60),
    "mcp": QColor(52, 152, 219),
}
# glow palette: Claude orange drifting through violet and blue
GLOW = [QColor("#D97757"), QColor("#F4A261"), QColor("#E76F51"), QColor("#B388FF"),
        QColor("#7AA2F7"), QColor("#D97757")]

# classic arrow cursor, tip at (0, 0), in 1x units
ARROW = [(0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12)]


class Listener(QThread):
    event = Signal(dict)

    def run(self):
        while True:
            try:
                with connect(URL, open_timeout=2) as ws:
                    for raw in ws:
                        self.event.emit(json.loads(raw))
            except Exception:
                time.sleep(1)                 # agent not up yet: keep retrying


class Overlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        geo = QGuiApplication.primaryScreen().virtualGeometry()
        self.setGeometry(geo)
        self.origin = QPointF(geo.x(), geo.y())

        self.pos = QPointF(geo.center())
        self.visible_cursor = False
        self.last_activity = 0.0
        self.phase = 0.0
        self.ripples: list[tuple[QPointF, float, QColor]] = []
        self.target: QRectF | None = None
        self.target_via = "kev"
        self.target_label = ""
        self.hidden_until = 0.0
        self.panel = overlay_panel.PanelModel()
        self.last_event = time.time()
        self.muted = False
        self.screen_top = QGuiApplication.primaryScreen().geometry()

        self.anim = QVariantAnimation(self, duration=GLIDE_MS, easingCurve=QEasingCurve.InOutCubic)
        self.anim.valueChanged.connect(self._moved)

        tick = QTimer(self, interval=16)      # ~60 fps for the shimmer and ripples
        tick.timeout.connect(self._tick)
        tick.start()

        self.listener = Listener()
        self.listener.event.connect(self.on_event)
        self.listener.start()

    # ---- events --------------------------------------------------------------------

    def _local(self, x, y) -> QPointF:
        return QPointF(x, y) - self.origin

    def glide_to(self, p: QPointF):
        if not self.visible_cursor:           # first appearance: fade in where it is going
            self.pos = p + QPointF(-120, 80)
            self.visible_cursor = True
        self.anim.stop()
        self.anim.setStartValue(self.pos)
        self.anim.setEndValue(p)
        self.anim.start()

    def on_event(self, ev: dict):
        kind = ev.get("kind")
        self.panel.on_event(ev)
        self.last_event = time.time()
        if kind in ("target", "cursor", "click", "overlay_hide"):
            self.last_activity = time.time()
        if kind in ("target", "cursor"):
            self.hidden_until = 0.0
        if kind == "target":
            l, t, r, b = ev["rect"]
            self.target = QRectF(self._local(l, t), self._local(r, b))
            self.target_via = ev.get("via", "kev")
            self.target_label = ev.get("label", "")
            self.glide_to(self.target.center())
        elif kind == "cursor":
            self.glide_to(self._local(ev["x"], ev["y"]))
        elif kind == "click":
            p = self._local(ev["x"], ev["y"])
            self.ripples.append((p, time.time(), VIA.get(ev.get("via", "kev"), VIA["kev"])))
        elif kind == "mute":
            self.muted = bool(ev.get("muted"))
        elif kind in ("target_clear", "task_done"):
            self.target = None
        elif kind == "overlay_hide":          # the agent is taking a screenshot for the model
            self.target = None
            self.ripples.clear()
            self.hidden_until = time.time() + float(ev.get("seconds", 1.0))
            self.repaint()                    # clear the screen now, not on the next tick

    def _moved(self, v):
        self.pos = v
        self.update()

    def _tick(self):
        self.phase = (self.phase + 2.2) % 360
        now = time.time()
        if self.panel.visible and now - self.last_event > PANEL_IDLE_S:
            self.panel.visible = False        # the session is over: give the screen back
        self.ripples = [r for r in self.ripples if now - r[1] < 0.7]
        if self.visible_cursor and now - self.last_activity > IDLE_HIDE_S and not self.anim.state():
            self.visible_cursor = False
            self.target = None
        self.update()

    # ---- drawing -------------------------------------------------------------------

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if time.time() < self.hidden_until:
            return
        if self.target is not None:
            self._draw_target(p)
        for center, born, color in self.ripples:
            self._draw_ripple(p, center, time.time() - born, color)
        if SHOW_PANEL:
            overlay_panel.draw(p, self.panel, self.width(), self.height(), self.phase)
        if self.visible_cursor and SHOW_CURSOR:
            self._draw_cursor(p)
        if self.muted:
            self._draw_muted(p)

    def _draw_muted(self, p: QPainter):
        text = "MUTED  ·  Iris can't hear you  ·  Ctrl+Alt+M"
        p.setFont(QFont("Segoe UI", 20, QFont.Bold))
        w = p.fontMetrics().horizontalAdvance(text) + 64
        top = self._local(self.screen_top.x(), self.screen_top.y())
        pill = QRectF(top.x() + (self.screen_top.width() - w) / 2, top.y() + 24, w, 60)
        pulse = 0.6 + 0.4 * math.sin(math.radians(self.phase * 2))
        glow = QColor(VIA["confirm"])
        glow.setAlpha(int(90 * pulse))
        p.setPen(QPen(glow, 12))
        p.setBrush(QColor(150, 30, 24, 235))
        p.drawRoundedRect(pill, 30, 30)
        p.setPen(QColor(255, 255, 255))
        p.drawText(pill, Qt.AlignCenter, text)

    def _draw_target(self, p: QPainter):
        color = QColor(VIA.get(self.target_via, VIA["kev"]))
        pulse = 0.55 + 0.45 * math.sin(math.radians(self.phase * 3))
        r = self.target.adjusted(-6, -6, 6, 6)
        for width, alpha in ((10, 40), (5, 90), (2.5, 255)):
            c = QColor(color)
            c.setAlpha(int(alpha * (pulse if width > 3 else 1)))
            p.setPen(QPen(c, width))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 10, 10)
        if self.target_label:
            name = {"kev": "Kev", "vision": "Claude vision", "confirm": "Waiting for your yes",
                    "mcp": "API"}.get(self.target_via, self.target_via)
            text = f"{name}  ·  {self.target_label}"
            f = QFont("Segoe UI", 12, QFont.DemiBold)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(text) + 24
            pill = QRectF(r.left(), r.top() - 38, w, 30)
            if pill.top() < 0:
                pill.moveTop(r.bottom() + 8)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 20, 24, 225))
            p.drawRoundedRect(pill, 15, 15)
            p.setPen(color)
            p.drawText(pill, Qt.AlignCenter, text)

    def _draw_ripple(self, p: QPainter, c: QPointF, age: float, color: QColor):
        k = age / 0.7
        radius = 14 + 76 * (1 - (1 - k) ** 3)
        col = QColor(color)
        col.setAlpha(int(255 * (1 - k)))
        p.setPen(QPen(col, 7 * (1 - k) + 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, radius, radius)

    def _arrow(self) -> QPainterPath:
        poly = QPolygonF([self.pos + QPointF(x * SCALE, y * SCALE) for x, y in ARROW])
        path = QPainterPath()
        path.addPolygon(poly)
        path.closeSubpath()
        return path

    def _draw_cursor(self, p: QPainter):
        path = self._arrow()
        center = path.boundingRect().center()
        grad = QConicalGradient(center, self.phase)
        for i, c in enumerate(GLOW):
            grad.setColorAt(i / (len(GLOW) - 1), c)
        # soft shimmering halo: the outline stroked wide and faint, then narrower and stronger
        for width, alpha in ((56, 30), (40, 55), (26, 95), (14, 170)):
            brush = QBrush(grad)
            pen = QPen(brush, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setOpacity(alpha / 255)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setOpacity(1.0)
        p.setPen(QPen(QColor(25, 25, 30), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor(255, 255, 255))
        p.drawPath(path)


if __name__ == "__main__":
    import ctypes
    import signal
    # one overlay only: two would draw two cursors
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex = k32.CreateMutexW(None, False, "IrisOverlaySingleton")
    if ctypes.get_last_error() == 183:                          # ERROR_ALREADY_EXISTS
        sys.exit(0)
    app = QApplication(sys.argv)
    w = Overlay()
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    w.show()
    sys.exit(app.exec())
