"""Show lifecycle commands: scene, snapshot, refresh, doctor --plan, preflight, monitor, triage, postshow.
All mocked: no OBS, no Keychain, no Mail.app."""
import json
import os
import time

import pytest

from conftest import FakeClient, make_png
from obsagent_lib import cli, doctor, guests, mailer, paths, plans, show, ws

GUEST1 = "Guest 1: Ann"
KEY = "abcd-efgh-ijkl-mnop"
PLAN_YAML = """title: Test show
target: youtube
preset: youtube-1080p60
profile: Show
scenes:
  - name: Camera
    sources:
      - {source: camera, layout: fill}
sources:
  camera: {name: StreamCam, device: Logitech StreamCam}
  mic: {name: Mic, device: Logitech StreamCam}
background: {color: "#112233"}
guests: {max: 2}
youtube: {rtmps: true, title: Test show}
"""


@pytest.fixture
def env(obs_dir, tmp_path, monkeypatch):
    kc = {"youtube": KEY}
    monkeypatch.setattr(paths, "keychain_get", lambda acct: kc.get(acct))
    monkeypatch.setattr(paths, "keychain_set", lambda acct, secret: kc.__setitem__(acct, secret) or True)
    monkeypatch.setattr(paths, "KEY_REGISTRY", tmp_path / "keys.json")
    monkeypatch.setenv("OBSAGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("OBSAGENT_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    monkeypatch.setattr(ws, "sample_input_level", lambda name, secs: -20.0)
    monkeypatch.setattr(paths, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(mailer, "load_config", lambda: {"sender": "me@example.com"})
    monkeypatch.setattr(mailer, "list_senders", lambda: [{"account": "A", "address": "me@example.com"}])
    plan = tmp_path / "show.yaml"
    plan.write_text(PLAN_YAML)
    guests.add_guest("show", "Ann", 2, "ann@example.com")
    return {"kc": kc, "plan": str(plan), "tmp": tmp_path}


def ready_client():
    cl = FakeClient(profiles=["Show"], current="Show", scenes=["Camera", "Panel", "Guest: Ann"],
                    inputs=[("Mic", "coreaudio_input_capture"), ("Mic/Aux", "coreaudio_input_capture"),
                            ("Background", "color_source_v3"), (GUEST1, "browser_source")])
    cl.muted["Mic/Aux"] = True
    cl.scene_items["Camera"] = [{"sceneItemId": 1, "sourceName": "Background", "sceneItemIndex": 0}]
    return cl


def run(argv, capsys, cl=None, monkeypatch=None):
    rc = cli.main(argv)
    return rc, capsys.readouterr().out


def use(monkeypatch, cl):
    monkeypatch.setattr(ws, "connect", lambda: cl)
    monkeypatch.setattr(paths, "obs_running", lambda: True)


# ---- ws scene / snapshot / refresh ----

def test_scene_switch_verified(env, monkeypatch, capsys):
    cl = ready_client()
    use(monkeypatch, cl)
    rc, out = run(["ws", "scene", "Panel", "--json"], capsys)
    assert rc == 0 and json.loads(out) == {"requested": "Panel", "current": "Panel", "verified": True}
    assert ("set_current_program_scene", "Panel") in cl.calls
    cl.set_current_program_scene = lambda n: None  # OBS ignored it
    rc, _ = run(["ws", "scene", "Camera"], capsys)
    assert rc == 1


def test_snapshot_writes_file_and_ratio(env, monkeypatch, capsys):
    cl = ready_client()
    cl.shots["Camera"] = make_png(10, 10, lambda x, y: 255 if x < 5 else 0)
    use(monkeypatch, cl)
    out_dir = env["tmp"] / "o"
    rc, out = run(["ws", "snapshot", "Camera", "--out", str(out_dir), "--width", "640", "--json"], capsys)
    r = json.loads(out)
    assert rc == 0 and r["visible_ratio"] == 0.5
    assert r["path"].startswith(str(out_dir)) and open(r["path"], "rb").read().startswith(b"\x89PNG")
    assert ("get_source_screenshot", "Camera", "png", 640) in cl.calls
    rc, out = run(["ws", "snapshot"], capsys)  # default: current program scene, default dir
    assert rc == 0 and str(env["tmp"] / "snaps") in out and "Camera-" in out and "visible pixels: 50.0%" in out


def test_snapshot_unrenderable_source(env, monkeypatch, capsys):
    cl = ready_client()
    cl.shot_fail.add("Nope")
    use(monkeypatch, cl)
    assert run(["ws", "snapshot", "Nope"], capsys)[0] == 1


def test_render_ratio_pure_decoder_and_none():
    cl = FakeClient()
    cl.shots["blank"] = make_png(8, 8, lambda x, y: 0)
    cl.shots["half"] = make_png(8, 8, lambda x, y: 255 if y < 2 else 0)
    cl.shot_fail.add("gone")
    assert ws.render_ratio(cl, "blank") == 0.0
    assert ws.render_ratio(cl, "gone") is None
    assert ws._alpha_ratio_pure(cl.shots["half"]) == 0.25
    assert ws._alpha_ratio_pure(b"not a png") is None


def test_refresh_source(env, monkeypatch, capsys):
    cl = ready_client()
    use(monkeypatch, cl)
    rc, _ = run(["ws", "refresh", GUEST1], capsys)
    assert rc == 0 and ("press_input_properties_button", GUEST1, "refreshnocache") in cl.calls
    assert run(["ws", "refresh", "Missing"], capsys)[0] == 1


def test_guests_refresh(env, monkeypatch, capsys):
    guests.add_guest("show", "Bob", 2)
    cl = ready_client()
    cl.inputs.append({"inputName": "Guest 2: Bob", "inputKind": "browser_source"})
    use(monkeypatch, cl)
    rc, out = run(["guests", "refresh", "show", "--json"], capsys)
    assert rc == 0 and [r["source"] for r in json.loads(out)["refreshed"]] == [GUEST1, "Guest 2: Bob"]
    cl.calls.clear()
    rc, _ = run(["guests", "refresh", "show", "--name", "Bob"], capsys)
    assert rc == 0 and cl.calls == [("press_input_properties_button", "Guest 2: Bob", "refreshnocache")]
    cl.inputs = [i for i in cl.inputs if i["inputName"] != GUEST1]
    assert run(["guests", "refresh", "show"], capsys)[0] == 1


# ---- doctor ----

def test_doctor_browser_hwaccel(env, monkeypatch, capsys):
    env_dir = paths.obs_config_dir()
    env_dir.mkdir(parents=True)
    (env_dir / "global.ini").write_text("[General]\nBrowserHWAccel=true\n")
    monkeypatch.setattr(doctor, "check_cameras", lambda: doctor.chk("cameras", "ok", "x"))
    monkeypatch.setattr(doctor, "check_audio", lambda: doctor.chk("audio", "ok", "x"))
    rc, out = run(["doctor", "--json"], capsys)
    c = next(c for c in json.loads(out)["checks"] if c["id"] == "browser_hwaccel")
    assert c["status"] == "warn" and "Settings > Advanced" in c["fix"] or "Advanced" in c["fix"]
    rc, out = run(["doctor"], capsys)
    assert "Browser Source Hardware Acceleration is on" in out
    (env_dir / "global.ini").write_text("[General]\nBrowserHWAccel=false\n")
    assert doctor.check_browser_hwaccel()["status"] == "ok"


def by_id(rows):
    return {c["id"]: c for c in rows}


def test_doctor_plan_all_good(env):
    rows = by_id(doctor.plan_checks(env["plan"]))
    env["kc"]["vdo:show"] = "pw"
    rows = by_id(doctor.plan_checks(env["plan"]))
    assert {c["status"] for c in rows.values()} == {"ok"}
    assert {"plan_valid", "plan_background", "plan_guests", "guest_emails", "room_password", "mail_sender"} <= set(rows)


def test_doctor_plan_problems(env, tmp_path):
    guests.add_guest("show", "Test Guest", 2)
    env["kc"].pop("vdo:show")
    rows = by_id(doctor.plan_checks(env["plan"]))
    assert rows["room_password"]["status"] == "fail"
    assert rows["guest_emails"]["status"] == "warn" and "Test Guest" in rows["guest_emails"]["summary"]
    assert rows["test_guest"]["status"] == "warn"
    bad = tmp_path / "bad.yaml"
    bad.write_text(PLAN_YAML.replace('{color: "#112233"}', '{image: /nope/x.png}'))
    rows = by_id(doctor.plan_checks(str(bad)))
    assert rows["plan_valid"]["status"] == "fail" and rows["plan_background"]["status"] == "fail"
    assert by_id(doctor.plan_checks("does-not-exist"))["plan_valid"]["status"] == "fail"


def test_doctor_plan_mail_cases(env, monkeypatch):
    env["kc"]["vdo:show"] = "pw"
    monkeypatch.setattr(mailer, "list_senders", lambda: [{"account": "A", "address": "other@example.com"}])
    assert by_id(doctor.plan_checks(env["plan"]))["mail_sender"]["status"] == "warn"
    monkeypatch.setattr(paths, "which", lambda n: None)
    assert by_id(doctor.plan_checks(env["plan"]))["mail_sender"]["status"] == "info"
    monkeypatch.setattr(paths, "which", lambda n: "/usr/bin/osascript")
    monkeypatch.setattr(mailer, "load_config", lambda: {})
    assert by_id(doctor.plan_checks(env["plan"]))["mail_sender"]["status"] == "warn"


def test_doctor_plan_empty_roster(env):
    guests.remove_guest("show", "Ann")
    assert by_id(doctor.plan_checks(env["plan"]))["plan_guests"]["status"] == "warn"


def test_doctor_cli_plan_json(env, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check_cameras", lambda: doctor.chk("cameras", "ok", "x"))
    monkeypatch.setattr(doctor, "check_audio", lambda: doctor.chk("audio", "ok", "x"))
    rc, out = run(["doctor", "--plan", env["plan"], "--json"], capsys)
    ids = {c["id"] for c in json.loads(out)["checks"]}
    assert {"plan_valid", "room_password", "browser_hwaccel"} <= ids


# ---- preflight ----

def preflight(env, monkeypatch, capsys, cl, *extra):
    use(monkeypatch, cl)
    env["kc"]["vdo:show"] = "pw"
    rc, out = run(["preflight", env["plan"], "--json", *extra], capsys)
    return rc, json.loads(out)


def test_preflight_ready_passes_and_never_streams(env, monkeypatch, capsys):
    cl = ready_client()
    rc, rep = preflight(env, monkeypatch, capsys, cl)
    rows = by_id(rep["rows"])
    assert rc == 0 and rep["ok"] and rep["summary"]["fail"] == 0
    assert rows["profile"]["status"] == "ok" and rows["scenes"]["status"] == "ok" and rows["background"]["status"] == "ok"
    assert rows["host_mic"]["status"] == "ok" and "-20.0 dB" in rows["host_mic"]["summary"]
    assert rows["mic_aux"]["status"] == "ok" and rows["not_streaming"]["status"] == "ok"
    assert rows["guest:Ann"]["status"] == "ok"
    assert rows["stream_service"]["status"] == "ok" and "youtube" in rows["stream_service"]["summary"]
    assert KEY not in json.dumps(rep) and paths.mask(KEY) in rows["stream_service"]["summary"]
    assert not any(c[0] in ("start_stream", "start_record") for c in cl.calls)


def test_preflight_failures_exit_1(env, monkeypatch, capsys):
    cl = ready_client()
    cl.stream_active = True
    cl.current = "Other"
    cl.scenes.remove("Panel")
    cl.muted["Mic"] = True
    cl.scene_items["Camera"] = []
    rc, rep = preflight(env, monkeypatch, capsys, cl)
    rows = by_id(rep["rows"])
    assert rc == 1 and not rep["ok"]
    for k in ("profile", "scenes", "background", "host_mic", "not_streaming"):
        assert rows[k]["status"] == "fail", k
    assert "Panel" in rows["scenes"]["summary"]
    assert "start_stream" not in [c[0] for c in cl.calls]


def test_preflight_guest_blank_waiting_and_warnings(env, monkeypatch, capsys):
    cl = ready_client()
    cl.shots[GUEST1] = make_png(8, 8, lambda x, y: 0)
    cl.muted["Mic/Aux"] = False
    cl.stream_service = type(cl.stream_service)(stream_service_type="rtmp_custom", stream_service_settings={
        "server": "rtmp://a.rtmp.youtube.com/live2", "key": "zzzz"})
    monkeypatch.setattr(ws, "sample_input_level", lambda n, s: -90.0)
    rc, rep = preflight(env, monkeypatch, capsys, cl)
    rows = by_id(rep["rows"])
    assert rc == 0
    assert rows["guest:Ann"]["status"] == "info" and "waiting for guest" in rows["guest:Ann"]["summary"]
    assert rows["mic_aux"]["status"] == "warn" and rows["host_mic"]["status"] == "warn"
    assert rows["stream_service"]["status"] == "warn" and "zzzz" not in json.dumps(rep["rows"])
    cl.stats_disk = None


def test_preflight_low_disk_and_meter_unavailable(env, monkeypatch, capsys):
    cl = ready_client()
    base = cl.get_stats()
    cl.get_stats = lambda: type(base)(**{**vars(base), "available_disk_space": 5000})
    monkeypatch.setattr(ws, "sample_input_level", lambda n, s: None)
    rc, rep = preflight(env, monkeypatch, capsys, cl)
    rows = by_id(rep["rows"])
    assert rows["obs_disk"]["status"] == "warn" and rows["host_mic"]["status"] == "info"


def test_preflight_obs_unreachable(env, monkeypatch, capsys):
    monkeypatch.setattr(paths, "obs_running", lambda: False)
    env["kc"]["vdo:show"] = "pw"
    rc, out = run(["preflight", env["plan"], "--json"], capsys)
    assert rc == 1 and by_id(json.loads(out)["rows"])["obs_connected"]["status"] == "fail"


def test_preflight_record(env, monkeypatch, capsys):
    cl = ready_client()
    rc, rep = preflight(env, monkeypatch, capsys, cl, "--record", "2")
    names = [c[0] for c in cl.calls]
    assert rc == 0 and names.count("start_record") == 1 and names.count("stop_record") == 1
    assert "start_stream" not in names and "stop_stream" not in names
    r = by_id(rep["rows"])["record_test"]
    assert r["status"] == "ok" and "/tmp/obsagent-test.mkv" in r["summary"]
    assert rep["recording"]["render_skipped"] == 0 and rep["recording"]["output_path"] == "/tmp/obsagent-test.mkv"


def test_preflight_text_output(env, monkeypatch, capsys):
    use(monkeypatch, ready_client())
    env["kc"]["vdo:show"] = "pw"
    rc, out = run(["preflight", env["plan"]], capsys)
    assert rc == 0 and "[pass]" in out and "0 fail" in out


# ---- monitor ----

def test_monitor_guest_blank_detection():
    cl = ready_client()
    cl.shots[GUEST1] = make_png(8, 8)
    cl.shots["Never"] = make_png(8, 8, lambda x, y: 0)
    cl.inputs.append({"inputName": "Never", "inputKind": "browser_source"})
    calls = []

    def hook(_):
        calls.append(1)
        cl.shots[GUEST1] = make_png(8, 8, lambda x, y: 0)  # guest goes blank after the first sample

    r = ws.monitor(cl, 8, interval=2, sleep=hook, guest_sources=[GUEST1, "Never"])
    flags = [[g["flagged"] for g in s["guests"]] for s in r["samples"]]
    assert flags == [[False, False], [False, False], [True, False], [True, False]]
    assert r["samples"][0]["guests"][0]["render_ratio"] == 1.0 and r["samples"][0]["guests"][0]["connected"]
    prob = [p for p in r["problems"] if p["id"] == "guest_blank"]
    assert prob and GUEST1 in prob[0]["detail"] and "Never" not in prob[0]["detail"]


def test_monitor_cli_guests_and_log(env, monkeypatch, capsys):
    cl = ready_client()
    use(monkeypatch, cl)
    rc, out = run(["ws", "monitor", "--seconds", "2", "--guests", "show", "--log", "--json"], capsys)
    j = json.loads(out)
    assert rc == 0 and j["samples"][0]["guests"][0]["name"] == GUEST1
    log = env["tmp"] / "state" / "sessions" / (time.strftime("%Y-%m-%d") + ".jsonl")
    assert j["log"] == str(log)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["type"] for x in lines] == ["sample", "summary"] and lines[0]["guests"][0]["name"] == GUEST1
    explicit = env["tmp"] / "x" / "s.jsonl"
    run(["ws", "monitor", "--seconds", "2", "--log", str(explicit)], capsys)
    run(["ws", "monitor", "--seconds", "2", "--log", str(explicit)], capsys)
    assert len(explicit.read_text().splitlines()) == 4  # appended
    rc, out = run(["ws", "monitor", "--seconds", "2"], capsys)  # no log, no guests
    assert rc == 0 and "log:" not in out


def test_monitor_unknown_guest_plan(env, monkeypatch, capsys):
    use(monkeypatch, ready_client())
    assert run(["ws", "monitor", "--seconds", "2", "--guests", "nope"], capsys)[0] == 1


# ---- triage ----

def test_triage_collect_masks_secrets(env, monkeypatch, capsys):
    env["kc"]["obs-websocket"] = "wspass123"
    logs = paths.obs_config_dir() / "logs"
    logs.mkdir(parents=True)
    (logs / "old.txt").write_text("error old log\n")
    lines = ["info fine"] * 400
    lines[10] = "error before the 300 line window"
    lines[350] = "warning: Invalid url https://vdo.ninja/?room=r&password=Sup3rS3cret&push=abc"
    lines[351] = "error: output rtmps://a.rtmps.youtube.com:443/live2/" + KEY
    lines[352] = "connect failed with wspass123"
    lines[353] = 'streamkey: "TOPSECRETKEY" dropped frames'
    lines[200:260] = [f"skipped frames {i}" for i in range(60)]
    new = logs / "new.txt"
    new.write_text("\n".join(lines) + "\n")
    os.utime(logs / "old.txt", (1, 1))
    crashes = paths.obs_config_dir() / "crashes"
    crashes.mkdir()
    (crashes / "fresh.crash").write_text("x")
    (crashes / "stale.crash").write_text("x")
    os.utime(crashes / "stale.crash", (1, 1))
    (paths.obs_config_dir() / "global.ini").write_text("[General]\nBrowserHWAccel=true\n")
    cl = ready_client()
    cl.params[("Output", "Mode")] = "Advanced"
    use(monkeypatch, cl)
    rc, out = run(["triage", "collect", "--json"], capsys)
    for secret in ("Sup3rS3cret", KEY, "wspass123", "TOPSECRETKEY"):
        assert secret not in out, secret
    rep = json.loads(out)
    assert rc == 0 and rep["log"]["path"].endswith("new.txt") and len(rep["log"]["matches"]) == 40
    assert "before the 300" not in out and "password=****" in out
    assert rep["crashes_24h"] == ["fresh.crash"] and rep["browser_hw_accel"] == "true"
    assert rep["profile"] == "Show" and rep["output"]["Output.Mode"] == "Advanced" and rep["stats"]["cpu_usage"] == 12.0
    rc, text = run(["triage", "collect"], capsys)
    assert rc == 0 and "BrowserHWAccel=true" in text and "Sup3rS3cret" not in text


def test_triage_without_obs(env, monkeypatch, capsys):
    monkeypatch.setattr(paths, "obs_running", lambda: False)
    rc, out = run(["triage", "collect", "--json"], capsys)
    rep = json.loads(out)
    assert rc == 0 and rep["obs_connected"] is False and rep["log"]["path"] is None and "stats" not in rep


# ---- postshow ----

def write_session(env):
    d = env["tmp"] / "state" / "sessions"
    d.mkdir(parents=True)
    (d / "2026-01-01.jsonl").write_text("{}\n")
    f = d / "2026-11-07.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in [
        {"type": "sample", "ts": "t1", "cpu": 10, "fps": 30, "live": True, "guests": [{"name": GUEST1, "flagged": False}]},
        {"type": "sample", "ts": "t2", "cpu": 50, "fps": 25, "live": True, "guests": [{"name": GUEST1, "flagged": True}]},
        {"type": "summary", "problems": [{"id": "guest_blank"}]}]) + "\n")
    return f


def test_postshow_summary_stops_nothing(env, monkeypatch, capsys):
    f = write_session(env)
    monkeypatch.setattr(ws, "connect", lambda: (_ for _ in ()).throw(AssertionError("must not touch OBS")))
    rc, out = run(["show", "postshow", "show", "--json"], capsys)
    s = json.loads(out)["session"]
    assert rc == 0 and s["path"] == str(f) and s["samples"] == 2 and s["cpu_max"] == 50 and s["fps_min"] == 25
    assert s["guests_flagged_blank"] == [GUEST1] and s["problems"] == ["guest_blank"] and "rotated" not in json.loads(out)


def test_postshow_no_log_and_rotate(env, monkeypatch, capsys):
    before = guests.load_state("show")
    rc, out = run(["show", "postshow", "show", "--rotate"], capsys)
    after = guests.load_state("show")
    assert rc == 0 and "no session log" in out and after["room"] != before["room"]
    assert after["guests"][0]["id"] != before["guests"][0]["id"]
    assert "plan apply show" in out and "re-invite" in out.lower() or "guests invite show" in out
    assert run(["show", "postshow", "ghost", "--rotate"], capsys)[0] == 1
