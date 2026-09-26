"""Start Kev under several server configs and time the agent's real request shape.

Each config: start server, warm up, run 3 rounds of the Calculator step request,
report p50 latency and VRAM. Picks nothing itself - just prints the table."""
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
import screen  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
PY = os.path.join(ROOT, "kev", ".venv", "Scripts", "python.exe")
URL = "http://127.0.0.1:8009"

CONFIGS = {
    "fused, no graphs": {"KEV_CUDA_GRAPHS": "0"},
    "plain, no graphs": {"KEV_CUDA_GRAPHS": "0", "KEV_FUSED": "0"},
    "fused + graphs, cache 1": {"KEV_PREFIX_CACHE": "1"},
}


def vram() -> int:
    out = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
    return int(out.decode().strip())


def up() -> bool:
    try:
        urllib.request.urlopen(URL + "/docs", timeout=2)
        return True
    except Exception:
        return False


def post(body) -> float:
    req = urllib.request.Request(URL + "/v1/systemone", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t = time.perf_counter()
    urllib.request.urlopen(req, timeout=120).read()
    return (time.perf_counter() - t) * 1000


def requests():
    w = screen.find_window("Calculator|Калькулятор")
    s = screen.snapshot(w)
    crit = {k: e.label for k, e in s.elements.items()}
    crit["BLOCKED"] = "None of these elements can do the next step"
    out = []
    for step in ["press the digit 1", "press the digit 2", "press multiply", "press the digit 3", "press equals"]:
        out.append({"state": f"{s.state()} The user wants to: {step}. Already done: nothing yet.",
                    "model": "kev-latest",
                    "questions": {"target": {"type": "choice", "instructions": "Which element should be used next?",
                                             "criteria": crit}}})
    out.append({"state": "The user asked: x. Next action: click Button 'One'", "model": "kev-latest",
                "questions": {"r": {"type": "noul", "instructions": "Is this irreversible?"}}})
    return out


def main():
    reqs = requests()
    for name, env_over in CONFIGS.items():
        env = {**os.environ, "HF_HUB_DISABLE_SYMLINKS": "1", "PYTHONUNBUFFERED": "1", **env_over}
        log = open(os.path.join(ROOT, f"kev-serve-{len(name)}.log"), "w")
        proc = subprocess.Popen([PY, "-m", "kev.serve", "--run", "jaredpalmer/kev-4b", "--port", "8009"],
                                cwd=os.path.join(ROOT, "kev"), env=env, stdout=log, stderr=subprocess.STDOUT)
        t0 = time.time()
        while not up():
            if proc.poll() is not None or time.time() - t0 > 600:
                print(name, "failed to start")
                break
            time.sleep(2)
        else:
            load_s = time.time() - t0
            for r in reqs:  # warmup / compile
                post(r)
            ms = [post(r) for _ in range(3) for r in reqs]
            print(f"{name:26} load {load_s:5.0f}s  p50 {statistics.median(ms):6.0f} ms  "
                  f"max {max(ms):6.0f} ms  VRAM {vram()} MiB", flush=True)
        proc.kill()
        proc.wait()
        time.sleep(3)


if __name__ == "__main__":
    main()
