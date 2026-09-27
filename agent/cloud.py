"""Which brain, eyes and decisions Iris uses, and Iris Cloud for people with no keys.

One line in .env picks the brain; the rest follows:

  IRIS_BRAIN=cloud        (default) no keys needed: Qwen on the AssemblyAI LLM Gateway, Jev
                          decisions and AssemblyAI speech through Iris Cloud; no vision
  IRIS_BRAIN=claude-code  your Claude Code login (`claude -p`, personal use); Claude vision
  IRIS_BRAIN=anthropic    your ANTHROPIC_API_KEY; Claude vision
  IRIS_BRAIN=gateway      your ASSEMBLYAI_API_KEY on the LLM Gateway (BRAIN_MODEL picks the model)
  IRIS_BRAIN=openai       any OpenAI-compatible endpoint: BRAIN_BASE_URL, BRAIN_API_KEY, BRAIN_MODEL

Decisions (IRIS_DECISIONS): auto (default: local Kev if it is running, else the cloud),
kev, jev (your JEV_KEY), cloud. SYSTEMONE_URL overrides all of it.
Speech: your ASSEMBLYAI_API_KEY if set, else a short-lived token from Iris Cloud.
"""
import json
import os
import socket
import urllib.request

import env  # noqa: F401  (loads ../.env)

CLOUD_URL = os.environ.get("IRIS_CLOUD_URL", "https://80-225-83-158.sslip.io").rstrip("/")
_LEGACY = {"cli": "claude-code", "api": "anthropic", "openai": "openai"}
_token: str | None = None


def brain_mode() -> str:
    mode = os.environ.get("IRIS_BRAIN", "").strip().lower()
    if not mode and os.environ.get("BRAIN_BACKEND"):
        mode = _LEGACY.get(os.environ["BRAIN_BACKEND"].strip().lower(), "")
    return mode or "cloud"


def vision_mode() -> str:
    """cli, api or none. Claude looks at the screen only on the user's own Claude."""
    explicit = os.environ.get("VISION_BACKEND", "").strip().lower()
    if explicit:
        return explicit
    return {"claude-code": "cli", "anthropic": "api"}.get(brain_mode(), "none")


def _state_file() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Iris", "device.json")


def device_token() -> str:
    """An anonymous id for this PC, made on first use. Iris Cloud counts daily use per id."""
    global _token
    if _token:
        return _token
    path = _state_file()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("cloud") == CLOUD_URL and data.get("token"):
            _token = data["token"]
            return _token
    except (OSError, ValueError):
        pass
    req = urllib.request.Request(f"{CLOUD_URL}/v1/device", b"{}", {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        _token = json.load(r)["token"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cloud": CLOUD_URL, "token": _token}, f)
    return _token


def stt_token() -> str:
    req = urllib.request.Request(f"{CLOUD_URL}/v1/stt-token", headers={"Authorization": f"Bearer {device_token()}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["token"]


def _kev_running(host: str = "127.0.0.1", port: int = 8009) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def decisions() -> tuple[str, str, str]:
    """(base url, bearer key, model) for the /v1/systemone decision API."""
    if os.environ.get("SYSTEMONE_URL"):
        return (os.environ["SYSTEMONE_URL"], os.environ.get("SYSTEMONE_KEY", ""),
                os.environ.get("SYSTEMONE_MODEL", "kev-latest"))
    mode = os.environ.get("IRIS_DECISIONS", "auto").strip().lower()
    if mode == "auto":
        mode = "kev" if _kev_running() else "cloud"
    if mode == "kev":
        return "http://127.0.0.1:8009", "", "kev-latest"
    if mode == "jev":
        return "https://api.typesafe.ai", os.environ.get("JEV_KEY", ""), "jev-latest"
    return CLOUD_URL, device_token(), "jev-latest"


def explain_error(e: Exception) -> str:
    """What to tell a person when Iris Cloud says no: a limit, or no connection."""
    import urllib.error
    detail = ""
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = json.loads(e.read() or b"{}").get("detail", "")
        except Exception:
            detail = ""
        if e.code == 429:
            if "device" in detail:
                return ("Iris Cloud has seen too many new computers from this network today, so I can't "
                        "start. Please try again tomorrow, or use your own key in the settings.")
            return ("I've reached today's free limit on Iris Cloud. It resets overnight, "
                    "or you can add your own key in the settings.")
        return f"Iris Cloud answered with an error, {e.code}. Please try again in a minute."
    return "I can't reach Iris Cloud. Please check the internet connection and start me again."


def describe() -> str:
    url, _, model = decisions()
    return f"brain={brain_mode()} vision={vision_mode()} decisions={model}@{url}"
