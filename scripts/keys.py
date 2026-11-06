#!/usr/bin/env python3
"""keys.py — API keys for optional services, stored in ~/.video-studio/keys.env (chmod 600).

  vs keys ls                 which keys are set (values never printed)
  vs keys set <name> [value] value from the argument or stdin (keeps it out of shell history)
  vs keys rm <name>
  vs keys test [name]        call a free endpoint of the service to prove the key works (no charge)

Names: pixabay (free), gemini (paid images), elevenlabs (paid voice/sfx/music).
An environment variable with the same name (e.g. GEMINI_API_KEY) always wins over keys.env.
"""
import json
import sys

from vslib import KEY_INFO, KEYS, die, get_key, http, set_key


def test(name: str) -> tuple[bool, str]:
    k = get_key(name)
    if not k:
        return False, "not set"
    try:
        if name == "pixabay":
            r = json.loads(http("https://pixabay.com/api/", params={"key": k, "q": "test", "per_page": 3}))
            return True, f"ok ({r.get('totalHits', 0)} hits)"
        if name == "gemini":
            r = json.loads(http("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": k}))
            return True, f"ok ({len(r.get('models', []))} models visible; billing not checked)"
        if name == "elevenlabs":
            r = json.loads(http("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": k}))
            left = r.get("character_limit", 0) - r.get("character_count", 0)
            return True, f"ok (tier {r.get('tier')}, {left} characters left)"
    except SystemExit:
        return False, "request failed (see message above)"
    return False, "no test for this key"


def main() -> None:
    a = sys.argv[1:]
    cmd = a[0] if a else "ls"
    if cmd in ("-h", "--help"):
        print(__doc__); return
    if cmd == "ls":
        print(f"keys file: {KEYS}")
        for n, (env, what, how) in KEY_INFO.items():
            print(f"  {'✓' if get_key(n) else '○'} {n:<11} {env:<20} {what} — {how}")
    elif cmd == "set":
        if len(a) < 2:
            die("usage: vs keys set <name> [value]")
        val = a[2] if len(a) > 2 else sys.stdin.readline().strip()
        if not val:
            die("empty value")
        set_key(a[1], val)
        print(f"✓ {a[1]} saved to {KEYS}")
    elif cmd == "rm":
        if len(a) < 2:
            die("usage: vs keys rm <name>")
        set_key(a[1], "")
        print(f"✓ {a[1]} removed")
    elif cmd == "test":
        for n in (a[1:] or [n for n in KEY_INFO if get_key(n)]):
            ok, msg = test(n)
            print(f"  {'✓' if ok else '✗'} {n}: {msg}")
    else:
        die(f"unknown: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main()
