"""Plan background and item locking. All mocked: no OBS."""
import pytest

from conftest import FakeClient
from obsagent_lib import plans


def _plan(tmp_path, **bg):
    img = tmp_path / "bg.png"
    img.write_bytes(b"png")
    if bg.get("image") == "TMP":
        bg["image"] = str(img)
    return {
        "title": "t", "target": "custom", "preset": "youtube-1080p60",
        "scenes": [
            {"name": "Camera", "sources": [{"source": "camera", "layout": "fill"}]},
            {"name": "Both", "sources": [{"source": "screen", "layout": "fill"},
                                          {"source": "camera", "layout": "inset", "corner": "bottom-right",
                                           "width_pct": 25, "margin_px": 40}]},
        ],
        "sources": {"camera": {"name": "StreamCam", "device": "Logitech StreamCam"}, "screen": {"name": "Screen"}},
        "background": bg,
    }


def _client(**kw):
    return FakeClient(inputs=[], kinds=("macos-avcapture", "screen_capture", "color_source_v3", "image_source"), **kw)


def _apply(plan, cl):
    return plans.apply_plan(plan, cl, sleep=lambda s: None)


def _idx(cl, scene, name):
    return next(i["sceneItemIndex"] for i in cl.scene_items[scene] if i["sourceName"] == name)


def test_image_form(tmp_path):
    p = _plan(tmp_path, image="TMP")
    assert plans.validate(p) == []
    cl = _client()
    r = _apply(p, cl)
    assert ("create_input", "Camera", "Background", "image_source") in cl.calls
    assert cl.settings["Background"] == {"file": str(tmp_path / "bg.png")}
    assert ("create_scene_item", "Both", "Background") in cl.calls
    assert any(q["request"] == "SetSceneItemLocked" and q.get("sceneName") == "Both" for q in r["requests"])
    for sc in ("Camera", "Both"):
        bid = next(i["sceneItemId"] for i in cl.scene_items[sc] if i["sourceName"] == "Background")
        t = cl.transforms[(sc, bid)]
        assert (t["positionX"], t["positionY"], t["alignment"]) == (0, 0, 5)
        assert t["boundsType"] == "OBS_BOUNDS_STRETCH" and (t["boundsWidth"], t["boundsHeight"]) == (1920, 1080)
        assert t["locked"] is True


def test_color_form(tmp_path):
    p = _plan(tmp_path, color="#1E2A38")
    assert plans.validate(p) == []
    cl = _client()
    _apply(p, cl)
    assert ("create_input", "Camera", "Background", "color_source_v3") in cl.calls
    assert cl.settings["Background"] == {"color": 0xFF382A1E, "width": 1920, "height": 1080}


def test_color_kind_fallback(tmp_path):
    cl = FakeClient(inputs=[], kinds=("macos-avcapture", "color_source_v2"))
    _apply(_plan(tmp_path, color="#000000"), cl)
    assert ("create_input", "Camera", "Background", "color_source_v2") in cl.calls


def test_custom_name_and_scenes(tmp_path):
    p = _plan(tmp_path, color="#112233", name="Backdrop", scenes=["Camera"])
    cl = _client()
    _apply(p, cl)
    assert any(i["sourceName"] == "Backdrop" for i in cl.scene_items["Camera"])
    assert not any(i["sourceName"] == "Backdrop" for i in cl.scene_items["Both"])


def test_kind_swap_removes_and_recreates(tmp_path):
    cl = FakeClient(inputs=[("Background", "color_source_v3")], scenes=("Camera", "Both"),
                    kinds=("macos-avcapture", "screen_capture", "color_source_v3", "image_source"))
    cl._add_item("Camera", "Background")
    cl._add_item("Both", "Background")
    _apply(_plan(tmp_path, image="TMP"), cl)
    names = [c[0] for c in cl.calls]
    assert ("set_input_name", "Background", "Background (replaced)") in cl.calls
    assert ("remove_input", "Background (replaced)") in cl.calls
    assert not any(i["inputName"] == "Background (replaced)" for i in cl.inputs)
    assert names.index("remove_input") < names.index("create_input", names.index("remove_input"))
    assert next(i for i in cl.inputs if i["inputName"] == "Background")["inputKind"] == "image_source"
    assert sum(1 for i in cl.scene_items["Both"] if i["sourceName"] == "Background") == 1


def test_same_kind_updates_settings(tmp_path):
    cl = FakeClient(inputs=[("Background", "color_source_v3")], scenes=("Camera", "Both"),
                    kinds=("macos-avcapture", "screen_capture", "color_source_v3"))
    cl._add_item("Camera", "Background")
    _apply(_plan(tmp_path, color="#FFFFFF"), cl)
    assert not any(c[0] in ("remove_input", "set_input_name") for c in cl.calls)
    assert cl.settings["Background"]["color"] == 0xFFFFFFFF
    assert any(i["sourceName"] == "Background" for i in cl.scene_items["Both"])


def test_background_is_at_index_zero_and_locked(tmp_path):
    cl = _client()
    _apply(_plan(tmp_path, color="#112233"), cl)
    for sc in ("Camera", "Both"):
        assert _idx(cl, sc, "Background") == 0
    last_idx = [c for c in cl.calls if c[0] == "set_scene_item_index" and c[1] == "Both"][-1]
    assert last_idx[3] == 0  # background ordered after every other item


def test_items_locked_by_default_and_opt_out(tmp_path):
    cl = _client()
    _apply(_plan(tmp_path, color="#112233"), cl)
    locked = {(c[1], c[2]) for c in cl.calls if c[0] == "set_scene_item_locked" and c[3] is True}
    both = {i["sceneItemId"]: i["sourceName"] for i in cl.scene_items["Both"]}
    assert {both[i] for s, i in locked if s == "Both"} == {"Screen", "StreamCam", "Background"}
    cl2 = _client()
    p = _plan(tmp_path, color="#112233")
    p["lock_items"] = False
    _apply(p, cl2)
    locked2 = [c for c in cl2.calls if c[0] == "set_scene_item_locked"]
    assert len(locked2) == 2 and all(c[2] in {i["sceneItemId"] for sc in cl2.scene_items.values() for i in sc
                                                if i["sourceName"] == "Background"} for c in locked2)


def test_dry_run_lists_requests(tmp_path):
    r = plans.apply_plan(_plan(tmp_path, image="TMP"), None, dry_run=True)
    reqs = [q["request"] for q in r["requests"]]
    assert "CreateInput" in reqs and "SetSceneItemLocked" in reqs
    assert any(q.get("inputName") == "Background" and q["request"] == "CreateInput" for q in r["requests"])


def test_verify_includes_background_rows(tmp_path):
    cl = _client()
    r = _apply(_plan(tmp_path, color="#112233"), cl)
    rows = [x for x in r["verify"]["items"] if x["item"] == "Background"]
    assert {x["scene"] for x in rows} == {"Camera", "Both"} and all(x["index"] == 0 for x in rows)
    assert "Background | 0" in plans.format_verify(r["verify"])


@pytest.mark.parametrize("bg,msg", [
    ({"image": "x.png", "color": "#000000"}, "exactly one"),
    ({}, "exactly one"),
    ({"color": "red"}, "#RRGGBB"),
    ({"color": "#12345"}, "#RRGGBB"),
    ({"image": "TMPBAD.gif"}, ".png, .jpg"),
    ({"image": "does/not/exist.png"}, "not found"),
    ({"color": "#000000", "bogus": 1}, "unknown keys"),
    ({"color": "#000000", "scenes": "Camera"}, "scenes"),
])
def test_validation_errors(tmp_path, bg, msg):
    bg = dict(bg)
    if bg.get("image") == "TMPBAD.gif":
        bg["image"] = str(tmp_path / "bg.gif")
    assert msg in " ".join(plans.validate(_plan(tmp_path, **bg)))


def test_relative_image_resolves_against_repo_root(tmp_path):
    assert plans.resolve_bg_image("assets/backgrounds/x.png") == plans.REPO_ROOT / "assets/backgrounds/x.png"


def test_lock_items_must_be_bool(tmp_path):
    p = _plan(tmp_path, color="#000000")
    p["lock_items"] = "yes"
    assert "lock_items" in " ".join(plans.validate(p))
