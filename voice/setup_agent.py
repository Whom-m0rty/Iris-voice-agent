"""Create the stored AssemblyAI agent whose brain is Claude (via AssemblyAI's LLM Gateway,
billed to the same AssemblyAI key) and save its id to voice-hack/.env as VOICE_AGENT_ID.
BYO-LLM config is only accepted on stored agents, not on session.update.

  python setup_agent.py [model]        # default claude-sonnet-4-6
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
import env  # noqa: E402

model = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-4-6"
key = os.environ["ASSEMBLYAI_API_KEY"]
body = {"name": f"iris-{model}", "system_prompt": "You are Iris.", "voice": {"voice_id": "ivy"},
        "llm": [{"base_url": "https://llm-gateway.assemblyai.com/v1", "model": model, "api_key": key}]}
req = urllib.request.Request("https://agents.assemblyai.com/v1/agents", json.dumps(body).encode(),
                             {"Authorization": key, "Content-Type": "application/json"}, method="POST")
agent_id = json.load(urllib.request.urlopen(req, timeout=30))["id"]

lines = [ln for ln in env.ENV_FILE.read_text(encoding="utf-8").splitlines() if not ln.startswith("VOICE_AGENT_ID=")]
env.ENV_FILE.write_text("\n".join(lines + [f"VOICE_AGENT_ID={agent_id}"]) + "\n", encoding="utf-8")
print("stored agent", agent_id, "model", model)
