"""Remote guests via VDO.Ninja: per-show state, secret generation and link building.

Layout of the secrets:
- ~/.config/obsagent/shows/<plan>.json (dir 700, file 600): room name and guest list. No password.
- macOS Keychain, service `obsagent`, account `vdo:<plan>`: the room password.
Nothing here is ever written inside the repo. Links contain the password, so they are masked
in all output unless the caller passes reveal=True.

VDO.Ninja link formats (https://docs.vdo.ninja):
- guest:    https://vdo.ninja/?room=ROOM&password=PASS&push=GUESTID&label=NAME
- director: https://vdo.ninja/?director=ROOM&password=PASS
- OBS solo: https://vdo.ninja/?room=ROOM&password=PASS&view=GUESTID&solo
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import string
from pathlib import Path
from urllib.parse import quote

from . import paths

BASE = "https://vdo.ninja/"
MAX_GUESTS = 3
MASK = "********"
LOWER_ALNUM = string.ascii_lowercase + string.digits
ALNUM = string.ascii_letters + string.digits
PLAN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# Template placeholder -> how it is filled. Order matters only for reporting.
PLACEHOLDERS = [
    "[SHOW TITLE]",
    "[DATE AND TIME WITH TIME ZONE]",
    "[TECH CHECK DATE AND TIME]",
    "[HOST CONTACT: phone or text]",
    "[YOUTUBE WATCH LINK]",
    "[YOUR GUEST LINK]",
]


class GuestError(Exception):
    pass


# ---- secrets and ids ----

def new_room() -> str:
    return "obsa" + "".join(secrets.choice(LOWER_ALNUM) for _ in range(10))


def new_password() -> str:
    return "".join(secrets.choice(ALNUM) for _ in range(16))


def new_guest_id() -> str:
    return "".join(secrets.choice(LOWER_ALNUM) for _ in range(8))


# ---- state files ----

def check_plan_name(plan: str) -> str:
    if not PLAN_NAME_RE.match(plan or "") or ".." in plan:
        raise GuestError(f"invalid plan name: {plan!r}")
    return plan


def shows_dir() -> Path:
    return paths.agent_config_dir() / "shows"


def state_path(plan: str) -> Path:
    return shows_dir() / f"{check_plan_name(plan)}.json"


def keychain_account(plan: str) -> str:
    return f"vdo:{check_plan_name(plan)}"


def guides_dir() -> Path:
    override = os.environ.get("OBSAGENT_GUIDES_DIR")
    return Path(override) if override else Path.home() / "Documents" / "obsagent" / "guides"


def load_state(plan: str) -> dict | None:
    f = state_path(plan)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except (OSError, ValueError) as e:
        raise GuestError(f"cannot read {f}: {e}")
    data.setdefault("guests", [])
    return data


def save_state(plan: str, state: dict) -> None:
    d = shows_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    f = state_path(plan)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(state, indent=2) + "\n")
    os.chmod(f, 0o600)


def get_password(plan: str) -> str:
    pw = paths.keychain_get(keychain_account(plan))
    if not pw:
        raise GuestError(f"no room password in Keychain (service obsagent, account {keychain_account(plan)}). "
                         f"Run `obsagent guests rotate {plan}` to create a new room.")
    return pw


def _new_room_state(plan: str) -> dict:
    pw = new_password()
    if not paths.keychain_set(keychain_account(plan), pw):
        raise GuestError("could not store the room password in the macOS Keychain")
    return {"plan": plan, "room": new_room(), "guests": []}


def ensure_state(plan: str) -> tuple[dict, bool]:
    """Return (state, created). Creates the room and password on first use."""
    st = load_state(plan)
    if st is not None:
        return st, False
    st = _new_room_state(plan)
    save_state(plan, st)
    return st, True


def _clean_name(name: str) -> str:
    n = (name or "").strip()
    if not n:
        raise GuestError("guest name is required")
    if len(n) > 60 or re.search(r"[\x00-\x1f]", n):
        raise GuestError("guest name must be 60 characters or fewer, with no control characters")
    return n


def _find(state: dict, name: str) -> dict | None:
    return next((g for g in state["guests"] if g["name"].lower() == name.strip().lower()), None)


def check_email(addr: str) -> str:
    a = (addr or "").strip()
    if not re.match(r"^[^\s@<>\"',;]+@[^\s@<>\"',;]+\.[A-Za-z]{2,}$", a):
        raise GuestError(f"{addr!r} is not a valid email address")
    return a


def set_email(plan: str, name: str, email: str) -> dict:
    email = check_email(email)
    state = load_state(plan)
    if state is None:
        raise GuestError(f"no guests for plan {plan!r}")
    g = find_guest(state, name)
    g["email"] = email
    save_state(plan, state)
    return g


def add_guest(plan: str, name: str, max_guests: int = MAX_GUESTS, email: str | None = None) -> tuple[dict, dict, bool]:
    """Returns (state, guest, room_created)."""
    name = _clean_name(name)
    if email:
        email = check_email(email)
    limit = min(int(max_guests), MAX_GUESTS)
    state, created = ensure_state(plan)
    if _find(state, name):
        raise GuestError(f"guest {name!r} already exists")
    if len(state["guests"]) >= limit:
        raise GuestError(f"at most {limit} guests per show (plan limit); remove one first")
    used = {g["slot"] for g in state["guests"]}
    slot = next(s for s in range(1, MAX_GUESTS + 1) if s not in used)
    ids = {g["id"] for g in state["guests"]}
    gid = new_guest_id()
    while gid in ids:
        gid = new_guest_id()
    guest = {"slot": slot, "name": name, "id": gid}
    if email:
        guest["email"] = email
    state["guests"].append(guest)
    state["guests"].sort(key=lambda g: g["slot"])
    save_state(plan, state)
    return state, guest, created


def remove_guest(plan: str, name: str) -> dict:
    state = load_state(plan)
    if state is None:
        raise GuestError(f"no guests for plan {plan!r}")
    g = _find(state, name)
    if not g:
        raise GuestError(f"no guest named {name!r}")
    state["guests"].remove(g)
    save_state(plan, state)
    return g


def rotate(plan: str) -> dict:
    """New room, new password, new guest ids. Old links stop working."""
    state = load_state(plan)
    if state is None:
        raise GuestError(f"no guests for plan {plan!r}; add one first")
    fresh = _new_room_state(plan)
    state["room"] = fresh["room"]
    for g in state["guests"]:
        g["id"] = new_guest_id()
    save_state(plan, state)
    return state


# ---- links ----

def _pw(password: str) -> str:
    return password if password == MASK else quote(password, safe='')


def guest_link(state: dict, guest: dict, password: str) -> str:
    return (f"{BASE}?room={quote(state['room'], safe='')}&password={_pw(password)}"
            f"&push={quote(guest['id'], safe='')}&label={quote(guest['name'], safe='')}")


def director_link(state: dict, password: str) -> str:
    return f"{BASE}?director={quote(state['room'], safe='')}&password={_pw(password)}"


def obs_link(state: dict, guest: dict, password: str) -> str:
    return (f"{BASE}?room={quote(state['room'], safe='')}&password={_pw(password)}"
            f"&view={quote(guest['id'], safe='')}&solo")


def links(state: dict, password: str, reveal: bool = False, only: dict | None = None) -> dict:
    pw = password if reveal else MASK
    gs = [only] if only else state["guests"]
    return {
        "room": state["room"],
        "revealed": reveal,
        "director_link": director_link(state, pw),
        "guests": [{"slot": g["slot"], "name": g["name"], "id": g["id"],
                    "guest_link": guest_link(state, g, pw), "obs_link": obs_link(state, g, pw)} for g in gs],
    }


def find_guest(state: dict, name: str) -> dict:
    g = _find(state, name)
    if not g:
        raise GuestError(f"no guest named {name!r}")
    return g


def plan_context(plan_name: str, plan: dict) -> dict | None:
    """What `plan apply` needs: None when the plan has no guests block or no show state."""
    if not plan.get("guests"):
        return None
    state = load_state(plan_name)
    if state is None:
        return None
    return {"room": state["room"], "password": get_password(plan_name),
            "guests": sorted(state["guests"], key=lambda g: g["slot"]), "state": state}


# ---- guide ----

def _plan_fields(plan: dict) -> dict:
    yt = plan.get("youtube") or {}
    return {
        "[SHOW TITLE]": plan.get("title"),
        "[DATE AND TIME WITH TIME ZONE]": plan.get("show_time"),
        "[TECH CHECK DATE AND TIME]": plan.get("tech_check"),
        "[HOST CONTACT: phone or text]": plan.get("host_contact"),
        "[YOUTUBE WATCH LINK]": yt.get("watch_url"),
    }


def render_guide(template: str, plan: dict, guest_name: str, link: str) -> tuple[str, list[str]]:
    """Fill the template. Returns (html, placeholders still unfilled)."""
    text = template
    fills = {k: v for k, v in _plan_fields(plan).items() if v}
    for ph, val in fills.items():
        sval = str(val)
        if ph == "[YOUTUBE WATCH LINK]":
            rep = f'<a href="{html.escape(sval, quote=True)}">{html.escape(sval)}</a>'
        else:
            rep = html.escape(sval)
        text = text.replace(f'<span class="fill">{ph}</span>', rep)
        text = text.replace(ph, html.escape(sval) if ph != "[YOUTUBE WATCH LINK]" else rep)
    anchor = f'<a href="{html.escape(link, quote=True)}">{html.escape(link)}</a>'
    text = text.replace('<span class="fill">[YOUR GUEST LINK]</span>', anchor)
    greeting = f"<p>Hi {html.escape(guest_name)},</p>\n"
    text = re.sub(r"(</h1>\n?)", lambda m: m.group(1) + greeting, text, count=1)
    remaining = [p for p in PLACEHOLDERS if p in text]
    return text, remaining


def safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-").lower() or "guest"


def write_guides(plan_name: str, plan: dict, only: str | None = None) -> list[dict]:
    state = load_state(plan_name)
    if state is None or not state["guests"]:
        raise GuestError(f"no guests for plan {plan_name!r}; run `obsagent guests add {plan_name} --name NAME`")
    pw = get_password(plan_name)
    template_file = paths.REPO_DIR / "docs" / "guest-guide.html"
    if not template_file.exists():
        raise GuestError(f"template not found: {template_file}")
    template = template_file.read_text()
    targets = [find_guest(state, only)] if only else state["guests"]
    d = guides_dir()
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for g in targets:
        text, remaining = render_guide(template, plan, g["name"], guest_link(state, g, pw))
        f = d / f"{safe_filename(plan_name)}-{safe_filename(g['name'])}.html"
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(f, 0o600)
        out.append({"name": g["name"], "path": str(f), "unfilled_placeholders": remaining})
    return out


# ---- invite email ----

EMAIL_SUBJECT = "Your guest link for {title}"


def render_email(plan_name: str, plan: dict, guest_name: str, link: str, host_name: str | None = None) -> tuple[str, str]:
    """Plain-text invite. Lines for missing plan fields are left out, never shown as placeholders."""
    yt = plan.get("youtube") or {}
    title = (yt.get("title") or plan.get("title") or plan_name)
    show_time = plan.get("show_time")
    tech = plan.get("tech_check")
    contact = plan.get("host_contact")
    lines = [f"Hi {guest_name},", ""]
    lines.append(f"Thanks for joining me on {title}" + (f" on {show_time}" if show_time else "") + ".")
    lines += ["", "Here is your personal guest link:", link, ""]
    if tech:
        lines += [f"Tech check: {tech}. Please join from the same room and setup you will use on the day.", ""]
    lines += [
        "How to join:",
        "1. Open the link in Chrome or Edge on a laptop or desktop. A phone works too: use Safari or Chrome and hold it sideways.",
        "2. Allow your camera and microphone when asked.",
        "3. Wear headphones, pick your camera and mic, and press the start button.",
        "4. Keep the tab open. I will bring you on air from my side.",
        "",
        "Please keep this link to yourself, it works like a key to the studio.",
    ]
    if contact:
        lines += ["", f"If anything goes wrong, reach me at {contact}."]
    lines += ["", host_name or "See you soon"]
    return EMAIL_SUBJECT.format(title=title), "\n".join(lines) + "\n"
