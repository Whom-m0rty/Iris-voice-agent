"""Fake Voice Agent API server for offline tests of client.py: plays a scripted
conversation and prints what the client sends (audio frames are only counted)."""
import asyncio, json, os, sys
import websockets

PLAN = {"goal": "compute 12 times 3", "window": "Calculator|Калькулятор",
        "steps": [{"do": "press the digit 1"}, {"do": "press the digit 2"}, {"do": "press multiply"},
                  {"do": "press the digit 3"}, {"do": "press equals"}]}


ANSWERS = sys.argv[1:] or ["uh, yes go ahead"]


async def handler(ws):
    audio = 0
    got = []

    async def reader():
        nonlocal audio
        async for raw in ws:
            m = json.loads(raw)
            if m["type"] == "input.audio":
                audio += 1
                continue
            got.append(m)
            print("CLIENT ->", json.dumps(m, ensure_ascii=False)[:160], flush=True)
            if m["type"] == "reply.create":      # pretend to speak, then finish
                await ws.send(json.dumps({"type": "reply.started", "reply_id": "r"}))
                await asyncio.sleep(0.3)
                await ws.send(json.dumps({"type": "transcript.agent", "text": "(spoken) " + m.get("instructions", "")[:60]}))
                await ws.send(json.dumps({"type": "reply.done", "reply_id": "r", "status": "completed"}))
                if m.get("instructions", "").startswith("Ask the user"):
                    await asyncio.sleep(0.5)
                    for said in ANSWERS:           # scripted replies to the confirmation question
                        await ws.send(json.dumps({"type": "transcript.user", "text": said}))
                        await asyncio.sleep(1.5)

    r = asyncio.ensure_future(reader())
    await asyncio.sleep(0.5)
    await ws.send(json.dumps({"type": "session.ready", "session_id": "mock"}))
    await asyncio.sleep(0.5)
    await ws.send(json.dumps({"type": "transcript.user", "text": "What is twelve times three?"}))
    await ws.send(json.dumps({"type": "reply.started", "reply_id": "r0"}))
    if os.environ.get("MOCK_SCENARIO") == "mcp":
        await ws.send(json.dumps({"type": "tool.call", "call_id": "c1", "name": "echo__lookup",
                                  "arguments": {"word": "voice"}}))
        await ws.send(json.dumps({"type": "reply.done", "reply_id": "r0", "status": "completed"}))
        await asyncio.sleep(1)
        await ws.send(json.dumps({"type": "reply.started", "reply_id": "r1"}))
        await ws.send(json.dumps({"type": "tool.call", "call_id": "c2", "name": "echo__wipe",
                                  "arguments": {"target": "old photos"}}))
    else:
        await ws.send(json.dumps({"type": "tool.call", "call_id": "c1", "name": "do_task", "arguments": PLAN}))
    await asyncio.sleep(0.2)
    await ws.send(json.dumps({"type": "reply.done", "reply_id": "r0", "status": "completed"}))
    await asyncio.sleep(12)
    print(f"MOCK: audio frames received: {audio}", flush=True)
    r.cancel()
    await ws.close()
    return


async def main():
    async with websockets.serve(handler, "127.0.0.1", 8765):
        await asyncio.sleep(25)

asyncio.run(main())
