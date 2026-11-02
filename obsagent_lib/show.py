"""Show lifecycle helpers: preflight, triage evidence, post-show summary.

Everything here is read-only against OBS except `preflight --record`, which starts and stops a
short recording. Nothing in this module ever starts a stream.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import doctor, guests, paths, plans, ws
from .doctor import chk

MIN_DISK_GB = 20
MIC_FLOOR_DB = -60
VISIBLE = 0.01


# ---- plan helpers ----

def guest_roster(plan_name: str) -> list[dict]:
    st = guests.load_state(plan_name)
    return sorted((st or {}).get("guests") or [], key=lambda g: g["slot"])


def guest_inputs(plan_name: str, name: str | None = None) -> list[str]:
    """OBS input names of a show's guests, optionally only the guest called `name`."""
    st = guests.load_state(plan_name)
    if st is None:
        raise guests.GuestError(f"no guests for plan {plan_name!r}; run `obsagent guests add {plan_name} --name NAME`")
    roster = [guests.find_guest(st, name)] if name else sorted(st["guests"], key=lambda g: g["slot"])
    return [plans.guest_input_name(g) for g in roster]


def expected_scenes(plan: dict, roster: list[dict]) -> list[str]:
    names = [s["name"] for s in plans.normalize_scenes(plan)]
    g = plan.get("guests")
    if g and roster:
        names.append(g.get("panel_scene") or "Panel")
        if g.get("solo_scenes", True):
            names.extend(plans.guest_solo_scene(x) for x in roster)
    return list(dict.fromkeys(names))


def youtube_account(plan: dict) -> str:
    name = (plan.get("youtube") or {}).get("key_name")
    return f"{paths.ACCT_YOUTUBE}:{name}" if name and name != "default" else paths.ACCT_YOUTUBE


def active_key_name(key: str | None) -> str | None:
    """Which stored key account matches the key OBS currently holds (no secret returned)."""
    if not key:
        return None
    for acct in paths.key_registry():
        if paths.keychain_get(acct) == key:
            return acct
    return None


# ---- preflight ----

def _row(rows, *a, **k):
    rows.append(chk(*a, **k))


def run_preflight(plan_name: str, cl, record: int = 0, level_fn=None, sleep=time.sleep) -> dict:
    rows = list(doctor.plan_checks(plan_name))
    rows.append(doctor.check_browser_hwaccel())
    hw_on = doctor.read_browser_hwaccel() == "true"
    plan = None
    try:
        plan = plans.load_plan(plan_name)
    except plans.PlanError:
        pass
    if cl is None:
        _row(rows, "obs_connected", "fail", "Cannot reach OBS over obs-websocket",
             fix="Open OBS, enable the WebSocket server and run `obsagent ws set-password`; see `obsagent doctor`.")
        return _finish(plan_name, rows)
    try:
        v = cl.get_version()
        _row(rows, "obs_connected", "ok", f"Connected to OBS {ws._g(v, 'obs_version', '?')}")
    except Exception as e:
        _row(rows, "obs_connected", "fail", "OBS websocket request failed", detail=str(e)[:200])
        return _finish(plan_name, rows)
    if plan is None or plans.validate(plan):
        _row(rows, "obs_checks", "info", "OBS state checks skipped because the plan is not valid")
        return _finish(plan_name, rows)
    roster = guest_roster(plan["_name"]) if plan.get("guests") else []

    want_profile = plan.get("profile") or plan["_name"]
    cur = ws._g(cl.get_profile_list(), "current_profile_name")
    if cur == want_profile:
        _row(rows, "profile", "ok", f"Profile {cur!r} is active")
    else:
        _row(rows, "profile", "fail", f"Profile is {cur!r}, plan wants {want_profile!r}",
             fix=f"Run `obsagent plan apply {plan_name}`.")

    have_scenes = ws.scenes(cl)
    missing = [s for s in expected_scenes(plan, roster) if s not in have_scenes]
    if missing:
        _row(rows, "scenes", "fail", "Missing scenes: " + ", ".join(missing), fix=f"Run `obsagent plan apply {plan_name}`.")
    else:
        _row(rows, "scenes", "ok", f"All {len(expected_scenes(plan, roster))} plan scenes exist")

    bg = plan.get("background")
    if isinstance(bg, dict):
        bgn = plans.bg_name(plan)
        targets = [t for t in (bg.get("scenes") or [s["name"] for s in plans.normalize_scenes(plan)]) if t in have_scenes]
        lacking = []
        for sc in targets:
            try:
                items = ws._g(cl.get_scene_item_list(sc), "scene_items", []) or []
            except Exception:
                items = []
            if bgn not in {ws._g(i, "sourceName") for i in items}:
                lacking.append(sc)
        if lacking:
            _row(rows, "background", "fail", f"{bgn!r} missing in: " + ", ".join(lacking), fix=f"Run `obsagent plan apply {plan_name}`.")
        else:
            _row(rows, "background", "ok", f"{bgn!r} present in {len(targets)} scene(s)")

    have_inputs = {i["name"] for i in ws.inputs(cl)}
    for g in roster:
        name = plans.guest_input_name(g)
        if name not in have_inputs:
            _row(rows, f"guest:{g['name']}", "fail", f"Guest source {name!r} not in OBS", fix=f"Run `obsagent plan apply {plan_name}`.")
            continue
        r = ws.render_ratio(cl, name)
        if r is not None and r > VISIBLE:
            _row(rows, f"guest:{g['name']}", "ok", f"{name!r} is rendering ({round(r * 100)}% visible)")
        else:
            _row(rows, f"guest:{g['name']}", "info", f"{name!r}: waiting for guest (nothing rendered yet)",
                 fix="If the guest is connected and this stays blank, turn off Browser Source Hardware Acceleration."
                 if hw_on else None)

    aux = plans.DEFAULT_MIC
    if aux in have_inputs:
        muted = bool(ws._g(cl.get_input_mute(aux), "input_muted"))
        _row(rows, "mic_aux", "ok" if muted else "warn", f"{aux} is {'muted' if muted else 'NOT muted'}",
             fix=None if muted else f"Mute {aux} so the default device does not double the host mic.")
    if "mic" in (plan.get("sources") or {}):
        mic = plans.input_name(plan, "mic")
        if mic not in have_inputs:
            _row(rows, "host_mic", "fail", f"Host mic input {mic!r} not in OBS", fix=f"Run `obsagent plan apply {plan_name}`.")
        elif bool(ws._g(cl.get_input_mute(mic), "input_muted")):
            _row(rows, "host_mic", "fail", f"Host mic {mic!r} is muted", fix="Unmute it in the OBS mixer.")
        else:
            lvl = (level_fn or ws.sample_input_level)(mic, 3.0)
            if lvl is None:
                _row(rows, "host_mic", "info", f"Host mic {mic!r} is unmuted; level not sampled (meter events unavailable)")
            elif lvl > MIC_FLOOR_DB:
                _row(rows, "host_mic", "ok", f"Host mic {mic!r} unmuted, peak {lvl} dB over 3s")
            else:
                _row(rows, "host_mic", "warn", f"Host mic {mic!r} unmuted but silent (peak {lvl} dB over 3s)",
                     fix="Speak into the mic, check the input device and Microphone permission for OBS.")

    live = bool(ws._g(cl.get_stream_status(), "output_active"))
    _row(rows, "not_streaming", "fail" if live else "ok", "Already streaming" if live else "Not streaming",
         fix="Stop the stream before preflight unless you meant to be live." if live else None)
    free = ws._g(cl.get_stats(), "available_disk_space")
    if free is not None:
        gb = round(free / 1024, 1)
        _row(rows, "obs_disk", "warn" if gb < MIN_DISK_GB else "ok", f"{gb} GB free on the recording disk",
             fix=f"Free space, keep {MIN_DISK_GB}+ GB." if gb < MIN_DISK_GB else None)

    if plan.get("target") == "youtube":
        rows.append(_service_row(cl, plan))
    else:
        _row(rows, "stream_service", "info", f"Target is {plan.get('target')}, YouTube service check skipped")

    rec = None
    if record and record > 0:
        rec = _record_test(cl, record, rows, sleep)
    return _finish(plan_name, rows, rec)


def _service_row(cl, plan: dict) -> dict:
    try:
        r = cl.get_stream_service_settings()
    except Exception as e:
        return chk("stream_service", "warn", "Could not read the stream service", detail=str(e)[:200])
    s = ws._g(r, "stream_service_settings", {}) or {}
    server, key = s.get("server") or "", s.get("key") or ""
    acct = active_key_name(key)
    want = youtube_account(plan)
    shown = f"key {acct or 'unknown name'} ({paths.mask(key)})" if key else "no key set"
    detail = {"server": re.sub(r"(://[^/]+/[^/]+/).+", r"\1****", server), "key_name": acct, "expected_key_name": want}
    if not key:
        return chk("stream_service", "fail", "Stream service has no key", detail=detail, fix="Run `obsagent plan apply PLAN` or `obsagent stream set-service`.")
    if not server.startswith("rtmps://") or "youtube" not in server:
        return chk("stream_service", "warn", f"Service is not rtmps YouTube; {shown}", detail=detail,
                   fix="Run `obsagent stream set-service --target youtube --rtmps`.")
    if acct != want:
        return chk("stream_service", "warn", f"rtmps YouTube, {shown}; plan expects {want}", detail=detail,
                   fix="Run `obsagent plan apply PLAN` to set the plan's key, or fix youtube.key_name.")
    return chk("stream_service", "ok", f"rtmps YouTube, {shown}", detail=detail)


def _record_test(cl, seconds: int, rows: list, sleep) -> dict:
    if ws._g(cl.get_record_status(), "output_active"):
        _row(rows, "record_test", "warn", "Already recording, record test skipped")
        return {"skipped": True}
    before = ws.stats_dict(cl)
    cl.start_record()
    try:
        mon = ws.monitor(cl, seconds, sleep=sleep)
    finally:
        stopped = cl.stop_record()
    after = ws.stats_dict(cl)
    out_path = ws._g(stopped, "output_path")
    res = {"seconds": seconds, "output_path": out_path, "summary": mon["summary"], "problems": mon["problems"],
           "render_skipped": (after.get("render_skipped_frames") or 0) - (before.get("render_skipped_frames") or 0),
           "encode_skipped": (after.get("output_skipped_frames") or 0) - (before.get("output_skipped_frames") or 0),
           "total_frames": after.get("output_total_frames")}
    s = mon["summary"]
    msg = (f"Recorded {seconds}s: {res['render_skipped']} lagged, {res['encode_skipped']} skipped frames, "
           f"cpu max {s['cpu_max']}%, file {out_path}")
    _row(rows, "record_test", "warn" if mon["problems"] else "ok", msg, detail=mon["problems"] or None,
         fix="; ".join(p["fix"] for p in mon["problems"]) or None)
    return res


def _finish(plan_name: str, rows: list, recording=None) -> dict:
    summary = {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "warn", "fail", "info")}
    rep = {"plan": plan_name, "rows": rows, "summary": summary, "ok": summary["fail"] == 0}
    if recording is not None:
        rep["recording"] = recording
    return rep


def print_preflight(rep: dict) -> None:
    icon = {"ok": "[pass]", "warn": "[warn]", "fail": "[FAIL]", "info": "[info]"}
    for r in rep["rows"]:
        print(f"{icon[r['status']]} {r['summary']}")
        if r.get("fix") and r["status"] != "ok":
            print(f"        fix: {r['fix']}")
    s = rep["summary"]
    print(f"\n{s['ok']} pass, {s['warn']} warn, {s['fail']} fail, {s['info']} info")


# ---- triage ----

SECRET_WORDS = r"(?:password|passwd|pass|pw|key|streamkey|stream_key|token|secret|auth|push)"
LOG_PATTERN = re.compile(r"error|warn|fail|crash|lagged|skipped|dropped|Invalid", re.I)


def scrub(text: str, secrets: list[str] | None = None) -> str:
    """Mask URL password params, key=value secrets, rtmp key paths and known secret values."""
    t = re.sub(r"(?i)([?&]" + SECRET_WORDS + r"=)[^&\s\"']+", r"\1****", text)
    t = re.sub(r"(?i)(\b" + SECRET_WORDS + r"\b[\"']?\s*[:=]\s*[\"']?)[^\s\"',&]+", r"\1****", t)
    t = re.sub(r"(rtmps?://[^/\s]+/[^/\s]+/)[^\s\"']+", r"\1****", t)
    for sec in secrets or []:
        if sec and len(sec) >= 4:
            t = t.replace(sec, "****")
    return t


def _scrub_tree(v, secrets):
    if isinstance(v, str):
        return scrub(v, secrets)
    if isinstance(v, dict):
        return {k: _scrub_tree(x, secrets) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub_tree(x, secrets) for x in v]
    return v


def _known_secrets() -> list[str]:
    out = [paths.keychain_get(paths.ACCT_WS)]
    out += [paths.keychain_get(a) for a in paths.key_registry()]
    return [s for s in out if s]


def latest_log() -> Path | None:
    d = paths.obs_config_dir() / "logs"
    files = sorted((f for f in d.glob("*.txt")), key=lambda f: f.stat().st_mtime) if d.is_dir() else []
    return files[-1] if files else None


def recent_crashes(hours: float = 24, now=None) -> list[str]:
    d = paths.obs_config_dir() / "crashes"
    if not d.is_dir():
        return []
    cutoff = (now or time.time()) - hours * 3600
    return sorted(f.name for f in d.iterdir() if f.stat().st_mtime >= cutoff)


def triage_collect(cl, now=None) -> dict:
    secrets = _known_secrets()
    rep: dict = {"obs_connected": cl is not None}
    log = latest_log()
    rep["log"] = {"path": str(log) if log else None, "matches": []}
    if log:
        try:
            tail = log.read_text(errors="replace").splitlines()[-300:]
        except OSError:
            tail = []
        hits = [scrub(ln.strip(), secrets) for ln in tail if LOG_PATTERN.search(ln)]
        rep["log"]["matches"] = hits[-40:]
    rep["browser_hw_accel"] = doctor.read_browser_hwaccel()
    rep["crashes_24h"] = recent_crashes(now=now)
    if cl is not None:
        for key, fn in (("stats", ws.stats_dict), ("stream", ws.stream_status_dict), ("record", ws.record_status_dict)):
            try:
                rep[key] = fn(cl)
            except Exception as e:
                rep[key] = {"error": str(e)[:200]}
        try:
            rep["profile"] = ws._g(cl.get_profile_list(), "current_profile_name")
            rep["scene"] = ws.current_scene(cl)
            vs = cl.get_video_settings()
            rep["video"] = {k: ws._g(vs, k) for k in ("base_width", "base_height", "output_width", "output_height",
                                                       "fps_numerator", "fps_denominator")}
            out = {}
            for cat, name in (("Output", "Mode"), ("AdvOut", "Encoder"), ("AdvOut", "Track1Bitrate"),
                              ("SimpleOutput", "StreamEncoder"), ("SimpleOutput", "VBitrate")):
                val = ws._g(cl.get_profile_parameter(cat, name), "parameter_value")
                if val is not None:
                    out[f"{cat}.{name}"] = val
            rep["output"] = out
        except Exception as e:
            rep["error"] = str(e)[:200]
    return _scrub_tree(rep, secrets)


def print_triage(rep: dict) -> None:
    print(f"OBS connected: {rep['obs_connected']}")
    for k in ("profile", "scene", "browser_hw_accel"):
        if k in rep:
            print(f"{k}: {rep[k]}")
    for k in ("video", "output", "stream", "record", "stats"):
        if k in rep:
            print(f"{k}: {json.dumps(rep[k], default=str)}")
    print(f"log: {rep['log']['path']}")
    for ln in rep["log"]["matches"]:
        print(f"  {ln}")
    print(f"crashes in the last 24h: {', '.join(rep['crashes_24h']) or 'none'}")
    if rep.get("browser_hw_accel") == "true":
        print("note: BrowserHWAccel=true can blank browser sources; turn it off in OBS Settings > Advanced")


# ---- session log and post-show ----

def session_log_path(date=None) -> Path:
    import datetime as dt
    d = (date or dt.date.today()).isoformat()
    return paths.state_dir() / "sessions" / f"{d}.jsonl"


def sample_line(sample: dict) -> dict:
    import datetime as dt
    st, ss = sample["stats"], sample["stream"]
    return {"type": "sample", "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "cpu": st.get("cpu_usage"), "fps": st.get("active_fps"),
            "render_skipped": st.get("render_skipped_frames"), "encode_skipped": st.get("output_skipped_frames"),
            "live": bool(ss.get("output_active")), "kbps": sample.get("kbps"), "guests": sample.get("guests", [])}


def append_log(path: Path, line: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(line, default=str) + "\n")


def latest_session() -> Path | None:
    d = paths.state_dir() / "sessions"
    files = sorted(d.glob("*.jsonl")) if d.is_dir() else []
    return files[-1] if files else None


def summarize_session(path: Path) -> dict:
    lines = []
    for ln in path.read_text().splitlines():
        try:
            lines.append(json.loads(ln))
        except ValueError:
            continue
    smp = [x for x in lines if x.get("type") == "sample"]
    flagged = sorted({g["name"] for x in smp for g in x.get("guests", []) if g.get("flagged")})
    cpu = [x["cpu"] for x in smp if x.get("cpu") is not None]
    fps = [x["fps"] for x in smp if x.get("fps") is not None]
    problems = sorted({p["id"] for x in lines if x.get("type") == "summary" for p in x.get("problems", [])})
    return {"path": str(path), "samples": len(smp), "first": smp[0]["ts"] if smp else None,
            "last": smp[-1]["ts"] if smp else None, "cpu_max": max(cpu) if cpu else None,
            "fps_min": min(fps) if fps else None, "was_live": any(x.get("live") for x in smp),
            "guests_flagged_blank": flagged, "problems": problems}


def postshow(plan_name: str, rotate: bool = False) -> dict:
    """Summarize the latest session log. Stops nothing. With rotate, issues a new room and password."""
    log = latest_session()
    rep: dict = {"plan": plan_name, "session": summarize_session(log) if log else None}
    if log is None:
        rep["note"] = "no session log found; run `obsagent ws monitor --guests PLAN --log` during the show"
    if rotate:
        st = guests.rotate(plan_name)
        rep["rotated"] = {"room": st["room"], "guests": [g["name"] for g in st["guests"]]}
        rep["reminder"] = (f"Old guest links are dead. Re-run `obsagent plan apply {plan_name}` to update the OBS guest "
                           f"sources, then re-invite guests with `obsagent guests invite {plan_name} --all`.")
    return rep
