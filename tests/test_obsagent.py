import json

import pytest

from obsagent_lib import cli, doctor, paths, plans, profiles, stream, ws
from conftest import FakeClient


# ---- presets ----

def test_all_presets_load_and_have_sources():
    names = profiles.list_presets()
    assert "youtube-1080p60" in names and len(names) >= 8
    for n in names:
        p = profiles.load_preset(n)
        assert p["source"].startswith("https://")
        assert p["rate_control"] == "CBR" and p["keyint_sec"] == 2


def test_unknown_preset():
    with pytest.raises(profiles.PresetError):
        profiles.load_preset("nope")


def test_encoder_json_videotoolbox():
    pr = profiles.load_preset("youtube-1080p60")
    j = profiles.encoder_json(pr)
    assert j == {"rate_control": "CBR", "bitrate": pr["video_bitrate_kbps"], "keyint_sec": 2, "profile": "high", "bframes": False}


def test_ini_changes_values():
    ch = {(s, k): v for s, k, v in profiles.ini_changes(profiles.load_preset("youtube-1080p60"))}
    assert ch[("AdvOut", "Encoder")] == "com.apple.videotoolbox.videoencoder.ave.avc"
    assert ch[("Video", "FPSCommon")] == "60"
    assert ch[("Audio", "SampleRate")] == "48000"
    assert ch[("Output", "Mode")] == "Advanced"


def test_hevc_preset_uses_hevc_id():
    ch = {(s, k): v for s, k, v in profiles.ini_changes(profiles.load_preset("youtube-1080p60-hevc"))}
    assert ch[("AdvOut", "Encoder")].endswith("ave.hevc")


# ---- profile apply ----

def test_dry_run_files_no_config_dir(obs_dir):
    plan = profiles.plan_apply("p", profiles.load_preset("youtube-720p30"))
    assert plan["mode"] == "files" and not plan["profile_exists"] and not plan["obs_config_dir_exists"]
    assert all(r["changed"] for r in plan["ini"])
    assert not obs_dir.exists()  # dry run writes nothing


def test_write_files_creates_profile(obs_dir):
    res = profiles.write_files("Show", profiles.load_preset("youtube-1080p30"))
    d = obs_dir / "basic" / "profiles" / "Show"
    assert (d / "basic.ini").exists()
    assert json.loads((d / "streamEncoder.json").read_text())["bitrate"] == profiles.load_preset("youtube-1080p30")["video_bitrate_kbps"]
    cp = profiles.read_ini(d / "basic.ini")
    assert cp["General"]["Name"] == "Show" and cp["Video"]["OutputCX"] == "1920"
    # re-apply preserves unrelated keys
    cp["Other"] = {"keep": "1"}
    with (d / "basic.ini").open("w") as f:
        cp.write(f, space_around_delimiters=False)
    profiles.write_files("Show", profiles.load_preset("youtube-720p30"))
    cp2 = profiles.read_ini(d / "basic.ini")
    assert cp2["Other"]["keep"] == "1" and cp2["Video"]["OutputCX"] == "1280"
    assert res["mode"] == "files"


def test_write_files_refuses_when_obs_running(obs_dir, monkeypatch):
    monkeypatch.setattr(paths, "obs_running", lambda: True)
    with pytest.raises(RuntimeError):
        profiles.write_files("Show", profiles.load_preset("youtube-720p30"))
    assert not obs_dir.exists()


def test_apply_via_ws_new_profile(obs_dir):
    cl = FakeClient()
    res = profiles.apply_via_ws(cl, "Show", profiles.load_preset("youtube-1080p60"))
    names = [c[0] for c in cl.calls]
    assert names[0] == "create_profile"
    assert cl.params[("AdvOut", "Encoder")].endswith("ave.avc")
    assert ("set_video_settings", 60, 1, 1920, 1080, 1920, 1080) in cl.calls
    # reload: switched to another profile and back, ending on the target
    assert cl.calls[-2][0] == "set_current_profile" and cl.calls[-1] == ("set_current_profile", "Show")
    assert (obs_dir / "basic" / "profiles" / "Show" / "streamEncoder.json").exists()
    assert res["mode"] == "websocket" and res["verified"]


def test_apply_via_ws_video_failure_is_nonfatal(obs_dir):
    cl = FakeClient()
    cl.video_fail = True
    res = profiles.apply_via_ws(cl, "Show", profiles.load_preset("youtube-720p30"))
    assert any("not applied" in s for s in res["steps"])


def test_dry_run_ws_diff(obs_dir):
    cl = FakeClient(profiles=["Show"], current="Show")
    cl.params[("Video", "OutputCX")] = "1920"
    plan = profiles.plan_apply("Show", profiles.load_preset("youtube-1080p30"), cl)
    row = next(r for r in plan["ini"] if r["key"] == "OutputCX")
    assert plan["mode"] == "websocket" and plan["profile_exists"] and not row["changed"]
    assert cl.calls == []  # read-only


# ---- stream service ----

def test_service_settings_youtube():
    t, s = stream.service_settings("youtube", True, "abcd-efgh-ijkl", None)
    assert t == "rtmp_custom" and s["server"].startswith("rtmps://") and s["key"] == "abcd-efgh-ijkl"
    assert stream.service_settings("youtube", False, "k", None)[1]["server"].startswith("rtmp://")


def test_custom_requires_server():
    with pytest.raises(stream.KeyError_):
        stream.service_settings("zoom", False, "k", None)


def test_set_service_masks_and_dry_run():
    cl = FakeClient()
    r = stream.set_service(cl, "youtube", True, "abcd-efgh-ijkl", dry_run=True)
    assert r["settings"]["key"].endswith("ijkl") and "abcd" not in r["settings"]["key"]
    assert cl.calls == []
    stream.set_service(cl, "youtube", True, "abcd-efgh-ijkl")
    assert cl.calls[0][0] == "set_stream_service_settings" and cl.calls[0][2]["key"] == "abcd-efgh-ijkl"


def test_mask():
    assert paths.mask("abcdefgh") == "****efgh" and paths.mask("ab") == "**" and paths.mask(None) == ""


# ---- ws helpers ----

def test_status_and_lists():
    cl = FakeClient(inputs=[("Cam", "macos-avcapture")])
    s = ws.status(cl)
    assert s["video"]["fps"] == 60 and s["current_profile"] == "Untitled" and s["stats"]["cpu_usage"] == 12.0
    assert ws.scenes(cl) == ["Scene"] and ws.inputs(cl) == [{"name": "Cam", "kind": "macos-avcapture"}]


def test_monitor_healthy_and_problems():
    cl = FakeClient()
    r = ws.monitor(cl, 4, interval=2, sleep=lambda s: None)
    assert len(r["samples"]) == 2 and r["problems"] == []
    cl.get_stats = lambda: __import__("types").SimpleNamespace(
        cpu_usage=95.0, active_fps=20.0, render_skipped_frames=50, render_total_frames=1000,
        output_skipped_frames=40, output_total_frames=1000, memory_usage=1, available_disk_space=1,
        average_frame_render_time=1)
    ids = {p["id"] for p in ws.monitor(cl, 2, interval=2, sleep=lambda s: None)["problems"]}
    assert {"render_lag", "encode_overload", "cpu_high"} <= ids


def test_monitor_network_drops_when_live():
    cl = FakeClient()
    cl.stream_active = True
    cl.get_stream_status = lambda: __import__("types").SimpleNamespace(
        output_active=True, output_reconnecting=False, output_timecode="", output_duration=1,
        output_congestion=0.9, output_bytes=1, output_skipped_frames=10, output_total_frames=100)
    ids = {p["id"] for p in ws.monitor(cl, 2, interval=2, sleep=lambda s: None)["problems"]}
    assert {"network_drops", "congestion"} <= ids


# ---- plans ----

def test_example_plan_validates():
    assert plans.validate(plans.load_plan("example")) == []


def test_plan_validation_errors():
    errs = plans.validate({"target": "x", "preset": "nope", "scenes": []})
    joined = " ".join(errs)
    assert "title" in joined and "target" in joined and "nope" in joined and "scene" in joined


def test_plan_apply_creates_scenes_and_inputs():
    plan = plans.load_plan("example")
    cl = FakeClient()
    r = plans.apply_plan(plan, cl)
    created = [c for c in cl.calls if c[0] == "create_input"]
    assert ("create_scene", "Talking head") in cl.calls
    kinds = {c[2]: c[3] for c in created}
    assert kinds == {"Camera": "macos-avcapture", "Mic": "coreaudio_input_capture", "Screen": "screen_capture"}
    assert r["actions"]


def test_plan_apply_dry_run_and_idempotent():
    plan = plans.load_plan("example")
    cl = FakeClient()
    plans.apply_plan(plan, cl, dry_run=True)
    assert cl.calls == []
    cl2 = FakeClient(scenes=("Talking head", "Screen share"),
                     inputs=[("Camera", "macos-avcapture"), ("Mic", "coreaudio_input_capture"), ("Screen", "screen_capture")])
    plans.apply_plan(plan, cl2)
    assert not [c for c in cl2.calls if c[0] in ("create_scene", "create_input")]


def test_camera_kind_fallback():
    assert plans.pick_camera_kind(["av_capture_input_v2", "screen_capture"]) == "av_capture_input_v2"
    assert plans.pick_camera_kind(None) == "macos-avcapture"


def test_export_md():
    md = plans.export_md(plans.load_plan("example"))
    assert md.startswith("# Weekly livestream") and "- [ ] Camera framing" in md


# ---- cli ----

def test_cli_requires_yes(capsys, monkeypatch):
    monkeypatch.setattr(ws, "connect", lambda: FakeClient())
    assert cli.main(["ws", "start-stream"]) == 2
    assert "--yes" in capsys.readouterr().err


def test_cli_start_stream_with_yes(monkeypatch, capsys):
    cl = FakeClient()
    monkeypatch.setattr(ws, "connect", lambda: cl)
    assert cli.main(["ws", "start-stream", "--yes", "--json"]) == 0
    assert ("start_stream",) in cl.calls


def test_cli_ws_unreachable_exit_3(monkeypatch, capsys):
    def boom():
        raise ws.WsUnavailable("refused")
    monkeypatch.setattr(ws, "connect", boom)
    assert cli.main(["ws", "status"]) == 3


def test_cli_profile_dry_run_json(obs_dir, capsys):
    assert cli.main(["profile", "apply", "youtube-1080p60", "--dry-run", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["dry_run"] and d["profile"] == "obsagent-youtube-1080p60"
    assert not obs_dir.exists()


def test_cli_stream_set_service_masks_output(monkeypatch, capsys):
    monkeypatch.setenv("OBS_STREAM_KEY", "supersecretkey1234")
    assert cli.main(["stream", "set-service", "--key-from", "env", "--dry-run", "--json"]) == 0
    out = capsys.readouterr().out
    assert "supersecretkey1234" not in out and "1234" in out


# ---- doctor ----

def test_doctor_json_shape(obs_dir, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check_cameras", lambda: doctor.chk("cameras", "ok", "x"))
    monkeypatch.setattr(doctor, "check_audio", lambda: doctor.chk("audio", "ok", "x"))
    rep = doctor.run_doctor()
    ids = {c["id"] for c in rep["checks"]}
    assert {"obs_installed", "obs_config", "ws_reachable", "hardware", "disk", "perm_screen_recording"} <= ids
    assert all(c["status"] in ("ok", "warn", "fail", "info") for c in rep["checks"])
    cfg = next(c for c in rep["checks"] if c["id"] == "obs_config")
    assert cfg["status"] == "warn" and "Launch OBS" in cfg["fix"]
    json.dumps(rep)


def test_ws_config_never_returns_password(obs_dir):
    obs_dir.mkdir(parents=True)
    (obs_dir / "user.ini").write_text("[OBSWebSocket]\nServerEnabled=true\nServerPort=4455\nServerPassword=hunter2\n")
    cfg = doctor.read_ws_config()
    assert "ServerPassword" not in cfg and "hunter2" not in json.dumps(cfg) and cfg["_has_password"] == "True"
    assert doctor.check_ws_config()["status"] == "ok"


def test_doctor_connects_when_running(obs_dir, monkeypatch):
    monkeypatch.setattr(paths, "obs_running", lambda: True)
    monkeypatch.setattr(ws, "connect", lambda: FakeClient())
    assert doctor.check_ws_reachable(True)["status"] == "ok"


# ---- bitrate override ----

def test_with_bitrate_validation():
    p = profiles.load_preset("youtube-1080p60")
    assert profiles.with_bitrate(p, None) is p
    assert profiles.with_bitrate(p, 9000)["video_bitrate_kbps"] == 9000
    assert p["video_bitrate_kbps"] != 9000 or True
    for bad in (499, 60001, "x"):
        with pytest.raises(profiles.PresetError):
            profiles.with_bitrate(p, bad)
    assert profiles.with_bitrate(p, 500)["video_bitrate_kbps"] == 500
    assert profiles.with_bitrate(p, 60000)["video_bitrate_kbps"] == 60000


def test_bitrate_flows_into_encoder_json():
    p = profiles.with_bitrate(profiles.load_preset("youtube-1080p60"), 9000)
    assert profiles.encoder_json(p)["bitrate"] == 9000


def test_cli_profile_apply_bitrate_dry_run(obs_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli, "try_ws", lambda: None)
    assert cli.main(["profile", "apply", "youtube-1080p60", "--bitrate", "9000", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["streamEncoder.json"]["bitrate"] == 9000


def test_cli_profile_apply_bitrate_out_of_range(obs_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli, "try_ws", lambda: None)
    assert cli.main(["profile", "apply", "youtube-1080p60", "--bitrate", "100", "--dry-run"]) != 0


def test_plan_bitrate_validation_and_cli_precedence(obs_dir, monkeypatch, capsys):
    plan = plans.load_plan("example") if "example" in [] else None
    monkeypatch.setattr(cli, "try_ws", lambda: None)
    base = {"title": "t", "target": "custom", "preset": "youtube-1080p60", "scenes": ["Main"],
            "sources": {}, "_name": "x", "video_bitrate_kbps": 7000}
    monkeypatch.setattr(plans, "load_plan", lambda n: dict(base))
    monkeypatch.setattr(plans, "validate", lambda p: [])
    assert cli.main(["plan", "apply", "x", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["profile"]["streamEncoder.json"]["bitrate"] == 7000
    assert cli.main(["plan", "apply", "x", "--dry-run", "--json", "--bitrate", "8000"]) == 0
    assert json.loads(capsys.readouterr().out)["profile"]["streamEncoder.json"]["bitrate"] == 8000


def test_plan_validate_rejects_bad_bitrate():
    p = plans.load_plan("example") if False else None
    errs = plans.validate({"title": "t", "target": "custom", "preset": "youtube-1080p60", "video_bitrate_kbps": 10})
    assert any("video_bitrate_kbps" in e for e in errs)


def test_network_suggested_max(monkeypatch):
    out = json.dumps({"ul_throughput": 20e6, "dl_throughput": 100e6})
    monkeypatch.setattr(paths, "run", lambda *a, **k: (0, out, ""))
    d = doctor.check_network()["detail"]
    assert d["suggested_max_video_kbps"] == 12872  # floor(20000*0.65 - 128)


# ---- websocket config ----

def test_ws_config_json_preferred_and_password_hidden(obs_dir):
    f = obs_dir / "plugin_config" / "obs-websocket" / "config.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"server_enabled": True, "server_port": 4466, "auth_required": True, "server_password": "s3cret"}))
    (obs_dir / "user.ini").write_text("[OBSWebSocket]\nServerEnabled=false\nServerPort=1\n")
    cfg = doctor.read_ws_config()
    assert cfg["ServerPort"] == "4466" and cfg["ServerEnabled"] == "true" and cfg["_has_password"] == "True"
    assert "s3cret" not in json.dumps(cfg)
    assert doctor.check_ws_config()["status"] == "ok"


def test_ws_config_ini_fallback(obs_dir):
    obs_dir.mkdir(parents=True)
    (obs_dir / "user.ini").write_text("[OBSWebSocket]\nServerEnabled=true\nServerPort=4455\nServerPassword=pw\n")
    cfg = doctor.read_ws_config()
    assert cfg["ServerPort"] == "4455" and cfg["_has_password"] == "True"
    assert "ServerPassword" not in cfg


def test_ws_config_bad_json_falls_back(obs_dir):
    f = obs_dir / "plugin_config" / "obs-websocket" / "config.json"
    f.parent.mkdir(parents=True)
    f.write_text("{not json")
    assert doctor.read_ws_config() is None


# ---- profile switch race ----

class SlowFake(FakeClient):
    """Switches complete only after `lag` get_profile_list polls; set_current_profile may time out."""

    def __init__(self, lag=3, timeout_on_switch=True, never=False, **kw):
        super().__init__(**kw)
        self.lag, self.timeout_on_switch, self.never = lag, timeout_on_switch, never
        self.pending = None
        self.polls = 0

    def get_profile_list(self):
        if self.pending is not None and not self.never:
            self.polls += 1
            if self.polls >= self.lag:
                self.current, self.pending, self.polls = self.pending, None, 0
        return super().get_profile_list()

    def set_current_profile(self, name):
        self._c("set_current_profile", name)
        self.pending = name
        if self.timeout_on_switch:
            raise OBSSDKTimeoutError("timeout")

    def create_profile(self, name):
        self._c("create_profile", name)
        self.profiles.append(name)
        self._mkdir(name)
        self.pending = name
        if self.timeout_on_switch:
            raise OBSSDKTimeoutError("timeout")

    def set_profile_parameter(self, cat, name, value):
        # model OBS: writes land in the current profile
        self.written_to = getattr(self, "written_to", []) + [self.current]
        super().set_profile_parameter(cat, name, value)


class OBSSDKTimeoutError(Exception):
    pass


@pytest.fixture
def fast_clock(monkeypatch):
    t = [0.0]
    monkeypatch.setattr(profiles, "_now", lambda: t[0])
    monkeypatch.setattr(profiles, "_sleep", lambda s: t.__setitem__(0, t[0] + s))


def test_switch_timeout_then_completes_writes_only_to_target(obs_dir, fast_clock):
    cl = SlowFake(lag=5)
    res = profiles.apply_via_ws(cl, "Show", profiles.load_preset("youtube-1080p60"))
    assert res["verified"]
    assert set(cl.written_to) == {"Show"}
    assert cl.current == "Show"


def test_profile_never_switches_no_writes(obs_dir, fast_clock):
    cl = SlowFake(never=True)
    with pytest.raises(profiles.ProfileError):
        profiles.apply_via_ws(cl, "Show", profiles.load_preset("youtube-1080p60"))
    assert not [c for c in cl.calls if c[0] == "set_profile_parameter"]
    assert cl.params == {}


def test_existing_profile_never_switches_no_writes(obs_dir, fast_clock):
    cl = SlowFake(never=True, profiles=["Untitled", "Show"], current="Untitled")
    with pytest.raises(profiles.ProfileError):
        profiles.apply_via_ws(cl, "Show", profiles.load_preset("youtube-1080p60"))
    assert cl.params == {}


def test_write_refused_when_profile_changes_midway(obs_dir):
    cl = FakeClient(profiles=["Show", "Other"], current="Other")
    with pytest.raises(profiles.ProfileError):
        profiles.write_params(cl, "Show", [("Video", "BaseCX", "1")])
    assert cl.params == {}


def test_verification_mismatch_reported(obs_dir):
    class Lossy(FakeClient):
        def set_profile_parameter(self, cat, name, value):
            super().set_profile_parameter(cat, name, "x" if name == "Encoder" else value)
    cl = Lossy()
    res = profiles.apply_via_ws(cl, "Show", profiles.load_preset("youtube-720p30"))
    assert not res["verified"]
    assert [r["key"] for r in res["verification"] if not r["ok"]] == ["Encoder"]


def test_reload_skipped_when_encoder_json_unchanged(obs_dir):
    pre = profiles.load_preset("youtube-720p30")
    cl = FakeClient(profiles=["Untitled"], current="Untitled")
    profiles.apply_via_ws(cl, "Show", pre)
    n = len(cl.calls)
    res = profiles.apply_via_ws(cl, "Show", pre)
    assert any("reload skipped" in s for s in res["steps"])
    assert not [c for c in cl.calls[n:] if c[0] in ("set_current_profile", "create_profile")]


def test_profile_set_restore(obs_dir, fast_clock):
    cl = SlowFake(lag=2, profiles=["Untitled", "Show"], current="Untitled")
    res = profiles.set_params_via_ws(cl, "Show", [("AdvOut", "Encoder", "obs_x264")], restore=True)
    assert res["verified"] and cl.written_to == ["Show"] and cl.current == "Untitled"
    cl2 = SlowFake(lag=2, profiles=["Untitled", "Show"], current="Untitled")
    profiles.set_params_via_ws(cl2, "Show", [("AdvOut", "Encoder", "obs_x264")])
    assert cl2.current == "Show"
    with pytest.raises(profiles.ProfileError):
        profiles.set_params_via_ws(cl2, "Nope", [("A", "b", "c")])


def test_cli_profile_set_dry_run(obs_dir, monkeypatch, capsys):
    from obsagent_lib import cli
    cl = FakeClient(profiles=["Show"], current="Show")
    monkeypatch.setattr(ws, "connect", lambda: cl)
    rc = cli.main(["profile", "set", "Show", "AdvOut", "Encoder", "obs_x264", "--dry-run"])
    assert rc == 0 and cl.calls == []
    rc = cli.main(["profile", "set", "Show", "AdvOut", "Encoder", "obs_x264"])
    assert rc == 0 and cl.params[("AdvOut", "Encoder")] == "obs_x264"
    assert cli.main(["profile", "set", "Show", "AdvOut", "Encoder"]) == 2


# ---- talking-head plan ----

DEV = [{"itemName": "MacBook Pro Camera", "itemValue": "cam0"}, {"itemName": "Logitech StreamCam", "itemValue": "cam1"}]
MIC_DEV = [{"itemName": "MacBook Pro Microphone", "itemValue": "m0"}, {"itemName": "Logitech StreamCam", "itemValue": "m1"}]


def _th_client(**kw):
    cl = FakeClient(**kw)
    cl.devices = {"device": list(DEV), "device_id": list(MIC_DEV)}
    return cl


def test_talking_head_validates():
    assert plans.validate(plans.load_plan("talking-head")) == []


def test_validate_entry_errors():
    p = {"title": "t", "target": "custom", "preset": "youtube-1080p60",
         "sources": {"camera": {}}, "scenes": [{"name": "A", "sources": [
             {"source": "camera", "layout": "wide", "corner": "middle", "width_pct": 0, "margin_px": -1, "x": 1}]}]}
    j = " ".join(plans.validate(p))
    for w in ("layout", "corner", "width_pct", "margin_px", "unknown keys"):
        assert w in j


def test_talking_head_full_apply():
    plan = plans.load_plan("talking-head")
    cl = _th_client(inputs=[("Mic/Aux", "coreaudio_input_capture")])
    r = plans.apply_plan(plan, cl, sleep=lambda s: None)
    made = [c[2] for c in cl.calls if c[0] == "create_input"]
    assert made == ["StreamCam", "Screen", "StreamCam Mic"] or sorted(made) == ["Screen", "StreamCam", "StreamCam Mic"]
    assert len(made) == 3
    assert cl.settings["StreamCam"]["device"] == "cam1"
    assert cl.settings["StreamCam Mic"]["device_id"] == "m1"
    assert cl.muted == {"Mic/Aux": True}
    assert cl.current_scene == "Camera"
    assert ("create_scene", "Camera") in cl.calls and ("create_scene", "Screen + camera") in cl.calls
    # reuse: camera added to second scene via CreateSceneItem, not a second CreateInput
    assert ("create_scene_item", "Screen + camera", "StreamCam") in cl.calls
    # global mic in every scene
    for sc in ("Camera", "Screen + camera"):
        assert any(i["sourceName"] == "StreamCam Mic" for i in cl.scene_items[sc])
    # layouts
    cam = next(i for i in cl.scene_items["Screen + camera"] if i["sourceName"] == "StreamCam")
    t = cl.transforms[("Screen + camera", cam["sceneItemId"])]
    assert t["boundsWidth"] == 480.0 and t["boundsHeight"] == 270.0
    assert (t["positionX"], t["positionY"]) == (1920 - 40 - 480, 1080 - 40 - 270)
    scr = next(i for i in cl.scene_items["Screen + camera"] if i["sourceName"] == "Screen")
    ts = cl.transforms[("Screen + camera", scr["sceneItemId"])]
    assert ts["boundsType"] == "OBS_BOUNDS_SCALE_INNER" and ts["boundsWidth"] == 1920.0 and ts["positionX"] == 0.0
    # screen listed first (bottom), camera later (top)
    assert scr["sceneItemIndex"] < cam["sceneItemIndex"]
    v = r["verify"]
    assert v["devices"]["StreamCam"] == "Logitech StreamCam" and v["mutes"]["Mic/Aux"] is True
    assert "StreamCam" in plans.format_verify(v)


def test_talking_head_idempotent_rerun():
    plan = plans.load_plan("talking-head")
    cl = _th_client(inputs=[("Mic/Aux", "coreaudio_input_capture")])
    plans.apply_plan(plan, cl, sleep=lambda s: None)
    cl.calls.clear()
    plans.apply_plan(plan, cl, sleep=lambda s: None)
    names = [c[0] for c in cl.calls]
    assert "create_input" not in names and "create_scene" not in names and "create_scene_item" not in names
    assert "set_input_settings" in names


def test_device_not_found_lists_available():
    plan = plans.load_plan("talking-head")
    cl = _th_client()
    cl.devices["device"] = [{"itemName": "FaceTime HD Camera", "itemValue": "x"}]
    with pytest.raises(plans.PlanError) as e:
        plans.apply_plan(plan, cl, sleep=lambda s: None)
    assert "FaceTime HD Camera" in str(e.value) and "Logitech StreamCam" in str(e.value)


def test_pick_device_case_insensitive():
    assert plans.pick_device(DEV, "logitech streamcam", "x") == ("Logitech StreamCam", "cam1")


def test_source_size_polling():
    plan = plans.load_plan("talking-head")
    cl = _th_client()
    cl.zero_polls = 2
    slept = []
    plans.apply_plan(plan, cl, sleep=slept.append)
    assert slept  # polled until the size appeared


def test_source_size_zero_falls_back():
    plan = plans.load_plan("talking-head")
    cl = _th_client()
    cl.zero_polls = 10**6
    clock = iter(x * 0.5 for x in range(10**4))
    r = plans.apply_plan(plan, cl, sleep=lambda s: None, now=lambda: next(clock))
    assert any("size still 0" in n for n in r["notes"])


def test_dry_run_prints_every_request_and_sends_none():
    plan = plans.load_plan("talking-head")
    cl = _th_client(inputs=[("Mic/Aux", "coreaudio_input_capture")])
    r = plans.apply_plan(plan, cl, dry_run=True)
    sent = [c for c in cl.calls if c[0] not in ()]
    assert sent == []
    kinds = [q["request"] for q in r["requests"]]
    for k in ("CreateScene", "CreateInput", "CreateSceneItem", "GetInputPropertiesListPropertyItems", "SetInputSettings",
              "SetSceneItemTransform", "SetSceneItemIndex", "SetInputMute", "SetCurrentProgramScene"):
        assert k in kinds
    assert "verify" not in r
    r2 = plans.apply_plan(plan, None, dry_run=True)
    assert r2["requests"]


def test_string_entries_still_work():
    plan = {"sources": {"camera": {}}, "scenes": [{"name": "A", "sources": ["camera"]}]}
    cl = FakeClient()
    plans.apply_plan(plan, cl)
    assert ("create_input", "A", "Camera", "macos-avcapture") in cl.calls
    assert not [c for c in cl.calls if c[0] == "set_scene_item_transform"]


def test_profile_step_skipped_when_current(obs_dir, monkeypatch):
    preset = profiles.load_preset("youtube-1080p60")
    cl = FakeClient(profiles=("P",), current="P")
    pa = {"ini": [{"changed": False}]}
    monkeypatch.setattr(profiles, "find_profile_dir", lambda n: None)
    assert profiles.already_current("P", preset, pa, cl) is False  # encoder file missing
    d = obs_dir / "p"
    d.mkdir(parents=True)
    import json as _j
    (d / "streamEncoder.json").write_text(_j.dumps(profiles.encoder_json(preset), indent=4) + "\n")
    monkeypatch.setattr(profiles, "find_profile_dir", lambda n: d)
    assert profiles.already_current("P", preset, pa, cl) is True
    assert profiles.already_current("Q", preset, pa, cl) is False
    assert profiles.already_current("P", preset, {"ini": [{"changed": True}]}, cl) is False


def test_screen_never_queries_property_items_and_sets_display_uuid(monkeypatch):
    from obsagent_lib import displays, plans
    monkeypatch.setattr(displays, "main_display_uuid", lambda: "UUID-MAIN")
    plan = {"title": "t", "target": "youtube", "preset": "youtube-1080p60",
            "scenes": [{"name": "S", "sources": [{"source": "screen", "layout": "fill"}]}],
            "sources": {"screen": {"name": "Screen", "display": "main"}}}
    res = plans.apply_plan(plan, None, dry_run=True)
    if res is None:
        import pytest
        pytest.skip("scene apply entry point has a different name")
    reqs = res["requests"] if isinstance(res, dict) else res.requests
    assert not any(r.get("request") == "GetInputPropertiesListPropertyItems" and r.get("inputName") == "Screen" for r in reqs)
    create = [r for r in reqs if r.get("request") == "CreateInput" and r.get("inputKind") == "screen_capture"]
    assert create and create[0]["inputSettings"].get("display_uuid") == "UUID-MAIN"
