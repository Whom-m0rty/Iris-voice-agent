"""Screen agent: the fast decision model (Kev/Jev) drives the accessibility tree,
Claude vision takes over where the tree is blind, and anything irreversible waits
for a spoken "yes".

Text-mode test:
  python agent.py "compute 7 plus 8" --window "Calculator|Калькулятор"
"""
import argparse
import re
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import screen
import systemone
import vault
import vision

DONE_P = 0.7          # Kev must be this sure the goal is reached
MIN_PROB = 0.2        # probability of the picked element; below this it goes to vision
# safety gate thresholds (Kev, two questions in one request). Chosen on two labelled sets
# on 26.09, then frozen and checked once on a third set - see bench/bench_full.py
RISK_P1 = 0.35
RISK_P2 = 0.5
MAX_STEPS = 12
# the demo overlay's cursor needs this long to glide to a target before the click lands
GLIDE_S = float(os.environ.get("CURSOR_GLIDE_MS", "450")) / 1000
DEBUG = bool(os.environ.get("AGENT_DEBUG"))

# words that always need a spoken "yes", no model involved: acts that reach other people,
# money, accounts or saved data
ALWAYS_CONFIRM = re.compile(
    r"\b(send|pay|buy|purchase|order\b|checkout|delete|remove|erase|format\b|submit|confirm|transfer|"
    r"post\b|publish|share|call\b|accept|invite|block|report|leave|unsubscribe|book\b|reset|"
    r"discard|don'?t save|uninstall|sign out|log ?out|"
    r"отправ|оплат|купи|заказ|удал|стер|форматир|подтверд|перев[её]д|опубликов|поделит|позвон|"
    r"приня|заблок|не сохран|выйти)", re.I)

SEND_INTENT = re.compile(r"\b(send|post|submit|reply|отправ)", re.I)

PASSWORD_WORDS = re.compile(r"pass(word|code|phrase)|\bpin\b|парол|пин-?код", re.I)

RISK_Q = ("Is this action irreversible or does it send, pay, publish or delete something, "
          "so a blind user must confirm it out loud first?")
RISK_Q2 = ("Does this action send or share something with someone, spend money, start a call, change an "
           "account or subscription, or delete or change data? (Just opening, viewing, searching, "
           "scrolling or typing into a field is not.)")


def risk_check(description: str) -> tuple[bool, str]:
    """(needs a spoken yes?, why). Word list first, then Kev with two phrasings in one request."""
    if ALWAYS_CONFIRM.search(description):
        return True, "word list"
    a, _ = systemone.ask(f"Action about to happen on the user's computer: {description}.",
                         {"q1": {"type": "noul", "instructions": RISK_Q},
                          "q2": {"type": "noul", "instructions": RISK_Q2}})
    p1, p2 = a["q1"]["noul"], a["q2"]["noul"]
    return p1 >= RISK_P1 or p2 >= RISK_P2, f"kev p1={p1:.2f} p2={p2:.2f}"


@dataclass
class Step:
    via: str            # "kev" | "vision"
    action: str
    target: str
    ms: float
    note: str = ""


@dataclass
class Result:
    ok: bool
    summary: str
    steps: list[Step] = field(default_factory=list)


def interpret_confirmation(said: str, question: str) -> str:
    # Phrasing measured on 19 answers (26.09): the question has to sit in the state next to
    # the reply. "User said: ..." alone scored 6/19 and read "cancel that" as yes; this 18/19,
    # no false yes.
    choice, _conf, _ms = systemone.choice(
        f'The assistant asked the user for permission: "{question}" The user replied: "{said}".',
        "What does the user's reply mean?",
        {"yes": "The user agreed: go ahead",
         "no": "The user refused or wants to cancel",
         "unclear": "The user hesitated, asked a question, or changed their mind"})
    return choice


class Agent:
    def __init__(self, say: Callable[[str], None], confirm: Callable[[str], str]):
        """say(text): speak to the user. confirm(question) -> what the user answered."""
        self.say = say
        self.confirm = confirm
        self.vision = vision.backend()
        self._risk_cache: dict[tuple[str, str], bool] = {}
        self.cancel = threading.Event()       # set by "stop": checked before every action
        self._declined: str | None = None     # set when the user said no to a vision action
        self.on_event: Callable[[dict], None] | None = None   # visuals: overlay and panel
        self._typed = ""                      # last text typed in this plan, for the question

    # ---- visuals (no effect unless on_event is set) ------------------------------------

    def _emit(self, **event):
        if self.on_event:
            self.on_event(event)

    def _show_target(self, rect, via: str, label: str):
        """Frame the target and glide the demo cursor there before acting."""
        if not self.on_event:
            return
        self._emit(kind="target", rect=list(rect), via=via, label=label)
        time.sleep(GLIDE_S)

    def _record(self, steps: list, step: "Step") -> None:
        steps.append(step)
        self._emit(kind="step", via=step.via, action=step.action, target=step.target,
                   ms=round(step.ms), note=step.note)

    def _show_click(self, rect, via: str):
        if self.on_event:
            l, t, r, b = rect
            self._emit(kind="click", x=(l + r) // 2, y=(t + b) // 2, via=via)

    def _question(self, description: str) -> str:
        """What a person would ask: 'Send "running late" to Maksim Okulov?', not 'About to click Button'."""
        if self._typed and SEND_INTENT.search(f"{self.goal} {description}"):
            where = re.sub(r"[\u200e\u200f]|\s*[–-]\s*\(\d+\)\s*$|\s*\(\d+\)\s*$", "",
                           self._window_title or "").strip()
            to = f" to {where}" if where and not re.search(r"telegram|chrome|edge|mail", where, re.I) else ""
            return f'Send "{self._typed}"{to}?'
        return f"About to {description}. Go ahead?"

    def _approved(self, description: str) -> bool:
        question = self._question(description)
        for _ in range(3):
            answer = interpret_confirmation(self.confirm(question), question)
            if answer == "yes":
                return True
            if answer == "no":
                return False
            question = f"{self._question(description)} Please say yes or no."
        return False

    def _risky(self, description: str) -> bool:
        # risk of a control depends on the window, not on which step asked for it
        key = (self._window_title, description)
        if key not in self._risk_cache:
            self._risk_cache[key] = risk_check(description)[0]
        return self._risk_cache[key]

    def run_plan(self, goal: str, plan: list[dict], window=None) -> Result:
        """Execute a plan the voice LLM already made: [{"do": "press the One button"},
        {"do": "type the reply", "text": "..."}]. Kev does one action per item, no planning."""
        window = window or screen.foreground()
        steps: list[Step] = []
        self._typed = ""
        for item in plan:
            if self.cancel.is_set():
                break
            if item.get("text"):
                self._typed = item["text"]
            self.goal = goal
            r = self.run(item["do"], window, item.get("text"), max_actions=1, parent_goal=goal,
                         secret=item.get("secret"))
            steps += r.steps
            if not r.ok:
                done = "; ".join(f"{s.action} {s.target}" for s in steps) or "nothing"
                return Result(False, f"Stopped at '{item['do']}': {r.summary} Done so far: {done}.", steps)
        if self.cancel.is_set():
            done = "; ".join(f"{s.action} {s.target}" for s in steps) or "nothing"
            return Result(False, f"Stopped because you asked. Done so far: {done}.", steps)
        typed = [item["text"] for item in plan if item.get("text")]
        if typed and SEND_INTENT.search(goal + " " + " ".join(i["do"] for i in plan)):
            time.sleep(0.4)
            shown = " ".join(screen.snapshot(window).texts)
            stuck = [t for t in typed if t.strip() and t.strip()[:40] in shown and "contains:" in shown]
            if stuck:
                return Result(False, f"NOT sent: the text is still in the input box. Steps done: "
                                     f"{'; '.join(f'{s.action} {s.target}' for s in steps)}.", steps)
        return Result(True, f"Done: {goal}.", steps)

    def run(self, goal: str, window=None, text: str | None = None,
            max_actions: int = MAX_STEPS, parent_goal: str | None = None,
            secret: str | None = None) -> Result:
        """secret: name of a stored login; the password field is filled from the vault,
        never by a model."""
        window = window or screen.foreground()
        self.goal = parent_goal or goal
        self._window_title = window.Name
        steps: list[Step] = []
        typed = False
        last_state = None
        if text is not None and PASSWORD_WORDS.search(goal):
            # a password must come from the vault, never as text a model produced or heard
            return Result(False, "I never type a password that was said or written out. "
                                 "It has to be a stored login.", [])
        if secret and vault.get(secret) is None:
            return Result(False, f"There is no stored login called '{secret}'.", steps)
        for _ in range(MAX_STEPS):
            if self.cancel.is_set():
                return Result(False, "Stopped because you asked.", steps)
            if len(steps) >= max_actions:
                return Result(True, f"Done: {goal}.", steps)
            snap = screen.snapshot(window)
            history = "; ".join(f"{s.action} {s.target}" for s in steps) or "nothing yet"
            state = f"{snap.state()} The user wants to: {goal}. Already done: {history}."
            pending_secret = secret is not None and not typed
            pending_text = text is not None and not typed and not pending_secret
            if pending_text:
                state += f' The agent still has to type this text: "{text}", so it needs a text field.'
            if pending_secret:
                state += " The agent has to fill in a password, so it needs a password field."
            # code narrows the legal actions, the model only picks among them:
            # a secret only ever goes into a password field, model text never does
            criteria = {k: e.label for k, e in snap.elements.items()
                        if (pending_secret and e.password)
                        or (pending_text and e.ctrl.ControlTypeName in screen.TYPEABLE and not e.password)
                        or not (pending_secret or pending_text)}
            criteria["BLOCKED"] = "None of these elements can do the next step"
            questions = {"target": {"type": "choice", "instructions": "Which element should be used next?",
                                    "criteria": criteria}}
            if max_actions > 1:  # a single planned step needs no "are we done?" question
                questions["done"] = {"type": "noul", "instructions": "Is the user's goal already fully achieved?"}
            answers, ms = systemone.ask(state, questions)
            answers.setdefault("done", {"noul": 0.0})
            if DEBUG:
                print(f"   state: {state}\n   done={answers['done']['noul']:.2f} "
                      f"target={answers['target']['choice']} "
                      f"p={answers['target'].get('probabilities', {}).get(answers['target']['choice'], 0):.2f}")
            if steps and not (pending_text or pending_secret) and answers["done"]["noul"] >= DONE_P:
                return Result(True, f"Done: {goal}.", steps)

            key = answers["target"]["choice"]
            conf = answers["target"].get("probabilities", {}).get(key, 0.0)
            if (pending_text or pending_secret) and len(criteria) == 2:  # one field + BLOCKED: nothing to decide
                key, conf = next(iter(criteria)), 1.0
            el = snap.elements.get(key)
            if el is None or conf < MIN_PROB or not el.name:
                if steps and not (pending_text or pending_secret) and answers["done"]["noul"] >= 0.5:
                    # leaning "done" and no convincing next step: finished
                    return Result(True, f"Done: {goal}.", steps)
                step = self._vision_step(window, goal, text if pending_text else None,
                                         secret if pending_secret else None, ms,
                                         why=f"kev={key} conf={conf:.2f}")
                if step is None:
                    if self._declined:
                        declined, self._declined = self._declined, None
                        return Result(False, f"Cancelled: I did not {declined}.", steps)
                    if self.cancel.is_set():
                        return Result(False, "Stopped because you asked.", steps)
                    if steps:
                        return Result(False, f"I did: {history}, but I cannot confirm the rest "
                                             "on this screen.", steps)
                    return Result(False, "I could not find a way to do that on this screen.", steps)
                if step.action == "done":
                    return Result(True, f"Done: {goal}.", steps)
                if step.action in ("typed into", "entered stored password into"):
                    typed = True
                self._record(steps, step)
                continue

            if steps and steps[-1].target == el.label and snap.state() == last_state:
                # the model wants to repeat an action that changed nothing: stop instead of looping
                return Result(True, f"I did: {history}. The screen stopped changing, "
                                    "so I think that is finished.", steps)
            last_state = snap.state()
            if pending_secret:
                description = f"enter the stored '{secret}' password into {el.label}"
            elif pending_text:
                description = f"type '{text}' into {el.label}"
            else:
                description = f"click {el.label}"
            br = el.ctrl.BoundingRectangle
            rect = (br.left, br.top, br.right, br.bottom)
            short = el.name[:40] or el.kind
            if self._risky(description) or ALWAYS_CONFIRM.search(goal):
                self._show_target(rect, "confirm", short)
                if not self._approved(description):
                    self._emit(kind="target_clear")
                    return Result(False, f"Cancelled: I did not {description}.", steps)
            if self.cancel.is_set():
                return Result(False, "Stopped because you asked.", steps)
            self._show_target(rect, "kev", f"{short}  {ms:.0f} ms")
            self._show_click(rect, "kev")
            if pending_secret:
                screen.type_secret(el, vault.get(secret))
                typed, action = True, "entered stored password into"
            elif pending_text:
                screen.type_text(el, text)
                typed, action = True, "typed into"
            else:
                screen.click(el)
                action = "clicked"
            self._record(steps, Step("kev", action, el.label, ms))
            screen.wait_for_change(window, snap.state())
        return Result(False, "I stopped after too many steps.", steps)

    def _vision_step(self, window, goal, text, secret, kev_ms, why) -> Step | None:
        self.say("Looking at the screen.")
        if self.on_event:                     # our own cursor must not end up in the screenshot
            self._emit(kind="overlay_hide", seconds=1.5)
            time.sleep(0.12)
        png, origin = screen.capture(window)
        full_goal = goal + (f' (text to type: "{text}")' if text else "")
        if secret:  # the model only learns that a password field must be clicked, not the value
            full_goal += " (click the password field; the app types the password itself)"
        try:
            act, ms = self.vision.ask(png, full_goal)
        except Exception as e:
            self.say("The vision fallback is not available.")
            print("vision error:", e)
            return None
        a = act.get("action")
        if a == "blocked":
            return None
        if a == "done":
            return Step("vision", "done", "", ms, why)
        target = act.get("target", "an element")
        if secret:
            description = f"enter the stored '{secret}' password into {target}"
        elif a == "type":
            description = f"type '{act.get('text', '')}' into {target}"
        else:
            description = f"click {target}"
        left, top, scale = origin
        sx, sy = int(left + act["x"] / scale), int(top + act["y"] / scale)
        rect = (sx - 28, sy - 28, sx + 28, sy + 28)
        short = target[:40]
        if self._risky(description) or ALWAYS_CONFIRM.search(goal):
            self._show_target(rect, "confirm", short)
            if not self._approved(description):
                self._declined = description
                self._emit(kind="target_clear")
                return None
        if self.cancel.is_set():
            return None
        self._show_target(rect, "vision", f"{short}  {ms / 1000:.1f} s")
        self._show_click(rect, "vision")
        screen.click_at(origin, act["x"], act["y"])
        time.sleep(0.15)
        action = "clicked"
        if secret:
            if not screen.focused_is_password():
                self.say("That was not a password field, so I did not type the password.")
                return None
            for ch in vault.get(secret):
                screen.auto.SendUnicodeChar(ch)
            action = "entered stored password into"
        elif a == "type":
            if screen.focused_is_password():   # model text never goes into a password field
                self.say("That is a password field. I only fill it from your stored logins.")
                return None
            screen.auto.SendKeys(act.get("text", ""), interval=0.01, waitTime=0)
            action = "typed into"
        if act.get("say"):
            self.say(act["say"])
        time.sleep(0.5)
        return Step("vision", action, target, ms, why)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("goal")
    ap.add_argument("--window", help="title regex; default = foreground window")
    ap.add_argument("--text", help="text to type, if the goal needs it")
    args = ap.parse_args()
    window = screen.find_window(args.window) if args.window else None
    if args.window and window is None:
        raise SystemExit(f"no window matching {args.window}")
    if window:
        window.SetActive()
    agent = Agent(say=lambda s: print("[say]", s), confirm=lambda q: input(f"[ask] {q}\n> "))
    t = time.perf_counter()
    res = agent.run(args.goal, window, args.text)
    for s in res.steps:
        print(f"  {s.via:6} {s.action} {s.target}  ({s.ms:.0f} ms) {s.note}")
    print(f"[say] {res.summary}   total {time.perf_counter() - t:.1f}s")


if __name__ == "__main__":
    main()
