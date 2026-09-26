"""Stored logins for the screen agent, kept in Windows Credential Manager.

The models never see a secret: a plan step says {"do": "enter the password", "secret": "amazon"}
and the executor fills the password field itself.

  python vault.py set amazon      # prompts for the value, stores it
  python vault.py list
  python vault.py delete amazon
"""
import getpass
import json
import sys

import keyring

SERVICE = "voice-agent-secret"
INDEX = "__index__"          # Credential Manager cannot enumerate, so keep the names


def _names() -> list[str]:
    raw = keyring.get_password(SERVICE, INDEX)
    return json.loads(raw) if raw else []


def get(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def put(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)
    names = _names()
    if name not in names:
        keyring.set_password(SERVICE, INDEX, json.dumps(names + [name]))


def delete(name: str) -> None:
    keyring.delete_password(SERVICE, name)
    keyring.set_password(SERVICE, INDEX, json.dumps([n for n in _names() if n != name]))


def names() -> list[str]:
    """Names only - this is what the voice LLM may be told."""
    return _names()


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["list"]
    if cmd == "set" and rest:
        put(rest[0], getpass.getpass(f"value for '{rest[0]}': "))
        print("stored")
    elif cmd == "delete" and rest:
        delete(rest[0])
        print("deleted")
    else:
        print("\n".join(names()) or "(no stored logins)")
