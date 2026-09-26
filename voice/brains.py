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
import time
import urllib.error
import urllib.request

GATEWAY = "https://llm-gateway.assemblyai.com/v1"


def extract_json(text: str) -> dict:
    start = (text or "").find("{")
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0] if start >= 0 else {"say": (text or "").strip(), "tool": None}
    except ValueError:
        return {"say": "", "tool": None}


class OpenAICompatibleBrain:
    """Keeps the conversation itself (the endpoint is stateless) and trims it to the last turns."""

    def __init__(self, prompt: str):
        self.base = os.environ.get("BRAIN_BASE_URL", GATEWAY).rstrip("/")
        self.key = os.environ.get("BRAIN_API_KEY") or os.environ.get("ASSEMBLYAI_API_KEY", "")
        self.model = os.environ.get("BRAIN_MODEL", "qwen3.5-4b-32k-fast")
        self.prompt = prompt
        self.history: list[dict] = []

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
        for attempt in range(4):              # free tiers rate-limit (HTTP 429): back off and retry
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    reply = json.load(r)["choices"][0]["message"]["content"] or ""
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        self.history.append({"role": "assistant", "content": reply})
        return extract_json(reply), (time.perf_counter() - t) * 1000
