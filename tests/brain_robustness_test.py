"""The conversation survives what small models do: a tool sent as a bare string, a missing
closing brace, an empty reply, "let me check" with no tool, a failing endpoint, a screen task
that keeps failing, a name instead of an email address. Offline: no mic, no model, no screen.
    python tests/brain_robustness_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "voice"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
import iris  # noqa: E402
from brains import extract_json, normalize_tool  # noqa: E402

checks = []


def check(name, ok):
    checks.append((name, bool(ok)))


# ---- parsing ---------------------------------------------------------------------------------
check("missing brace", extract_json('{"say": "Hi", "tool": {"name": "x", "args": {}}')["say"] == "Hi")
check("prose around JSON", extract_json('Sure! {"say": "Ok", "tool": null} done')["say"] == "Ok")
check("broken JSON keeps say", extract_json('{"say": "Hello there", "tool": {"name": ')["say"] == "Hello there")
check("plain text", extract_json("Just words")["say"] == "Just words")
check("string tool", normalize_tool("mail__list_recent") == {"name": "mail__list_recent", "args": {}})
check("flat args", normalize_tool({"name": "do_task", "goal": "g"}) == {"name": "do_task", "args": {"goal": "g"}})
check("junk tool", normalize_tool(["x"]) is None and normalize_tool({"args": {}}) is None)


# ---- the turn loop ---------------------------------------------------------------------------
class FakeBrain:
    def __init__(self, replies):
        self.replies = list(replies)
        self.asked = []

    def ask(self, text):
        self.asked.append(text)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, 5.0


class Speaker:
    def chime(self, *_):
        pass


def fake_iris(replies, task_ok=True):
    ir = iris.Iris.__new__(iris.Iris)
    ir.brain = FakeBrain(replies)
    ir.spoken, ir.tools = [], []
    ir.say = lambda text: ir.spoken.append(text)
    ir.emit = lambda event: None
    ir.speaker = Speaker()
    ir._outcome_sound = lambda *a: None
    ir.last_ok = True

    def run_tool(name, args):
        ir.tools.append((name, args))
        if name == "do_task":
            ir.last_ok = task_ok
            return "Done." if task_ok else "Stopped at 'open chat': not found."
        return "ok"
    ir.run_tool = run_tool
    return ir


ir = fake_iris([{"say": "Checking.", "tool": "mail__list_recent"}, {"say": "Two emails.", "tool": None}])
ir._turn("any mail?")
check("string tool runs", ir.tools == [("mail__list_recent", {})] and "Two emails." in ir.spoken)

ir = fake_iris([{"say": "Let me check your mail.", "tool": None},
                {"say": "", "tool": {"name": "mail__list_recent", "args": {}}},
                {"say": "Nothing new.", "tool": None}])
ir._turn("any mail?")
check("promise without tool is nudged", ir.tools and "Nothing new." in ir.spoken)

ir = fake_iris([{"say": "", "tool": None}, {"say": "Hello!", "tool": None}])
ir._turn("hi")
check("empty reply is retried", "Hello!" in ir.spoken)

ir = fake_iris([{"say": "", "tool": None}, {"say": "", "tool": None}])
ir._turn("hi")
check("never silent", any("didn't catch" in s for s in ir.spoken))

ir = fake_iris([RuntimeError("429"), {"say": "Here.", "tool": None}])
ir._turn("hi")
check("brain error retried once", ir.spoken[:2] == ["One moment.", "Here."])

ir = fake_iris([RuntimeError("429"), RuntimeError("429")])
ir._turn("hi")
check("brain down: said so", any("can't think" in s for s in ir.spoken))

task = {"name": "do_task", "args": {"goal": "message Maksim", "steps": []}}
ir = fake_iris([{"say": "On it.", "tool": task}] * 5 + [{"say": "I couldn't open the chat.", "tool": None}],
               task_ok=False)
ir._turn("tell Maksim I'm late")
check("two failed tasks, then stop", len(ir.tools) == 2 and "failed twice" in ir.brain.asked[2])

# a whole turn blowing up must not kill the worker thread
ir = fake_iris([{"say": "x", "tool": None}])
ir.run_tool = None
ir._turn = lambda text: (_ for _ in ()).throw(ValueError("boom"))
import queue  # noqa: E402
ir.turns = queue.Queue()
ir.turns.put("hi")
ir.busy_since = None
import threading  # noqa: E402
threading.Thread(target=ir.converse, daemon=True).start()
import time  # noqa: E402
time.sleep(0.3)
check("turn crash is spoken, worker alive", any("went wrong" in s for s in ir.spoken))

for name, ok in checks:
    print("PASS" if ok else "FAIL", name)
sys.exit(0 if all(ok for _, ok in checks) else 1)
