"""Client for the /v1/systemone decision API (Kev locally, Jev in the cloud)."""
import json

import env  # noqa: F401  (loads ../.env)
import os
import time
import urllib.request

URL = os.environ.get("SYSTEMONE_URL", "http://127.0.0.1:8009").rstrip("/") + "/v1/systemone"
KEY = os.environ.get("SYSTEMONE_KEY", "")
MODEL = os.environ.get("SYSTEMONE_MODEL", "kev-latest")


def ask(state: str, questions: dict) -> tuple[dict, float]:
    """Returns (answers, wall_ms)."""
    body = json.dumps({"state": state, "model": MODEL, "questions": questions}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    if KEY:
        req.add_header("Authorization", f"Bearer {KEY}")
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    return out["answers"], (time.perf_counter() - t) * 1000


def choice(state: str, instructions: str, criteria: dict) -> tuple[str, float, float]:
    a, ms = ask(state, {"q": {"type": "choice", "instructions": instructions, "criteria": criteria}})
    return a["q"]["choice"], a["q"].get("confidence", 0.0), ms


def noul(state: str, instructions: str) -> tuple[float, float]:
    a, ms = ask(state, {"q": {"type": "noul", "instructions": instructions}})
    return a["q"]["noul"], ms
