"""Read-only environment probe. Every check returns {id, status, summary, detail, fix}.

status is ok | warn | fail | info. Only a missing OBS install is a fail; everything else that is
not ready yet is a warn with a fix hint, so a fresh Mac gets a clear to-do list.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import guests, mailer, paths, plans, profiles, ws


def chk(id_, status, summary, detail=None, fix=None) -> dict:
    return {"id": id_, "status": status, "summary": summary, "detail": detail, "fix": fix}


def check_obs_installed() -> dict:
    v = paths.obs_version()
    if v:
        return chk("obs_installed", "ok", f"OBS {v} at {paths.OBS_APP}")
    return chk("obs_installed", "fail", "OBS Studio not found", fix="Install from https://obsproject.com (Apple Silicon build) into /Applications.")


def check_obs_running() -> dict:
    if paths.obs_running():
        return chk("obs_running", "ok", "OBS is running")
    return chk("obs_running", "info", "OBS is not running", fix="Open OBS to use websocket commands. File-based profile apply works while it is closed.")


def check_config_dir() -> dict:
    d = paths.obs_config_dir()
    if not d.exists():
        return chk("obs_config", "warn", f"No OBS config dir at {d}", fix="Launch OBS once and finish the auto-configuration wizard (or cancel it) so the folder is created.")
    names = profiles.list_profile_names()
    sd = paths.scenes_dir()
    collections = sorted(p.stem for p in sd.glob("*.json")) if sd.exists() else []
    return chk("obs_config", "ok", f"Config dir present: {len(names)} profile(s), {len(collections)} scene collection(s)",
               detail={"profiles": names, "scene_collections": collections})


def _ws_json_path() -> Path:
    return paths.obs_config_dir() / "plugin_config" / "obs-websocket" / "config.json"


def read_ws_config() -> dict | None:
    """Read obs-websocket settings. OBS 33 JSON first, then the legacy [OBSWebSocket] ini section.

    Returns normalized keys ServerEnabled, ServerPort, AuthRequired, _has_password, _file.
    The password itself is never returned.
    """
    jf = _ws_json_path()
    if jf.exists():
        try:
            data = json.loads(jf.read_text())
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            return {
                "ServerEnabled": str(bool(data.get("server_enabled", False))).lower(),
                "ServerPort": str(data.get("server_port", 4455)),
                "AuthRequired": str(bool(data.get("auth_required", False))).lower(),
                "_has_password": str(bool(data.get("server_password"))),
                "_file": str(jf),
            }
    d = paths.obs_config_dir()
    for fname in ("user.ini", "global.ini"):
        f = d / fname
        if f.exists():
            cp = profiles.read_ini(f)
            if cp.has_section("OBSWebSocket"):
                sec = dict(cp["OBSWebSocket"])
                sec.pop("ServerPassword", None)
                sec["_has_password"] = str(bool(cp["OBSWebSocket"].get("ServerPassword")))
                sec["_file"] = str(f)
                return sec
    return None


def check_ws_config() -> dict:
    cfg = read_ws_config()
    howto = "OBS > Tools > WebSocket Server Settings > Enable WebSocket server, set a password, then run `obsagent ws set-password`."
    if cfg is None:
        return chk("ws_config", "warn", "No obs-websocket settings found in OBS config", fix=howto)
    enabled = str(cfg.get("ServerEnabled", "false")).lower() == "true"
    if not enabled:
        return chk("ws_config", "warn", "obs-websocket server is disabled", detail=cfg, fix=howto)
    return chk("ws_config", "ok", f"obs-websocket enabled on port {cfg.get('ServerPort', '4455')}", detail=cfg)


def check_ws_reachable(running: bool) -> dict:
    s = ws.ws_settings()
    if not running:
        return chk("ws_reachable", "info", "Skipped websocket probe because OBS is not running")
    try:
        cl = ws.connect()
        v = cl.get_version()
        return chk("ws_reachable", "ok", f"Connected to obs-websocket {getattr(v, 'obs_web_socket_version', '?')} (OBS {getattr(v, 'obs_version', '?')})")
    except ws.WsUnavailable as e:
        msg = str(e)
        auth = "auth" in msg.lower() or "password" in msg.lower()
        has_pw = bool(s["password"])
        return chk("ws_reachable", "warn", f"Cannot connect to ws://{s['host']}:{s['port']}",
                   detail=msg[:300],
                   fix=("Password rejected. Re-run `obsagent ws set-password` or set OBS_WS_PASSWORD." if auth or has_pw
                        else "Enable the WebSocket server in OBS (Tools > WebSocket Server Settings), then `obsagent ws set-password`."))


def check_ws_password() -> dict:
    if paths.keychain_get(paths.ACCT_WS):
        return chk("ws_password", "ok", "websocket password stored in Keychain (obsagent / obs-websocket)")
    import os
    if os.environ.get("OBS_WS_PASSWORD"):
        return chk("ws_password", "ok", "websocket password from OBS_WS_PASSWORD")
    return chk("ws_password", "warn", "No websocket password stored", fix="Run `obsagent ws set-password` after enabling the server in OBS.")


def check_hardware() -> dict:
    _, chip, _ = paths.run(["sysctl", "-n", "machdep.cpu.brand_string"])
    _, mem, _ = paths.run(["sysctl", "-n", "hw.memsize"])
    _, osv, _ = paths.run(["sw_vers", "-productVersion"])
    gb = round(int(mem.strip()) / 2**30) if mem.strip().isdigit() else None
    detail = {"chip": chip.strip(), "ram_gb": gb, "macos": osv.strip()}
    status = "ok" if "Apple" in chip else "warn"
    fix = None if status == "ok" else "VideoToolbox CBR hardware encoding needs Apple Silicon; use x264 presets."
    return chk("hardware", status, f"{detail['chip']}, {gb} GB, macOS {detail['macos']}", detail=detail, fix=fix)


def _system_profiler(dtype: str) -> dict | None:
    rc, out, _ = paths.run(["system_profiler", dtype, "-json"], timeout=30)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def check_cameras() -> dict:
    data = _system_profiler("SPCameraDataType")
    if data is None:
        return chk("cameras", "warn", "Could not list cameras", fix="Check System Settings > Privacy & Security > Camera.")
    names = [c.get("_name") for c in data.get("SPCameraDataType", [])]
    if not names:
        return chk("cameras", "warn", "No cameras detected", fix="Plug in the camera and check cable/hub.")
    return chk("cameras", "ok", f"{len(names)} camera(s): {', '.join(n for n in names if n)}", detail=names)


def check_audio() -> dict:
    data = _system_profiler("SPAudioDataType")
    if data is None:
        return chk("audio", "warn", "Could not list audio devices")
    ins, outs = [], []
    for grp in data.get("SPAudioDataType", []):
        for it in grp.get("_items", []):
            n = it.get("_name")
            if it.get("coreaudio_device_input"):
                ins.append(n)
            if it.get("coreaudio_device_output"):
                outs.append(n)
    if not ins:
        return chk("audio", "warn", "No audio input devices detected", detail={"outputs": outs}, fix="Connect a microphone and allow Microphone access in System Settings.")
    return chk("audio", "ok", f"{len(ins)} input(s), {len(outs)} output(s)", detail={"inputs": ins, "outputs": outs})


def check_permissions() -> list[dict]:
    # macOS does not expose TCC grants to unprivileged processes, so give the exact path to check.
    return [
        chk("perm_screen_recording", "info", "Screen Recording permission cannot be read from the CLI",
            fix="System Settings > Privacy & Security > Screen & System Audio Recording > enable OBS (restart OBS after granting). Symptom of missing grant: black screen capture."),
        chk("perm_camera", "info", "Camera permission cannot be read from the CLI",
            fix="System Settings > Privacy & Security > Camera > enable OBS. Symptom: black video capture device."),
        chk("perm_microphone", "info", "Microphone permission cannot be read from the CLI",
            fix="System Settings > Privacy & Security > Microphone > enable OBS. Symptom: silent mic meter."),
    ]


def check_disk() -> dict:
    target = Path.home() / "Movies"
    if not target.exists():
        target = Path.home()
    free_gb = round(shutil.disk_usage(target).free / 2**30, 1)
    if free_gb < 10:
        return chk("disk", "fail", f"{free_gb} GB free for recordings", fix="Free disk space; 1080p60 at 12 Mbps records about 5.4 GB/hour.")
    if free_gb < 50:
        return chk("disk", "warn", f"{free_gb} GB free for recordings", fix="Keep 50+ GB free for long recordings.")
    return chk("disk", "ok", f"{free_gb} GB free for recordings")


def check_keychain() -> dict:
    yt = paths.keychain_get(paths.ACCT_YOUTUBE)
    if yt:
        return chk("youtube_key", "ok", f"YouTube stream key in Keychain ({paths.mask(yt)})")
    return chk("youtube_key", "info", "No YouTube stream key stored", fix="Run `obsagent stream set-key --target youtube` (prompts, stores in Keychain).")


def check_network() -> dict:
    t0 = time.time()
    rc, out, err = paths.run(["networkQuality", "-s", "-c"], timeout=90)
    if rc != 0:
        return chk("network", "warn", "networkQuality failed", detail=(err or out)[:200])
    try:
        d = json.loads(out)
    except ValueError:
        return chk("network", "warn", "Could not parse networkQuality output")
    up = (d.get("ul_throughput") or 0) / 1e6
    dn = (d.get("dl_throughput") or 0) / 1e6
    safe = int(up * 1000 * 0.7)
    suggested = max(0, int(up * 1000 * 0.65 - 128)) if up > 0 else None
    detail = {"upload_mbps": round(up, 1), "download_mbps": round(dn, 1), "responsiveness": d.get("responsiveness"),
              "max_safe_video_kbps": safe, "suggested_max_video_kbps": suggested, "seconds": round(time.time() - t0)}
    status = "ok" if safe >= 6000 else "warn"
    return chk("network", status, f"Upload {up:.1f} Mbps; keep total stream bitrate under about {safe} kbps (70% of upload)", detail=detail,
               fix=None if status == "ok" else "Use a lower preset (youtube-720p30) or a wired connection.")


def read_browser_hwaccel() -> str | None:
    """BrowserHWAccel from [General] in global.ini (or user.ini), lowercased, None if unset."""
    for fname in ("global.ini", "user.ini"):
        f = paths.obs_config_dir() / fname
        if f.exists():
            cp = profiles.read_ini(f)
            if cp.has_section("General") and cp["General"].get("BrowserHWAccel") is not None:
                return cp["General"]["BrowserHWAccel"].strip().lower()
    return None


def check_browser_hwaccel() -> dict:
    v = read_browser_hwaccel()
    if v == "true":
        return chk("browser_hwaccel", "warn", "Browser Source Hardware Acceleration is on (BrowserHWAccel=true)",
                   fix="Browser sources such as VDO.Ninja guests can render fully transparent. OBS > Settings > Advanced > "
                       "turn off Browser Source Hardware Acceleration, then restart OBS.")
    if v == "false":
        return chk("browser_hwaccel", "ok", "Browser Source Hardware Acceleration is off")
    return chk("browser_hwaccel", "info", "BrowserHWAccel not set in global.ini (OBS default is on)",
               fix="If browser sources render blank, turn it off in OBS > Settings > Advanced and restart OBS.")


def plan_checks(plan_name: str) -> list[dict]:
    """Show-readiness checks for a plan: validity, background, guests, Keychain and Mail.app."""
    try:
        plan = plans.load_plan(plan_name)
    except plans.PlanError as e:
        return [chk("plan_valid", "fail", f"Plan {plan_name!r} cannot be loaded", detail=str(e)[:300],
                    fix="Check the plan name or path (`obsagent plan new NAME`).")]
    errs = plans.validate(plan)
    rows = [chk("plan_valid", "fail" if errs else "ok",
                f"Plan {plan_name!r} has {len(errs)} validation error(s)" if errs else f"Plan {plan_name!r} validates",
                detail=errs or None, fix="Run `obsagent plan validate %s` and fix each error." % plan_name if errs else None)]
    bg = plan.get("background")
    if isinstance(bg, dict) and bg.get("image") is not None and isinstance(bg["image"], str):
        img = plans.resolve_bg_image(bg["image"])
        rows.append(chk("plan_background", "ok" if img.is_file() else "fail",
                        f"Background image {img}" + (" exists" if img.is_file() else " is missing"),
                        fix=None if img.is_file() else "Fix background.image in the plan."))
    elif isinstance(bg, dict):
        rows.append(chk("plan_background", "ok", f"Background is a solid color {bg.get('color')}"))
    else:
        rows.append(chk("plan_background", "info", "Plan has no background block"))
    if not plan.get("guests"):
        rows.append(chk("plan_guests", "info", "Plan has no guests block, guest checks skipped"))
        return rows
    try:
        state = guests.load_state(plan["_name"])
    except guests.GuestError as e:
        state = None
        rows.append(chk("plan_guests", "fail", "Guest state unreadable", detail=str(e)[:200]))
    roster = (state or {}).get("guests") or []
    if not roster:
        rows.append(chk("plan_guests", "warn", "Guest roster is empty",
                        fix=f"Run `obsagent guests add {plan['_name']} --name NAME --email ADDR`."))
    else:
        rows.append(chk("plan_guests", "ok", f"{len(roster)} guest(s): {', '.join(g['name'] for g in roster)}"))
        no_mail = [g["name"] for g in roster if not g.get("email")]
        if no_mail:
            rows.append(chk("guest_emails", "warn", "No email address for: " + ", ".join(no_mail),
                            fix=f"Run `obsagent guests set-email {plan['_name']} --name NAME --email ADDR`."))
        else:
            rows.append(chk("guest_emails", "ok", "Every guest has an email address"))
        if any(g["name"].strip().lower() == "test guest" for g in roster):
            rows.append(chk("test_guest", "warn", "\"Test Guest\" is still on the roster",
                            fix=f"Run `obsagent guests remove {plan['_name']} \"Test Guest\"` then `obsagent plan apply {plan['_name']}`."))
    if state is not None:
        acct = guests.keychain_account(plan["_name"])
        if paths.keychain_get(acct):
            rows.append(chk("room_password", "ok", f"Room password present in Keychain ({acct})"))
        else:
            rows.append(chk("room_password", "fail", f"No room password in Keychain ({acct})",
                            fix=f"Run `obsagent guests rotate {plan['_name']}` to create a new room and password."))
    rows.append(_mail_sender_check())
    return rows


def _mail_sender_check() -> dict:
    sender = mailer.load_config().get("sender")
    if not sender:
        return chk("mail_sender", "warn", "No mail sender configured",
                   fix="Run `obsagent guests senders`, then `obsagent guests set-sender ADDR --name NAME`.")
    if not paths.which("osascript"):
        return chk("mail_sender", "info", f"Sender {sender} configured; osascript unavailable, Mail.app check skipped")
    try:
        addrs = {r["address"].lower() for r in mailer.list_senders()}
    except (mailer.MailError, OSError) as e:
        return chk("mail_sender", "warn", f"Sender {sender} configured but Mail.app could not be queried",
                   detail=str(e)[:300], fix="Allow your terminal to control Mail: System Settings > Privacy & Security > Automation.")
    if sender.lower() in addrs:
        return chk("mail_sender", "ok", f"Sender {sender} is a Mail.app account")
    return chk("mail_sender", "warn", f"Sender {sender} is not an enabled Mail.app account",
               fix="Add the account in System Settings > Internet Accounts, or run `obsagent guests set-sender` with a listed address.")


def run_doctor(net: bool = False, plan: str | None = None) -> dict:
    running = paths.obs_running()
    checks = [
        check_obs_installed(),
        check_obs_running(),
        check_config_dir(),
        check_ws_config(),
        check_ws_password(),
        check_ws_reachable(running),
        check_hardware(),
        check_cameras(),
        check_audio(),
        *check_permissions(),
        check_disk(),
        check_keychain(),
        check_browser_hwaccel(),
    ]
    if plan:
        checks.extend(plan_checks(plan))
    if net:
        checks.append(check_network())
    summary = {s: sum(1 for c in checks if c["status"] == s) for s in ("ok", "warn", "fail", "info")}
    return {"checks": checks, "summary": summary, "healthy": summary["fail"] == 0}


def print_text(report: dict) -> None:
    icon = {"ok": "[ ok ]", "warn": "[warn]", "fail": "[FAIL]", "info": "[info]"}
    for c in report["checks"]:
        print(f"{icon[c['status']]} {c['summary']}")
        if c.get("fix") and c["status"] != "ok":
            print(f"        fix: {c['fix']}")
    s = report["summary"]
    print(f"\n{s['ok']} ok, {s['warn']} warn, {s['fail']} fail, {s['info']} info")
