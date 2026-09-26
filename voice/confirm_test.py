"""Offline test of the spoken-confirmation path: force the Equals click to need a "yes"."""
import asyncio, json, re, sys
sys.path.insert(0, "../agent")
import agent
agent.ALWAYS_CONFIRM = re.compile(r"Equals")
import client
asyncio.run(client.VoiceSession(emit=client.console_and_bus()).run())
