"""Read-only guard for the Chrome panel's chat.

The bridge starts the chat's `claude` child with OBSAGENT_CALLER=panel. While that variable is set, both CLIs
refuse every command that changes state, even if the model's permission rules were bypassed. Panel buttons
call bridge endpoints that run the CLIs WITHOUT this variable, so they keep working.

One table (RULES) and one check (blocked). Each rule: (tool, positional prefix, condition).
Conditions: "always"; "unless:--flag" (blocked unless that exact flag is present); "if:--flag" (blocked when
the flag is present, abbreviations like --rev included); "unless-pos:word" (blocked unless the next
positional word is `word`).
"""
from __future__ import annotations

import os

ENV = "OBSAGENT_CALLER"
VALUE = "panel"
EXIT_CODE = 3

RULES = [
    ("obsagent", ("ws", "start-stream"), "always"),
    ("obsagent", ("ws", "stop-stream"), "always"),
    ("obsagent", ("ws", "start-record"), "always"),
    ("obsagent", ("ws", "stop-record"), "always"),
    ("obsagent", ("ws", "scene"), "always"),
    ("obsagent", ("ws", "refresh"), "always"),
    ("obsagent", ("ws", "set-password"), "always"),
    ("obsagent", ("plan", "new"), "always"),
    ("obsagent", ("plan", "apply"), "unless:--dry-run"),
    ("obsagent", ("profile", "apply"), "unless:--dry-run"),
    ("obsagent", ("profile", "set"), "unless:--dry-run"),
    ("obsagent", ("stream", "set-key"), "always"),
    ("obsagent", ("stream", "set-service"), "always"),
    ("obsagent", ("guests", "add"), "always"),
    ("obsagent", ("guests", "remove"), "always"),
    ("obsagent", ("guests", "rotate"), "always"),
    ("obsagent", ("guests", "set-email"), "always"),
    ("obsagent", ("guests", "set-sender"), "always"),
    ("obsagent", ("guests", "invite"), "always"),
    ("obsagent", ("guests", "guide"), "always"),
    ("obsagent", ("guests", "refresh"), "always"),
    ("obsagent", ("guests", "links"), "if:--reveal"),
    ("obsagent", ("preflight",), "if:--record"),
    ("obsagent", ("show", "postshow"), "always"),
    ("obsagent", ("serve",), "unless-pos:status"),
    ("obsagent", ("youtube", "auth"), "always"),
    ("obsagent", ("youtube", "broadcast"), "always"),
    ("podproducer", ("run",), "always"),
    ("podproducer", ("prep",), "always"),
    ("podproducer", ("recap",), "always"),
    ("podproducer", ("sessions", "prune"), "always"),
    ("podproducer", ("sessions",), "if:--delete"),
]


def _has_flag(argv: list[str], flag: str, abbrev: bool) -> bool:
    for t in argv:
        if not t.startswith("--"):
            continue
        name = t.split("=", 1)[0]
        if name == flag or (abbrev and len(name) >= 3 and flag.startswith(name)):
            return True
    return False


def blocked(tool: str, argv: list[str]) -> str | None:
    """Return a refusal message if this command may not run for the panel chat, else None."""
    pos = [t for t in argv if not t.startswith("-")]
    for rtool, prefix, cond in RULES:
        if rtool != tool or tuple(pos[:len(prefix)]) != prefix:
            continue
        kind, _, arg = cond.partition(":")
        if kind == "always":
            hit = True
        elif kind == "unless":
            hit = not _has_flag(argv, arg, abbrev=False)
        elif kind == "if":
            hit = _has_flag(argv, arg, abbrev=True)
        elif kind == "unless-pos":
            hit = pos[len(prefix):len(prefix) + 1] != [arg]
        else:
            hit = True
        if hit:
            return (f"refused: `{tool} {' '.join(prefix)}` changes state and is not allowed from the panel chat "
                    f"({ENV}={VALUE}). Use the panel buttons or run it in a terminal.")
    return None


def check(tool: str, argv: list[str]) -> int | None:
    """Exit code to return when the guard refuses (message printed to stderr), else None."""
    if os.environ.get(ENV) != VALUE:
        return None
    msg = blocked(tool, list(argv))
    if msg is None:
        return None
    import sys
    print(msg, file=sys.stderr)
    return EXIT_CODE
