"""Remote guests (VDO.Ninja). All mocked: no OBS, no real Keychain."""
import json
import os
import re
import stat
from pathlib import Path

import pytest

from conftest import FakeClient
from obsagent_lib import cli, guests, paths, plans

REPO = Path(__file__).resolve().parent.parent
DEV = [{"itemName": "Logitech StreamCam", "itemValue": "cam1"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSAGENT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("OBSAGENT_GUIDES_DIR", str(tmp_path / "guides"))
    kc = {}
    monkeypatch.setattr(paths, "keychain_get", lambda acct: kc.get(acct))
    monkeypatch.setattr(paths, "keychain_set", lambda acct, secret: kc.__setitem__(acct, secret) or True)
    return kc


def run(argv, capsys):
    rc = cli.main(argv)
    return rc, capsys.readouterr().out


def test_generated_formats(env):
    state, g, created = guests.add_guest("show", "Ann")
    assert created
    assert re.fullmatch(r"obsa[a-z0-9]{10}", state["room"])
    assert re.fullmatch(r"[a-z0-9]{8}", g["id"])
    assert re.fullmatch(r"[A-Za-z0-9]{16}", env["vdo:show"])
    assert g["slot"] == 1


def test_state_permissions_and_no_password_in_file(env, tmp_path):
    guests.add_guest("show", "Ann")
    d, f = tmp_path / "cfg" / "shows", tmp_path / "cfg" / "shows" / "show.json"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert env["vdo:show"] not in f.read_text()


def test_max_three_and_slot_reuse(env):
    for n in ("A", "B", "C"):
        guests.add_guest("show", n)
    with pytest.raises(guests.GuestError, match="at most 3"):
        guests.add_guest("show", "D")
    guests.remove_guest("show", "B")
    _, g, _ = guests.add_guest("show", "E")
    assert g["slot"] == 2
    with pytest.raises(guests.GuestError):
        guests.add_guest("show", "a")  # duplicate, case-insensitive


def test_bad_plan_name_rejected(env):
    with pytest.raises(guests.GuestError):
        guests.state_path("../x")


def test_links_masked_and_revealed(env, capsys):
    cli.main(["guests", "add", "show", "--name", "Ann Lee"])
    capsys.readouterr()
    pw = env["vdo:show"]
    rc, o = run(["guests", "links", "show", "--json"], capsys)
    assert rc == 0 and pw not in o
    d = json.loads(o)
    assert "password=********" in d["director_link"]
    g = d["guests"][0]
    assert "label=Ann%20Lee" in g["guest_link"] and "&push=" in g["guest_link"]
    assert g["obs_link"].endswith("&solo") and "&view=" + g["id"] in g["obs_link"]
    rc, o = run(["guests", "links", "show", "Ann Lee", "--reveal", "--json"], capsys)
    d = json.loads(o)
    assert f"password={pw}" in d["guests"][0]["guest_link"]
    assert d["director_link"].startswith("https://vdo.ninja/?director=obsa")


def test_list_and_add_output_never_show_password(env, capsys):
    rc, o = run(["guests", "add", "show", "--name", "Ann", "--json"], capsys)
    assert rc == 0 and env["vdo:show"] not in o
    rc, o = run(["guests", "list", "show"], capsys)
    assert env["vdo:show"] not in o and "Ann" in o


def test_fourth_guest_error_exit(env, capsys):
    for n in "ABC":
        cli.main(["guests", "add", "show", "--name", n])
    capsys.readouterr()
    assert cli.main(["guests", "add", "show", "--name", "D"]) == 1


def test_rotate_changes_everything(env, capsys):
    cli.main(["guests", "add", "show", "--name", "Ann"])
    before = guests.load_state("show"), env["vdo:show"]
    capsys.readouterr()
    rc, o = run(["guests", "rotate", "show", "--json"], capsys)
    j = json.loads(o)
    after = guests.load_state("show"), env["vdo:show"]
    assert rc == 0 and "old links" in j["warning"]
    assert before[0]["room"] != after[0]["room"] and before[1] != after[1]
    assert before[0]["guests"][0]["id"] != after[0]["guests"][0]["id"]


_load = plans.load_plan


def _plan(extra=None):
    p = _load("talking-head")
    p["_name"] = "show"
    p.update(extra or {})
    return p


def test_guide_fills_placeholders(env, tmp_path, capsys):
    cli.main(["guests", "add", "show", "--name", "Ann <b>"])
    p = _plan({"show_time": "Thu 7pm PT", "host_contact": "text 555-0100"})
    out = guests.write_guides("show", p)
    assert len(out) == 1
    f = Path(out[0]["path"])
    assert f.parent == tmp_path / "guides" and stat.S_IMODE(f.stat().st_mode) == 0o600
    text = f.read_text()
    assert "Hi Ann &lt;b&gt;," in text and "Thu 7pm PT" in text and "text 555-0100" in text
    assert "Talking head show" in text
    assert f'<a href="https://vdo.ninja/?room=' in text and "[YOUR GUEST LINK]" not in text
    assert set(out[0]["unfilled_placeholders"]) == {"[TECH CHECK DATE AND TIME]", "[YOUTUBE WATCH LINK]"}
    assert 'class="fill">[TECH CHECK DATE AND TIME]' in text
    # template untouched
    assert "[YOUR GUEST LINK]" in (REPO / "docs" / "guest-guide.html").read_text()


def test_validate_guest_fields():
    base = _plan()
    assert plans.validate(base) == []
    bad = _plan({"guests": {"max": 4, "solo_scenes": "yes", "bogus": 1}, "show_time": 12,
                 "youtube": {"title": "t", "watch_url": "ftp://x"}})
    j = " ".join(plans.validate(bad))
    for w in ("guests.max", "solo_scenes", "unknown keys", "show_time", "watch_url"):
        assert w in j
    no_cam = _plan()
    del no_cam["sources"]["camera"]
    assert "sources.camera" in " ".join(plans.validate(no_cam))


@pytest.mark.parametrize("n,expect", [
    (2, [(0, 270), (960, 270)]),
    (3, [(0, 0), (960, 0), (480, 540)]),
    (4, [(0, 0), (960, 0), (0, 540), (960, 540)]),
])
def test_panel_tiles(n, expect):
    t = plans.panel_tiles(n)
    assert [(x["x"], x["y"]) for x in t] == [(float(a), float(b)) for a, b in expect]
    assert all(x["w"] == 960 and x["h"] == 540 for x in t)


def _client(**kw):
    cl = FakeClient(inputs=[], **kw)
    cl.devices = {"device": list(DEV), "device_id": [{"itemName": "Logitech StreamCam", "itemValue": "m1"}]}
    return cl


def _ctx():
    return guests.plan_context("show", _plan())


def test_apply_builds_panel_and_solo(env):
    guests.add_guest("show", "Ann")
    guests.add_guest("show", "Bob")
    pw = env["vdo:show"]
    cl = _client()
    r = plans.apply_plan(_plan(), cl, sleep=lambda s: None, guest_ctx=_ctx())
    kinds = {i["inputName"]: i["inputKind"] for i in cl.inputs}
    assert kinds["Guest 1: Ann"] == kinds["Guest 2: Bob"] == "browser_source"
    s = cl.settings["Guest 1: Ann"]
    assert s["width"] == 1920 and s["height"] == 1080 and s["reroute_audio"] is True
    assert s["url"].startswith("https://vdo.ninja/?room=obsa") and s["url"].endswith("&solo") and f"password={pw}" in s["url"]
    assert {"Panel", "Guest: Ann", "Guest: Bob"} <= set(cl.scenes)
    # panel: n=3 layout
    pos = {}
    for it in cl.scene_items["Panel"]:
        t = cl.transforms[("Panel", it["sceneItemId"])]
        pos[it["sourceName"]] = (t.get("positionX"), t.get("positionY"), t.get("boundsWidth"), t.get("boundsType"))
    assert pos["StreamCam"][:3] == (0.0, 0.0, 960.0)
    assert pos["Guest 1: Ann"][:2] == (960.0, 0.0)
    assert pos["Guest 2: Bob"][:2] == (480.0, 540.0)
    assert pos["Guest 2: Bob"][3] == "OBS_BOUNDS_SCALE_INNER"
    assert any(i["sourceName"] == "StreamCam Mic" for i in cl.scene_items["Panel"])
    # solo scene: full frame guest plus mic
    solo = {i["sourceName"] for i in cl.scene_items["Guest: Ann"]} - {"Background"}
    assert solo == {"Guest 1: Ann", "StreamCam Mic"}
    ann = next(i for i in cl.scene_items["Guest: Ann"] if i["sourceName"] == "Guest 1: Ann")
    assert cl.transforms[("Guest: Ann", ann["sceneItemId"])]["boundsWidth"] == 1920.0
    # report never contains the password
    assert pw not in json.dumps(r, default=str)
    assert any("********" in json.dumps(q, default=str) for q in r["requests"])


def test_apply_rerun_updates_url_and_prunes_removed(env):
    guests.add_guest("show", "Ann")
    guests.add_guest("show", "Bob")
    cl = _client()
    plans.apply_plan(_plan(), cl, sleep=lambda s: None, guest_ctx=_ctx())
    cl.calls.clear()
    guests.rotate("show")
    guests.remove_guest("show", "Bob")
    r = plans.apply_plan(_plan(), cl, sleep=lambda s: None, guest_ctx=_ctx())
    names = [c[0] for c in cl.calls]
    assert "create_input" not in names and "create_scene" not in names
    assert ("remove_input", "Guest 2: Bob") in cl.calls
    assert r["removed_guest_inputs"] == ["Guest 2: Bob"]
    assert any("Guest: Bob" in n for n in r["notes"])
    assert guests.load_state("show")["room"] in cl.settings["Guest 1: Ann"]["url"]
    # n=2 layout now
    for it in cl.scene_items["Panel"]:
        if it["sourceName"] == "Guest 1: Ann":
            t = cl.transforms[("Panel", it["sceneItemId"])]
            assert (t["positionX"], t["positionY"]) == (960.0, 270.0)


def test_apply_ignores_foreign_inputs_and_no_solo(env):
    guests.add_guest("show", "Ann")
    cl = _client()
    cl.inputs.append({"inputName": "Guestbook", "inputKind": "browser_source"})
    p = _plan({"guests": {"solo_scenes": False}})
    plans.apply_plan(p, cl, sleep=lambda s: None, guest_ctx=guests.plan_context("show", p))
    assert not any(c == ("remove_input", "Guestbook") for c in cl.calls)
    assert "Guest: Ann" not in cl.scenes and "Panel" in cl.scenes


def test_dry_run_masks_url_via_cli(env, monkeypatch, capsys, tmp_path):
    guests.add_guest("show", "Ann")
    monkeypatch.setattr(paths, "obs_running", lambda: False)
    monkeypatch.setattr(plans, "load_plan", lambda n: _plan())
    monkeypatch.setenv("OBSAGENT_OBS_DIR", str(tmp_path / "obs"))
    for extra in ([], ["--json"]):
        rc, o = run(["plan", "apply", "show", "--dry-run", *extra], capsys)
        assert rc == 0
        assert env["vdo:show"] not in o
        assert "Guest 1: Ann" in o and "password=********" in o


def test_nothing_leaks_into_repo(env, monkeypatch, capsys, tmp_path):
    cli.main(["guests", "add", "show", "--name", "Ann"])
    cli.main(["guests", "add", "show", "--name", "Bob"])
    secrets_ = [env["vdo:show"]]
    st = guests.load_state("show")
    secrets_ += [st["room"]] + [g["id"] for g in st["guests"]]
    guests.write_guides("show", _plan())
    cl = _client()
    plans.apply_plan(_plan(), cl, sleep=lambda s: None, guest_ctx=_ctx())
    monkeypatch.setattr(paths, "obs_running", lambda: False)
    monkeypatch.setattr(plans, "load_plan", lambda n: _plan())
    monkeypatch.setenv("OBSAGENT_OBS_DIR", str(tmp_path / "obs"))
    cli.main(["plan", "apply", "show", "--dry-run"])
    cli.main(["guests", "links", "show"])
    capsys.readouterr()
    hits = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fn in files:
            f = Path(root) / fn
            try:
                data = f.read_bytes()
            except OSError:
                continue
            for s in secrets_:
                if s.encode() in data:
                    hits.append((str(f), s[:3] + "..."))
    assert hits == []


def test_guide_cli_reports_unfilled(env, monkeypatch, capsys):
    cli.main(["guests", "add", "show", "--name", "Ann"])
    capsys.readouterr()
    monkeypatch.setattr(plans, "load_plan", lambda n: _plan())
    rc, o = run(["guests", "guide", "show", "--name", "Ann", "--json"], capsys)
    d = json.loads(o)
    assert rc == 0 and env["vdo:show"] not in o
    assert "[TECH CHECK DATE AND TIME]" in d["guides"][0]["unfilled_placeholders"]
    assert cli.main(["guests", "guide", "show", "--name", "Nobody"]) == 1
