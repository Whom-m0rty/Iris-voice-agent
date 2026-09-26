"""Click accuracy of the vision fallback on small unlabeled targets (bench/click_targets.html).

The page is opened maximised in an isolated Edge window (like a maximised Telegram, where the
screenshot is scaled down). For every target Claude is asked once where it is; that one answer
is then clicked three ways and the page reports what was hit:

  raw     Claude's point as is
  snap    snapped to the UI Automation control under/near the point (30 px)
  refine  snap, or - when no control is there (canvas) - a zoomed second look

  python click_accuracy.py            # writes bench/results_click.json
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent"))
import screen  # noqa: E402
import vision  # noqa: E402
from agent import locate  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PAGE = "file:///" + os.path.join(HERE, "click_targets.html").replace("\\", "/")
COLOURS = {"#e74c3c": "red", "#3498db": "blue", "#27ae60": "green", "#f1c40f": "yellow",
           "#8e44ad": "purple", "#e67e22": "orange"}
TARGETS = [  # id, shape, colour, size, kind  (same as the page)
    ("b1", "circle", "#e74c3c", 14, "button"), ("b2", "square", "#3498db", 16, "button"),
    ("b3", "triangle", "#27ae60", 20, "button"), ("b4", "star", "#f1c40f", 22, "button"),
    ("b5", "ring", "#8e44ad", 18, "button"), ("b6", "circle", "#27ae60", 28, "button"),
    ("b7", "square", "#e67e22", 14, "button"), ("b8", "star", "#e74c3c", 40, "button"),
    ("c1", "circle", "#3498db", 16, "canvas"), ("c2", "triangle", "#e74c3c", 18, "canvas"),
    ("c3", "square", "#27ae60", 14, "canvas"), ("c4", "star", "#8e44ad", 20, "canvas"),
    ("c5", "ring", "#e67e22", 22, "canvas"), ("c6", "circle", "#f1c40f", 30, "canvas"),
]


def status(window) -> str:
    for t in screen.snapshot(window).texts:
        if re.match(r"^(button:|canvas:|miss|ready)", t):
            return t
    return "?"


def main():
    profile = os.path.join(tempfile.gettempdir(), "iris-click-bench")
    subprocess.Popen([EDGE, f"--app={PAGE}", f"--user-data-dir={profile}", "--no-first-run",
                      "--force-renderer-accessibility", "--start-maximized"])
    window = screen.find_window("Click Accuracy Range", 15)
    time.sleep(1.5)
    v = vision.backend()
    v.warm()
    rows = []
    for tid, shape, colour, size, kind in TARGETS:
        name = f"the small {COLOURS[colour]} {shape} icon"
        png, (left, top, scale) = screen.capture(window)
        act, ms = v.ask(png, f"Click {name}.")
        if act.get("action") != "click" or "x" not in act:
            rows.append({"id": tid, "kind": kind, "size": size, "answer": act.get("action")})
            print(tid, "no click answer:", act)
            continue
        sx, sy = int(left + act["x"] / scale), int(top + act["y"] / scale)
        row = {"id": tid, "kind": kind, "size": size, "scale": round(scale, 3), "vision_ms": round(ms)}
        for mode, snap, refine in (("raw", False, False), ("snap", True, False), ("refine", True, True)):
            t = time.perf_counter()
            where = ("point", sx, sy) if mode == "raw" else locate(v, window, sx, sy, name, snap, refine)
            if where[0] == "control":
                screen.click(where[1])
            else:
                screen.click_point(where[1], where[2], window)
            time.sleep(0.35)
            got = status(window)
            row[mode] = got.endswith(":" + tid)
            row[f"{mode}_ms"] = round((time.perf_counter() - t) * 1000)
            row[f"{mode}_got"] = got
        rows.append(row)
        print(f"{tid} {kind:6} {size:2}px  raw={row['raw']!s:5} snap={row['snap']!s:5} "
              f"refine={row['refine']!s:5}  ({row['raw_got']} / {row['snap_got']} / {row['refine_got']})")

    json.dump(rows, open(os.path.join(HERE, "results_click.json"), "w"), indent=1)
    done = [r for r in rows if "raw" in r]
    for kind in ("button", "canvas"):
        k = [r for r in done if r["kind"] == kind]
        if k:
            print(f"{kind:6}: raw {sum(r['raw'] for r in k)}/{len(k)}  snap {sum(r['snap'] for r in k)}/{len(k)}  "
                  f"refine {sum(r['refine'] for r in k)}/{len(k)}")
    subprocess.run(["taskkill", "/F", "/FI", "WINDOWTITLE eq Click Accuracy Range*"], capture_output=True)


if __name__ == "__main__":
    main()
