"""Vision fallback: Claude looks at the pixels when the accessibility tree is not enough.

Two backends, picked by VISION_BACKEND:
  cli (default) - a warm headless Claude Code session (`claude -p`), uses the local
                  Claude Code login. For local testing on your own machine only.
  api           - Anthropic API with ANTHROPIC_API_KEY. Use this for any shared deployment.
"""
import json

import env  # noqa: F401  (loads ../.env)
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

MODEL = os.environ.get("VISION_MODEL", "sonnet")

SYSTEM = """You operate a Windows app for a blind user. You get a screenshot of one window and a goal.
Reply with ONE JSON object and nothing else:
{"action": "click" | "type" | "scroll" | "done" | "blocked",
 "x": int, "y": int,            // pixel in the screenshot, for click, type and scroll
 "direction": "up" | "down",    // for scroll only: when the control you need is not visible
 "text": str,                   // for type only
 "target": str,                 // short name of what you click, e.g. "the blue Install button"
 "say": str}                    // one short sentence to speak to the user, in English
Pick the single next step toward the goal. "done" if the goal is already reached, "blocked" if impossible."""


def _parse(text: str) -> dict:
    start = text.find("{")
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return {"action": "blocked", "say": "I could not read the screen."}


def _content(png_b64: str, goal: str) -> list:
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}},
            {"type": "text", "text": f"Goal: {goal}"}]


class ClaudeCLI:
    """One long-lived `claude -p` process in stream-json mode, so each call skips CLI startup.
    Restarted every `max_calls` to keep screenshots from piling up in its context."""

    def __init__(self, max_calls: int = 8):
        self.max_calls = max_calls
        self.proc = None
        self.spare = None
        self.calls = 0
        self.lock = threading.Lock()

    def _new_process(self):
        # a multi-line prompt does not survive cmd.exe quoting, so it goes in a file
        prompt_file = Path(tempfile.gettempdir()) / "voice_agent_vision_prompt.txt"
        prompt_file.write_text(SYSTEM, encoding="utf-8")
        cmd = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--tools", "", "--model", MODEL, "--no-session-persistence",
               "--system-prompt-file", str(prompt_file)]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                     shell=True)

    def _spawn(self):
        self.proc = self._new_process()
        self.calls = 0

    def warm(self):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._spawn()

    def ask(self, png_b64: str, goal: str) -> tuple[dict, float]:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None or self.calls >= self.max_calls:
                if self.proc and self.proc.poll() is None:
                    self.proc.stdin.close()
                if self.spare and self.spare.poll() is None:   # a fresh process started in advance
                    self.proc, self.spare, self.calls = self.spare, None, 0
                else:
                    self._spawn()
            t = time.perf_counter()
            msg = {"type": "user", "message": {"role": "user", "content": _content(png_b64, goal)}}
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            self.calls += 1
            if self.calls == self.max_calls - 1 and self.spare is None:
                self.spare = self._new_process()      # warm the next one while this one works
            for line in self.proc.stdout:
                ev = json.loads(line)
                if ev.get("type") == "result":
                    if ev.get("is_error") or "API Error" in (ev.get("result") or ""):
                        raise RuntimeError(ev.get("result"))
                    return _parse(ev["result"]), (time.perf_counter() - t) * 1000
            raise RuntimeError("claude CLI exited")


class ClaudeAPI:
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = os.environ.get("VISION_API_MODEL", "claude-sonnet-5")

    def warm(self):
        pass

    def ask(self, png_b64: str, goal: str) -> tuple[dict, float]:
        t = time.perf_counter()
        r = self.client.messages.create(model=self.model, max_tokens=400, system=SYSTEM,
                                        messages=[{"role": "user", "content": _content(png_b64, goal)}])
        return _parse(r.content[0].text), (time.perf_counter() - t) * 1000


class NoVision:
    """No Claude configured (the default cloud mode): the screen agent works from the
    accessibility tree and the keyboard, and says so when it would need to look."""

    def warm(self):
        pass

    def ask(self, png_b64: str, goal: str):
        raise RuntimeError("vision is off: set IRIS_BRAIN=claude-code or anthropic to let Iris look at the screen")


_shared = None


def backend():
    """One shared backend per process, so the warm `claude -p` session is reused across tasks."""
    global _shared
    if _shared is None:
        import cloud
        mode = cloud.vision_mode()
        _shared = ClaudeAPI() if mode == "api" else ClaudeCLI() if mode == "cli" else NoVision()
    return _shared
