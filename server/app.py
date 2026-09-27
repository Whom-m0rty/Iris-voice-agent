"""Iris Cloud: lets Iris run with no keys on the user's PC.

The PC gets an anonymous device token and, through it:
  GET  /v1/stt-token          a short-lived AssemblyAI streaming token (audio goes straight to
                              AssemblyAI; our key never leaves this server)
  POST /v1/chat/completions   the brain: AssemblyAI LLM Gateway, OpenAI-compatible
  POST /v1/systemone          decisions: Jev by TypeSafe
Per-device daily limits keep one install from burning the keys. Nothing a user says or sees
is stored: only counters.

Env: ASSEMBLYAI_API_KEY, JEV_KEY, optional BRAIN_MODEL, IRIS_DB, limits below.
Run:  uvicorn app:app --host 127.0.0.1 --port 8080
"""
import os
import secrets
import sqlite3
import threading
import time

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

AAI_KEY = os.environ["ASSEMBLYAI_API_KEY"]
JEV_KEY = os.environ.get("JEV_KEY", "")
BRAIN_MODEL = os.environ.get("BRAIN_MODEL", "qwen3.5-4b-32k-fast")
GATEWAY = "https://llm-gateway.assemblyai.com/v1/chat/completions"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
STT_TOKEN_URL = "https://streaming.assemblyai.com/v3/token"

LIMITS = {                                  # per device per day
    "stt": int(os.environ.get("LIMIT_STT", 40)),         # voice sessions (1 h max each)
    "brain": int(os.environ.get("LIMIT_BRAIN", 600)),
    "decide": int(os.environ.get("LIMIT_DECIDE", 3000)),
}
DEVICES_PER_IP = int(os.environ.get("LIMIT_DEVICES_PER_IP", 5))

db = sqlite3.connect(os.environ.get("IRIS_DB", "iris_cloud.db"), check_same_thread=False)
db.execute("create table if not exists devices (token text primary key, ip text, created real)")
db.execute("create table if not exists usage (token text, day text, kind text, n integer, "
           "primary key (token, day, kind))")
db.commit()
lock = threading.Lock()
http = httpx.AsyncClient(timeout=60)
app = FastAPI(title="Iris Cloud", docs_url=None, redoc_url=None)


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _client_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()


def _spend(authorization: str | None, kind: str) -> str:
    token = (authorization or "").removeprefix("Bearer ").strip()
    with lock:
        if not token or not db.execute("select 1 from devices where token=?", (token,)).fetchone():
            raise HTTPException(401, "unknown device; POST /v1/device first")
        row = db.execute("select n from usage where token=? and day=? and kind=?", (token, _day(), kind)).fetchone()
        n = row[0] if row else 0
        if n >= LIMITS[kind]:
            raise HTTPException(429, f"daily {kind} limit reached ({LIMITS[kind]}); it resets at 00:00 UTC. "
                                     "Use your own keys for more (see README).")
        db.execute("insert into usage values (?,?,?,1) on conflict(token, day, kind) do update set n=n+1",
                   (token, _day(), kind))
        db.commit()
    return token


@app.get("/health")
async def health():
    return {"ok": True, "brain": BRAIN_MODEL, "decisions": "jev" if JEV_KEY else None}


@app.post("/v1/device")
async def device(request: Request):
    ip = _client_ip(request)
    with lock:
        n = db.execute("select count(*) from devices where ip=? and created>?", (ip, time.time() - 86400)).fetchone()[0]
        if n >= DEVICES_PER_IP:
            raise HTTPException(429, "too many new devices from this address today")
        token = "iris_" + secrets.token_urlsafe(24)
        db.execute("insert into devices values (?,?,?)", (token, ip, time.time()))
        db.commit()
    return {"token": token, "limits": LIMITS}


@app.get("/v1/stt-token")
async def stt_token(authorization: str | None = Header(None)):
    _spend(authorization, "stt")
    r = await http.get(STT_TOKEN_URL, params={"expires_in_seconds": 600, "max_session_duration_seconds": 3600},
                       headers={"Authorization": AAI_KEY})
    if r.status_code != 200:
        raise HTTPException(502, f"speech service: {r.status_code}")
    return {"token": r.json()["token"]}


@app.post("/v1/chat/completions")
async def chat(request: Request, authorization: str | None = Header(None)):
    _spend(authorization, "brain")
    body = await request.json()
    body["model"] = BRAIN_MODEL              # the cloud decides the model; users with keys pick their own
    body["max_tokens"] = min(int(body.get("max_tokens") or 700), 1200)
    for attempt in range(4):
        r = await http.post(GATEWAY, json=body, headers={"Authorization": AAI_KEY})
        if r.status_code != 429:
            break
        await _sleep(1.5 * (attempt + 1))
    return JSONResponse(r.json() if r.headers.get("content-type", "").startswith("application/json")
                        else {"error": r.text[:300]}, status_code=r.status_code,
                        headers={"Retry-After": r.headers["Retry-After"]} if "Retry-After" in r.headers else None)


@app.post("/v1/systemone")
async def systemone(request: Request, authorization: str | None = Header(None)):
    _spend(authorization, "decide")
    if not JEV_KEY:
        raise HTTPException(503, "decisions are not configured on this server")
    body = await request.json()
    body["model"] = "jev-latest"
    r = await http.post(JEV_URL, json=body, headers={"Authorization": f"Bearer {JEV_KEY}"})
    return JSONResponse(r.json() if r.headers.get("content-type", "").startswith("application/json")
                        else {"error": r.text[:300]}, status_code=r.status_code)


async def _sleep(s: float):
    import asyncio
    await asyncio.sleep(s)
