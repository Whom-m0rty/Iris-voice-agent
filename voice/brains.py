"""Brains Iris can run on, all with one contract: ask(text) -> (dict, ms), where the dict is
{"say": ..., "tool": ...} parsed from the model's JSON reply. Pick one in .env:

  BRAIN_BACKEND=cli       (default) Claude through a local Claude Code login, `claude -p`
  BRAIN_BACKEND=api       Claude through the Anthropic API (ANTHROPIC_API_KEY)
  BRAIN_BACKEND=openai    any OpenAI-compatible chat endpoint:
      BRAIN_BASE_URL  default https://llm-gateway.assemblyai.com/v1  (AssemblyAI LLM Gateway:
                      Claude, GPT, Gemini, Qwen, DeepSeek... as your AssemblyAI plan allows)
      BRAIN_API_KEY   default ASSEMBLYAI_API_KEY
      BRAIN_MODEL     e.g. qwen3.5-4b-32k-fast, gpt-5-mini, gemini-3.5-flash, claude-sonnet-4-6
  (Ollama, vLLM, LM Studio and OpenAI itself speak the same protocol: point BRAIN_BASE_URL at them.)
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

GATEWAY = "https://llm-gateway.assemblyai.com/v1"


def extract_json(text: str) -> dict:
    """The model's JSON reply. Small models drop a closing brace or wrap the JSON in prose,
    so close it and try again, and at worst keep the "say" text rather than go silent."""
    text = text or ""
    start = text.find("{")
    if start < 0:
        return {"say": text.strip(), "tool": None}
    body = text[start:]
    for extra in range(4):
        try:
            out = json.JSONDecoder().raw_decode(body + "}" * extra)[0]
            return out if isinstance(out, dict) else {"say": str(out), "tool": None}
        except ValueError:
            continue
    say = re.search(r'"say"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
    return {"say": json.loads(f'"{say.group(1)}"') if say else "", "tool": None}


def normalize_tool(tool) -> dict | None:
    """{"name": ..., "args": {...}} from whatever the model sent: a bare tool name, or the
    arguments next to the name instead of under "args"."""
    if not tool:
        return None
    if isinstance(tool, str):
        return {"name": tool, "args": {}}
    if not isinstance(tool, dict) or not tool.get("name"):
        return None
    args = tool.get("args")
    if not isinstance(args, dict):
        args = {k: v for k, v in tool.items() if k not in ("name", "args")}
    return {"name": str(tool["name"]), "args": args}


class OpenAICompatibleBrain:
    """Keeps the conversation itself (the endpoint is stateless) and trims it to the last turns."""

    def __init__(self, prompt: str, base: str | None = None, key: str | None = None):
        self.base = (base or os.environ.get("BRAIN_BASE_URL", GATEWAY)).rstrip("/")
        self.key = key or os.environ.get("BRAIN_API_KEY") or os.environ.get("ASSEMBLYAI_API_KEY", "")
        self.model = os.environ.get("BRAIN_MODEL", "qwen3.5-4b-32k-fast")
        self.prompt = prompt
        self.history: list[dict] = []
        self.on_wait = None                   # called on a rate limit, so the app can tell the user

    def warm(self):
        pass

    def ask(self, text: str) -> tuple[dict, float]:
        self.history.append({"role": "user", "content": text})
        body = {"model": self.model, "max_tokens": 700,
                "messages": [{"role": "system", "content": self.prompt}] + self.history[-30:]}
        # the AssemblyAI gateway takes the bare key; OpenAI-style servers want "Bearer <key>"
        auth = self.key if "assemblyai.com" in self.base else f"Bearer {self.key}"
        req = urllib.request.Request(f"{self.base}/chat/completions", json.dumps(body).encode(),
                                     {"Authorization": auth, "Content-Type": "application/json"})
        t = time.perf_counter()
        waited = 0.0
        while True:                           # free tiers rate-limit (HTTP 429): back off and retry
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    reply = json.load(r)["choices"][0]["message"]["content"] or ""
                break
            except urllib.error.HTTPError as e:
                if e.code not in (429, 503) or waited >= 20:
                    self.history.pop()        # the turn did not happen; don't leave it dangling
                    raise
                if self.on_wait and waited == 0:
                    self.on_wait()
                try:
                    pause = float(e.headers.get("Retry-After") or 0)
                except ValueError:
                    pause = 0
                pause = min(max(pause, 1.5 + waited / 2), 20 - waited)
                time.sleep(pause)
                waited += pause
        self.history.append({"role": "assistant", "content": reply})
        return extract_json(reply), (time.perf_counter() - t) * 1000
