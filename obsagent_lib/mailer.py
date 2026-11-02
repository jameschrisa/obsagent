"""Guest invites through macOS Mail.app (AppleScript via osascript).

Mail.app owns the credentials, so no SMTP password or OAuth lives here. Every value
(subject, body, addresses, paths) reaches AppleScript as an `on run argv` argument,
never interpolated into the script source.

Host settings live in ~/.config/obsagent/config (JSON, mode 600): `sender`, `host_name`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess

from . import guests, paths

AUTOMATION_HELP = ("macOS blocked this terminal from controlling Mail (error -1743). Fix: System Settings > "
                   "Privacy & Security > Automation, then allow your terminal app to control Mail, and retry.")
ADD_ACCOUNT_HELP = ("add the account in System Settings > Internet Accounts or Mail > Settings > Accounts "
                    "(to send from james@ausvrn.com, add the ausvrn.com Fastmail account), or pick another address")


class MailError(guests.GuestError):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


valid_email = guests.check_email


# ---- host config ----

def config_path():
    return paths.agent_config_dir() / "config"


def load_config() -> dict:
    f = config_path()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text())
    except (OSError, ValueError) as e:
        raise guests.GuestError(f"cannot read {f}: {e}")
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict) -> None:
    d = paths.agent_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = config_path()
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(cfg, indent=2) + "\n")
    os.chmod(f, 0o600)


def set_sender(addr: str, name: str | None = None) -> dict:
    cfg = load_config()
    cfg["sender"] = valid_email(addr)
    if name is not None:
        if name.strip():
            cfg["host_name"] = name.strip()
        else:
            cfg.pop("host_name", None)
    save_config(cfg)
    return cfg


# ---- osascript ----

SENDERS_SCRIPT = '''tell application "Mail"
  set out to ""
  -- Loop by index and `get` values: iterating references with `repeat with e in`
  -- fails on real Mail.app with error -1700 (cannot make emad into text).
  set accts to every account
  repeat with i from 1 to count of accts
    set a to item i of accts
    if (get enabled of a) then
      set acctName to (get name of a)
      set addrs to (get email addresses of a)
      repeat with j from 1 to count of addrs
        set out to out & acctName & tab & (item j of addrs) & linefeed
      end repeat
    end if
  end repeat
  return out
end tell'''

_COMPOSE = '''on run argv
  set theSubject to item 1 of argv
  set theBody to item 2 of argv
  set theSender to item 3 of argv
  set theTo to item 4 of argv
  set theAttach to item 5 of argv
  tell application "Mail"
    set msg to make new outgoing message with properties {subject:theSubject, content:theBody, visible:%s}
    tell msg
      if theSender is not "" then set sender to theSender
      make new to recipient at end of to recipients with properties {address:theTo}
      if theAttach is not "" then make new attachment with properties {file name:((POSIX file theAttach) as alias)}
    end tell
%s  end tell
end run'''

DRAFT_SCRIPT = _COMPOSE % ("true", "    activate\n")
SEND_SCRIPT = _COMPOSE % ("false", "    send msg\n")


def run_osascript(script: str, args: list[str] | None = None) -> str:
    cmd = ["osascript", "-e", script, *(args or [])]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except FileNotFoundError:
        raise MailError("osascript not found: Mail.app invites only work on macOS")
    except subprocess.TimeoutExpired:
        raise MailError("Mail.app did not respond within 90 seconds")
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "-1743" in err or "Not authorized to send Apple events" in err:
            raise MailError(AUTOMATION_HELP)
        raise MailError(f"Mail.app script failed: {err or 'unknown error'}")
    return r.stdout


def list_senders() -> list[dict]:
    rows = []
    for line in run_osascript(SENDERS_SCRIPT).splitlines():
        if "\t" in line:
            acct, addr = line.split("\t", 1)
            if addr.strip():
                rows.append({"account": acct.strip(), "address": addr.strip()})
    return rows


def check_sender(addr: str) -> list[dict]:
    rows = list_senders()
    if addr.lower() not in {r["address"].lower() for r in rows}:
        avail = ", ".join(f"{r['address']} ({r['account']})" for r in rows) or "none"
        raise MailError(f"{addr} is not an enabled Mail.app account address. Available: {avail}. {ADD_ACCOUNT_HELP}", code=2)
    return rows


def resolve_sender(cli_from: str | None, cfg: dict) -> str:
    addr = cli_from or cfg.get("sender")
    if not addr:
        raise MailError("no sender set: run `obsagent guests senders` to list Mail accounts, then "
                        "`obsagent guests set-sender ADDR [--name NAME]` (or pass --from ADDR)")
    return valid_email(addr)


# ---- invites ----

def invite(plan_name: str, plan: dict, names: list[str] | None, cli_from: str | None,
           send: bool, attach_guide: bool) -> list[dict]:
    """names=None means all guests. Returns one summary dict per guest. Never contains the password."""
    state = guests.load_state(plan_name)
    if state is None or not state["guests"]:
        raise guests.GuestError(f"no guests for plan {plan_name!r}; run `obsagent guests add {plan_name} --name NAME --email ADDR`")
    targets = [guests.find_guest(state, n) for n in names] if names else list(state["guests"])
    missing = [g["name"] for g in targets if not g.get("email")]
    if missing:
        raise guests.GuestError("no email address for: " + ", ".join(missing) +
                                f". Run `obsagent guests set-email {plan_name} --name NAME --email ADDR`")
    cfg = load_config()
    sender = resolve_sender(cli_from, cfg)
    check_sender(sender)
    host_name = cfg.get("host_name") or None
    pw = guests.get_password(plan_name)
    sender_prop = f"{host_name} <{sender}>" if host_name else sender
    results = []
    for g in targets:
        link = guests.guest_link(state, g, pw)
        subject, body = guests.render_email(plan_name, plan, g["name"], link, host_name)
        attach = ""
        if attach_guide:
            attach = guests.write_guides(plan_name, plan, g["name"])[0]["path"]
        run_osascript(SEND_SCRIPT if send else DRAFT_SCRIPT,
                      [subject, body, sender_prop, g["email"], attach])
        mode = "sent" if send else "draft"
        g["last_invited_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        g["invited_from"] = sender
        g["invite_mode"] = mode
        guests.save_state(plan_name, state)
        results.append({"name": g["name"], "to": g["email"], "from": sender, "subject": subject, "mode": mode,
                        "link": guests.guest_link(state, g, guests.MASK), "attached": attach or None})
    return results
