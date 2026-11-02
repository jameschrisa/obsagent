"""Argument parsing and command dispatch for obsagent."""
from __future__ import annotations

import argparse
import getpass
import re
import json
import os
import sys

from . import __version__, doctor, guests, mailer, paths, plans, profiles, stream, ws


class CliError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")

    p = argparse.ArgumentParser(prog="obsagent", description="Set up, configure, test and troubleshoot OBS Studio.")
    p.add_argument("--version", action="version", version=f"obsagent {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", parents=[common], help="read-only environment probe")
    d.add_argument("--net", action="store_true", help="also run networkQuality (about 20s)")

    w = sub.add_parser("ws", help="talk to obs-websocket").add_subparsers(dest="sub", required=True)
    w.add_parser("status", parents=[common])
    m = w.add_parser("monitor", parents=[common])
    m.add_argument("--seconds", type=int, default=20)
    w.add_parser("scenes", parents=[common])
    w.add_parser("inputs", parents=[common])
    for name in ("start-stream", "stop-stream", "start-record", "stop-record"):
        x = w.add_parser(name, parents=[common])
        x.add_argument("--yes", action="store_true", help="confirm")
    w.add_parser("set-password", parents=[common], help="store the websocket password in Keychain")

    pr = sub.add_parser("preset").add_subparsers(dest="sub", required=True)
    pr.add_parser("list", parents=[common])
    ps = pr.add_parser("show", parents=[common])
    ps.add_argument("name")

    pf = sub.add_parser("profile").add_subparsers(dest="sub", required=True)
    pa = pf.add_parser("apply", parents=[common])
    pa.add_argument("preset")
    pa.add_argument("--profile")
    pa.add_argument("--dry-run", action="store_true")
    pa.add_argument("--bitrate", type=int, metavar="KBPS", help="video bitrate override, 500-60000")

    pset = pf.add_parser("set", parents=[common], help="set profile parameters safely (switch, write, verify)")
    pset.add_argument("profile")
    pset.add_argument("triplets", nargs="+", metavar="SECTION KEY VALUE", help="repeatable SECTION KEY VALUE triplets")
    pset.add_argument("--dry-run", action="store_true")
    pset.add_argument("--restore", action="store_true", help="switch back to the original current profile at the end")

    st = sub.add_parser("stream").add_subparsers(dest="sub", required=True)
    ss = st.add_parser("set-service", parents=[common])
    ss.add_argument("--target", default="youtube", choices=["youtube", "zoom", "custom"])
    ss.add_argument("--rtmps", action="store_true")
    ss.add_argument("--key-from", default="keychain", choices=["keychain", "env", "prompt"])
    ss.add_argument("--server", help="RTMP URL (required for zoom/custom)")
    ss.add_argument("--key-name", help="named key, e.g. a second YouTube channel (see `stream list-keys`)")
    ss.add_argument("--dry-run", action="store_true")
    sk = st.add_parser("set-key", parents=[common], help="store a stream key in Keychain (prompts, or reads stdin)")
    sk.add_argument("--target", default="youtube", choices=["youtube", "zoom", "custom"])
    sk.add_argument("--key-name", help="store under a name, e.g. a second YouTube channel; omit for the default key")
    sk.add_argument("--label", help="free-text note shown by list-keys, e.g. the channel name")
    st.add_parser("list-keys", parents=[common], help="list stored stream keys (masked)")

    pl = sub.add_parser("plan").add_subparsers(dest="sub", required=True)
    for name in ("new", "show", "validate", "apply", "export"):
        x = pl.add_parser(name, parents=[common])
        x.add_argument("name")
        if name == "apply":
            x.add_argument("--dry-run", action="store_true")
            x.add_argument("--bitrate", type=int, metavar="KBPS", help="video bitrate override, 500-60000")
            x.add_argument("--key-from", default="keychain", choices=["keychain", "env", "prompt"])
        if name == "export":
            x.add_argument("--md", action="store_true", default=True)

    gu = sub.add_parser("guests", help="remote guests via VDO.Ninja (links, roster, guides)").add_subparsers(dest="sub", required=True)
    ga = gu.add_parser("add", parents=[common], help="add a guest (creates the room on first use)")
    ga.add_argument("plan")
    ga.add_argument("--name", required=True)
    ga.add_argument("--email", help="guest email address (stored in show state only)")
    gse = gu.add_parser("set-email", parents=[common], help="set or change a guest's email address")
    gse.add_argument("plan")
    gse.add_argument("--name", required=True)
    gse.add_argument("--email", required=True)
    gss = gu.add_parser("set-sender", parents=[common], help="store the default sender address (a Mail.app account)")
    gss.add_argument("addr")
    gss.add_argument("--name", help="host display name used in From and as the email sign-off")
    gu.add_parser("senders", parents=[common], help="list Mail.app accounts and addresses (read-only)")
    gi = gu.add_parser("invite", parents=[common], help="email each guest their personal link via Mail.app (draft by default)")
    gi.add_argument("plan")
    gi.add_argument("--name")
    gi.add_argument("--all", action="store_true")
    gi.add_argument("--from", dest="sender", metavar="ADDR")
    gi.add_argument("--send", action="store_true", help="send immediately instead of opening a draft for review")
    gi.add_argument("--attach-guide", action="store_true")
    gr = gu.add_parser("remove", parents=[common])
    gr.add_argument("plan")
    gr.add_argument("name")
    gl = gu.add_parser("list", parents=[common])
    gl.add_argument("plan")
    gl.add_argument("--reveal", action="store_true", help="show the real password in links")
    gk = gu.add_parser("links", parents=[common], help="guest, director and OBS links (password masked unless --reveal)")
    gk.add_argument("plan")
    gk.add_argument("name", nargs="?")
    gk.add_argument("--reveal", action="store_true")
    gt = gu.add_parser("rotate", parents=[common], help="new room and password; old links stop working")
    gt.add_argument("plan")
    gg = gu.add_parser("guide", parents=[common], help="write a personal guest guide HTML per guest")
    gg.add_argument("plan")
    gg.add_argument("--name")

    yt = sub.add_parser("youtube").add_subparsers(dest="sub", required=True)
    yt.add_parser("auth", parents=[common])
    yb = yt.add_parser("broadcast", parents=[common])
    yb.add_argument("action", choices=["create"])
    yb.add_argument("--title", required=True)
    yb.add_argument("--start", required=True, help="ISO 8601, e.g. 2026-11-07T10:00:00-08:00")
    yb.add_argument("--description", default="")
    yb.add_argument("--privacy", default="unlisted", choices=sorted(plans.PRIVACY))
    yb.add_argument("--latency", default="low", choices=sorted(plans.LATENCY))
    ys = yt.add_parser("stream", parents=[common])
    ys.add_argument("action", choices=["bind"])
    ys.add_argument("--broadcast", required=True)
    ys.add_argument("--stream-id")
    yst = yt.add_parser("status", parents=[common])
    yst.add_argument("--broadcast")
    return p


# ---- helpers ----

def need_ws():
    try:
        return ws.connect()
    except ws.WsUnavailable as e:
        raise CliError(f"Cannot reach obs-websocket: {e}. Run `obsagent doctor` for fix steps.", 3)


def try_ws():
    """Connect if OBS is running, else None."""
    if not paths.obs_running():
        return None
    try:
        return ws.connect()
    except ws.WsUnavailable:
        return None


def out(data, args, text=None):
    paths.emit(data, getattr(args, "json", False), text)


def kv_text(d, indent=0):
    pad = " " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            print(f"{pad}{k}:")
            kv_text(v, indent + 2)
        elif isinstance(v, list) and v and isinstance(v[0], (dict, list)):
            print(f"{pad}{k}:")
            for i in v:
                print(f"{pad}  - {json.dumps(i, default=str)}")
        else:
            print(f"{pad}{k}: {v}")


def print_diff(plan: dict):
    print(f"profile: {plan['profile']}  preset: {plan['preset']}  mode: {plan['mode']}")
    print(f"profile exists: {plan['profile_exists']}  OBS config dir exists: {plan['obs_config_dir_exists']}  OBS running: {plan['obs_running']}")
    for r in plan["ini"]:
        mark = "~" if r["changed"] else "="
        cur = "(unset)" if r["current"] is None else r["current"]
        print(f"  {mark} [{r['section']}] {r['key']}: {cur} -> {r['new']}")
    print("streamEncoder.json:", json.dumps(plan["streamEncoder.json"]))
    for w_ in plan["warnings"]:
        print(f"  warning: {w_}")


# ---- commands ----

def cmd_doctor(a):
    rep = doctor.run_doctor(net=a.net)
    out(rep, a, doctor.print_text)
    return 0 if rep["healthy"] else 1


def cmd_ws(a):
    if a.sub == "set-password":
        pw = getpass.getpass("obs-websocket password (input hidden): ")
        if not pw:
            raise CliError("empty password")
        if not paths.keychain_set(paths.ACCT_WS, pw):
            raise CliError("could not write to Keychain")
        out({"stored": True, "account": paths.ACCT_WS, "password": paths.mask(pw)}, a, kv_text)
        return 0
    if a.sub.startswith(("start-", "stop-")) and not a.yes:
        raise CliError(f"`ws {a.sub}` changes live state. Re-run with --yes to confirm.", 2)
    cl = need_ws()
    if a.sub == "status":
        out(ws.status(cl), a, kv_text)
    elif a.sub == "monitor":
        res = ws.monitor(cl, a.seconds)

        def txt(r):
            kv_text(r["summary"])
            if r["problems"]:
                print("problems:")
                for p_ in r["problems"]:
                    print(f"  - {p_['id']}: {p_['detail']}\n      fix: {p_['fix']}")
            else:
                print("no problems flagged")
        out(res, a, txt)
    elif a.sub == "scenes":
        out({"scenes": ws.scenes(cl)}, a, lambda d: print("\n".join(d["scenes"])))
    elif a.sub == "inputs":
        out({"inputs": ws.inputs(cl)}, a, lambda d: print("\n".join(f"{i['name']}  ({i['kind']})" for i in d["inputs"])))
    else:
        call = {"start-stream": cl.start_stream, "stop-stream": cl.stop_stream,
                "start-record": cl.start_record, "stop-record": cl.stop_record}[a.sub]
        call()
        out({"ok": True, "action": a.sub}, a, kv_text)
    return 0


def cmd_preset(a):
    if a.sub == "list":
        rows = []
        for n in profiles.list_presets():
            p_ = profiles.load_preset(n)
            rows.append({"name": n, "target": p_.get("target"), "res": f"{p_['width']}x{p_['height']}@{p_['fps']}",
                         "video_kbps": p_["video_bitrate_kbps"], "encoder": p_["encoder"]})
        out({"presets": rows}, a, lambda d: [print(f"{r['name']:26} {r['res']:14} {r['video_kbps']:>6} kbps  {r['encoder']}") for r in d["presets"]])
    else:
        try:
            p_ = profiles.load_preset(a.name)
        except profiles.PresetError as e:
            raise CliError(str(e))
        out({**p_, "streamEncoder.json": profiles.encoder_json(p_)}, a, kv_text)
    return 0


def print_verification(rows):
    print(f"{'section':<10} {'key':<24} {'expected':<42} {'actual':<42} ok")
    for r in rows:
        print(f"{r['section']:<10} {r['key']:<24} {str(r['expected']):<42} {str(r['actual']):<42} {'yes' if r['ok'] else 'MISMATCH'}")


def apply_text(r):
    kv_text({k: v for k, v in r.items() if k != "verification"})
    if r.get("verification"):
        print_verification(r["verification"])


def cmd_profile_set(a):
    if len(a.triplets) % 3:
        raise CliError("profile set needs SECTION KEY VALUE triplets (got %d values)" % len(a.triplets), 2)
    t = a.triplets
    changes = [(t[i], t[i + 1], t[i + 2]) for i in range(0, len(t), 3)]
    cl = need_ws()
    if a.dry_run:
        cur = profiles.current_values_from_files(a.profile, changes)
        out({"profile": a.profile, "dry_run": True, "ini": profiles.diff_rows(changes, cur)}, a, lambda p: print_diff({
            **p, "preset": "-", "mode": "files", "profile_exists": profiles.find_profile_dir(a.profile) is not None,
            "obs_config_dir_exists": paths.obs_config_dir().exists(), "obs_running": paths.obs_running(),
            "streamEncoder.json": {}, "warnings": []}))
        return 0
    try:
        result = profiles.set_params_via_ws(cl, a.profile, changes, restore=a.restore)
    except profiles.ProfileError as e:
        raise CliError(str(e))
    out({"profile": a.profile, **result}, a, apply_text)
    return 0 if result["verified"] else 1


def cmd_profile(a):
    if a.sub == "set":
        return cmd_profile_set(a)
    try:
        preset = profiles.with_bitrate(profiles.load_preset(a.preset), a.bitrate)
    except profiles.PresetError as e:
        raise CliError(str(e))
    profile = a.profile or f"obsagent-{preset['name']}"
    cl = try_ws()
    running = paths.obs_running()
    if running and cl is None:
        raise CliError("OBS is running but obs-websocket is unreachable. Enable it (Tools > WebSocket Server Settings) "
                       "and run `obsagent ws set-password`, or quit OBS to apply by editing files.", 3)
    plan = profiles.plan_apply(profile, preset, cl)
    if a.dry_run:
        plan["dry_run"] = True
        out(plan, a, print_diff)
        return 0
    try:
        result = profiles.apply_via_ws(cl, profile, preset) if cl else profiles.write_files(profile, preset)
    except profiles.ProfileError as e:
        raise CliError(str(e))
    out({"profile": profile, "preset": preset["name"], **result}, a, apply_text)
    return 0 if result.get("verified", True) else 1


def key_account(target: str, name: str | None) -> str:
    """Keychain account: `youtube` for the default key, `youtube:NAME` for a named one."""
    base = {"youtube": paths.ACCT_YOUTUBE}.get(target, target)
    if not name or name == "default":
        return base
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", name):
        raise CliError("--key-name must be lowercase letters, digits and hyphens (max 32)")
    return f"{base}:{name}"


def cmd_stream(a):
    if a.sub == "list-keys":
        rows = []
        for acct, meta in sorted(paths.key_registry().items()):
            k = paths.keychain_get(acct)
            rows.append({"account": acct, "label": meta.get("label", ""), "stored": bool(k),
                         "key": paths.mask(k) if k else None})
        out({"keys": rows}, a, kv_text)
        return 0
    account = key_account(a.target, getattr(a, "key_name", None))
    if a.sub == "set-key":
        if sys.stdin.isatty():
            key = getpass.getpass(f"{account} stream key (input hidden): ").strip()
        else:
            key = sys.stdin.readline().strip()
        if not key or not paths.keychain_set(account, key):
            raise CliError("key not stored")
        paths.key_registry_add(account, a.label)
        out({"stored": True, "account": account, "key": paths.mask(key)}, a, kv_text)
        return 0
    try:
        key = stream.resolve_key(a.key_from, account)
        cl = None if a.dry_run else need_ws()
        res = stream.set_service(cl, a.target, a.rtmps, key, a.server, dry_run=a.dry_run)
    except stream.KeyError_ as e:
        raise CliError(str(e))
    out(res, a, kv_text)
    return 0


def cmd_plan(a):
    try:
        if a.sub == "new":
            out({"created": str(plans.new_plan(a.name))}, a, kv_text)
            return 0
        plan = plans.load_plan(a.name)
        if a.sub == "show":
            out(plan, a, kv_text)
        elif a.sub == "validate":
            errs = plans.validate(plan)
            out({"valid": not errs, "errors": errs}, a, kv_text)
            return 0 if not errs else 1
        elif a.sub == "export":
            print(plans.export_md(plan), end="")
        elif a.sub == "apply":
            errs = plans.validate(plan)
            if errs:
                out({"valid": False, "errors": errs}, a, kv_text)
                return 1
            preset = profiles.with_bitrate(profiles.load_preset(plan["preset"]),
                                           a.bitrate if a.bitrate is not None else plan.get("video_bitrate_kbps"))
            profile = plan.get("profile") or plan["_name"]
            cl = try_ws()
            if paths.obs_running() and cl is None:
                raise CliError("OBS is running but obs-websocket is unreachable; see `obsagent doctor`.", 3)
            report = {"plan": plan["_name"], "dry_run": a.dry_run}
            pa = profiles.plan_apply(profile, preset, cl)
            report["profile"] = pa
            skip = profiles.already_current(profile, preset, pa, cl)
            if skip:
                report["profile_result"] = {"skipped": f"profile {profile} is already current and matches the preset"}
            if not a.dry_run and not skip:
                try:
                    report["profile_result"] = profiles.apply_via_ws(cl, profile, preset) if cl else profiles.write_files(profile, preset)
                except profiles.ProfileError as e:
                    raise CliError(str(e))
            # stream service (needs websocket)
            if plan["target"] in ("youtube",):
                account = key_account("youtube", (plan.get("youtube") or {}).get("key_name"))
                try:
                    key = stream.resolve_key(a.key_from, account)
                    if cl is not None:
                        report["service"] = stream.set_service(cl, plan["target"], bool((plan.get("youtube") or {}).get("rtmps", True)), key, dry_run=a.dry_run)
                    else:
                        report["service"] = {"skipped": "OBS not running; set the service after launching OBS: obsagent stream set-service"}
                except stream.KeyError_ as e:
                    report["service"] = {"skipped": str(e)}
            else:
                report["service"] = {"skipped": f"target {plan['target']}: run `obsagent stream set-service --target {plan['target']} --server URL`"}
            try:
                gctx = guests.plan_context(plan["_name"], plan)
            except guests.GuestError as e:
                raise CliError(str(e))
            if cl is not None:
                report["scenes_inputs"] = plans.apply_plan(plan, cl, dry_run=a.dry_run, guest_ctx=gctx)
            else:
                report["scenes_inputs"] = {**plans.apply_plan(plan, None, dry_run=True, guest_ctx=gctx),
                                           "note": "planned only: scenes and inputs are created over the websocket once OBS is running"}
            report["checklist"] = plan.get("checklist") or []

            def txt(r):
                print_diff(r["profile"])
                print("service:", json.dumps(r["service"], default=str))
                si = r["scenes_inputs"]
                print("requests:" if r["dry_run"] or "verify" not in si else "requests sent:")
                for q in si.get("requests", []):
                    print("  " + json.dumps({k: v for k, v in q.items() if k != "action"}, default=str))
                for n in si.get("notes", []):
                    print(f"note: {n}")
                if si.get("verify"):
                    print("verify:")
                    print(plans.format_verify(si["verify"]))
                print("checklist:")
                for c in r["checklist"]:
                    print(f"  [ ] {c}")
            out(report, a, txt)
    except (plans.PlanError, profiles.PresetError) as e:
        raise CliError(str(e))
    return 1 if (locals().get("report") or {}).get("profile_result", {}).get("verified") is False else 0


def _plan_guest_max(plan_name: str) -> int:
    try:
        return int((plans.load_plan(plan_name).get("guests") or {}).get("max", guests.MAX_GUESTS))
    except plans.PlanError:
        return guests.MAX_GUESTS


def cmd_guests(a):
    try:
        if a.sub == "add":
            state, g, created = guests.add_guest(a.plan, a.name, _plan_guest_max(a.plan), a.email)
            lk = guests.links(state, guests.get_password(a.plan), False, g)
            res = {"room_created": created, "guest": g, **lk,
                   "next": f"obsagent guests links {a.plan} {g['name']} --reveal  |  obsagent guests guide {a.plan} --name {g['name']}"}
            out(res, a, kv_text)
        elif a.sub == "set-email":
            g = guests.set_email(a.plan, a.name, a.email)
            out({"guest": g["name"], "email": g["email"]}, a, kv_text)
        elif a.sub == "set-sender":
            cfg = mailer.set_sender(a.addr, a.name)
            out({"sender": cfg["sender"], "host_name": cfg.get("host_name"),
                 "config": str(mailer.config_path())}, a, kv_text)
        elif a.sub == "senders":
            rows = mailer.list_senders()

            def stxt(d):
                for r in d["senders"]:
                    print(f"{r['address']}  ({r['account']})")
                if not d["senders"]:
                    print("no enabled Mail.app accounts found")
            out({"senders": rows}, a, stxt)
        elif a.sub == "invite":
            if bool(a.name) == bool(a.all):
                raise CliError("pass exactly one of --name NAME or --all")
            res = mailer.invite(a.plan, plans.load_plan(a.plan), [a.name] if a.name else None,
                                a.sender, a.send, a.attach_guide)

            def itxt(rows):
                for r in rows:
                    print(f"{r['mode']}: to {r['to']} from {r['from']} subject {r['subject']!r} link {r['link']}")
                if not a.send:
                    print("drafts are open in Mail.app for review; nothing was sent")
            out({"invites": res}, a, lambda d: itxt(d["invites"]))
        elif a.sub == "remove":
            g = guests.remove_guest(a.plan, a.name)
            out({"removed": g, "note": f"run `obsagent plan apply {a.plan}` to remove its OBS source"}, a, kv_text)
        elif a.sub in ("list", "links"):
            state = guests.load_state(a.plan)
            if state is None:
                raise CliError(f"no guests for plan {a.plan!r}; run `obsagent guests add {a.plan} --name NAME`")
            only = guests.find_guest(state, a.name) if a.sub == "links" and a.name else None
            lk = guests.links(state, guests.get_password(a.plan), a.reveal, only)
            by_id = {g["id"]: g for g in state["guests"]}
            for row in lk["guests"]:
                sg = by_id[row["id"]]
                row["email"] = sg.get("email")
                row["invited"] = ({"at": sg["last_invited_at"], "from": sg.get("invited_from"), "mode": sg.get("invite_mode")}
                                  if sg.get("last_invited_at") else None)

            def txt(r):
                print(f"room: {r['room']}")
                if a.sub == "links":
                    print(f"director: {r['director_link']}")
                for g in r["guests"]:
                    inv = g.get("invited")
                    mail = f"  email={g['email']}" if g.get("email") else ""
                    istat = f"  invited {inv['mode']} {inv['at']} from {inv['from']}" if inv else "  not invited"
                    print(f"{g['slot']}  {g['name']}  id={g['id']}{mail}{istat}")
                    if a.sub == "links":
                        print(f"   guest: {g['guest_link']}")
                        print(f"   obs:   {g['obs_link']}")
                if not r["revealed"]:
                    print("password masked; add --reveal to show the real links")
            out(lk, a, txt)
        elif a.sub == "rotate":
            state = guests.rotate(a.plan)
            out({"rotated": True, "room": state["room"],
                 "warning": "old links no longer work: send every guest a new link and re-run the guide and `plan apply`",
                 "guests": [g["name"] for g in state["guests"]]}, a, kv_text)
        elif a.sub == "guide":
            res = guests.write_guides(a.plan, plans.load_plan(a.plan), a.name)

            def gtxt(rows):
                for r in rows:
                    print(f"{r['name']}: {r['path']}")
                    if r["unfilled_placeholders"]:
                        print("  still highlighted: " + ", ".join(r["unfilled_placeholders"]))
            out({"guides": res}, a, lambda d: gtxt(d["guides"]))
    except (guests.GuestError, plans.PlanError) as e:
        raise CliError(str(e), getattr(e, "code", 1))
    return 0


def cmd_youtube(a):
    # Re-exec with Google deps if they are missing (uv script only declares the core deps).
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
    except ImportError:
        if os.environ.get("OBSAGENT_REEXEC") != "1" and paths.which("uv"):
            env = dict(os.environ, OBSAGENT_REEXEC="1")
            script = os.path.realpath(sys.argv[0])
            os.execvpe("uv", ["uv", "run", "--script", "--with", "google-api-python-client",
                              "--with", "google-auth-oauthlib", script, *sys.argv[1:]], env)
    from . import youtube
    try:
        if a.sub == "auth":
            res = youtube.auth()
        elif a.sub == "broadcast":
            res = youtube.create_broadcast(a.title, a.start, a.description, a.privacy, a.latency)
        elif a.sub == "stream":
            res = youtube.bind_stream(a.broadcast, a.stream_id)
        else:
            res = youtube.status(a.broadcast)
    except youtube.YoutubeNotConfigured as e:
        out({"configured": False, "help": str(e)}, a, lambda d: print(d["help"]))
        return 0
    out(res, a, kv_text)
    return 0


COMMANDS = {"doctor": cmd_doctor, "ws": cmd_ws, "preset": cmd_preset, "profile": cmd_profile,
            "stream": cmd_stream, "plan": cmd_plan, "guests": cmd_guests, "youtube": cmd_youtube}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except CliError as e:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(e), "code": e.code}))
        else:
            paths.err(f"error: {e}")
        return e.code
    except KeyboardInterrupt:
        return 130
