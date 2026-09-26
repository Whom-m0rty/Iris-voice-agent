import sys, time, json
import screen
from agent import Agent
w = screen.find_window(sys.argv[1]); w.SetActive()
plan = json.loads(sys.argv[3])
a = Agent(say=lambda s: print("[say]", s), confirm=lambda q: input(f"[ask] {q}\n> "))
t = time.perf_counter(); r = a.run_plan(sys.argv[2], plan, w)
for s in r.steps: print(f"  {s.via:6} {s.action} {s.target} ({s.ms:.0f} ms)")
print("[say]", r.summary, f"total {time.perf_counter()-t:.1f}s")
