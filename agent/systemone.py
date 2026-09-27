"""Client for the /v1/systemone decision API (Kev locally, Jev in the cloud)."""
import json

import env  # noqa: F401  (loads ../.env)
import os
import time
import urllib.request

import cloud

_target: tuple[str, str, str] | None = None


def target() -> tuple[str, str, str]:
    """(url, key, model), chosen once: local Kev, Jev, or Iris Cloud (see cloud.py)."""
    global _target
    if _target is None:
        base, key, model = cloud.decisions()
        _target = (base.rstrip("/") + "/v1/systemone", key, model)
    return _target


def ask(state: str, questions: dict) -> tuple[dict, float]:
    """Returns (answers, wall_ms)."""
    url, key, model = target()
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
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
