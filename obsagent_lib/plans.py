"""Show plans: YAML files describing a stream, validated and applied against OBS."""
from __future__ import annotations

import re
import time
from pathlib import Path

import yaml

from . import displays, guests, paths, profiles, ws

TARGETS = {"youtube", "zoom", "custom"}
PRIVACY = {"public", "unlisted", "private"}
LATENCY = {"normal", "low", "ultraLow"}

# Input kinds. Confirmed in OBS source (plugins/mac-capture/mac-sck-video-capture.m:
# .id = "screen_capture"; plugins/mac-capture/mac-audio.c: "coreaudio_input_capture";
# plugins/mac-avcapture/plugin-main.m on master: "macos-avcapture"). The installed OBS 33.0.1
# binary contains "macos-avcapture" and "macos-avcapture-fast"; the older "av_capture_input_v2" id
# is not present in it, so candidates are checked against GetInputKindList at apply time.
CAMERA_KINDS = ["macos-avcapture", "macos-avcapture-fast", "av_capture_input_v2", "av_capture_input"]
KINDS = {
    "mic": "coreaudio_input_capture",
    "screen": "screen_capture",
    "browser": "browser_source",
}
LAYOUTS = {"fill", "inset", "tile"}
CORNERS = {"top-left", "top-right", "bottom-left", "bottom-right"}
ENTRY_KEYS = {"source", "layout", "corner", "width_pct", "margin_px"}
TILE_KEYS = {"x", "y", "w", "h"}  # only with layout: tile (explicit rectangle in canvas pixels)
GUEST_KEYS = {"max", "panel_scene", "solo_scenes", "monitor"}
# guests.monitor: audio monitoring on guest inputs only, so the pod producer hears guests via BlackHole.
GUEST_MONITOR = {
    "monitor_and_output": "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT",
    "monitor_only": "OBS_MONITORING_TYPE_MONITOR_ONLY",
    "none": "OBS_MONITORING_TYPE_NONE",
}
BG_KEYS = {"image", "color", "name", "scenes"}
BG_NAME = "Background"
BG_IMAGE_KIND = "image_source"
BG_COLOR_KIND = "color_source_v3"
BG_IMAGE_EXT = {".png", ".jpg", ".jpeg"}
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_GUESTS = 3
GUEST_INPUT_RE = re.compile(r"^Guest (\d+): (.+)$")

# Browser source settings, confirmed in obs-browser master (obs-browser-plugin.cpp,
# obs-browser-source.cpp): input kind id "browser_source"; settings keys "url", "width", "height"
# (ints, 1-8192) and "reroute_audio" (bool, UI label "Control audio via OBS", default false).
GUEST_KIND = "browser_source"
DEFAULT_MIC = "Mic/Aux"
DEVICE_PROPS = {"camera": "device", "mic": "device_id"}  # mac-avcapture "device"; mac-audio.c "device_id"
INPUT_NAMES = {"camera": "Camera", "mic": "Mic", "screen": "Screen", "browser": "Overlay"}


class PlanError(Exception):
    pass


def plan_path(name: str) -> Path:
    p = Path(name)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return p
    return paths.PLANS_DIR / f"{name}.yaml"


def load_plan(name: str) -> dict:
    f = plan_path(name)
    if not f.exists():
        raise PlanError(f"plan not found: {f}")
    data = yaml.safe_load(f.read_text()) or {}
    data.setdefault("_name", f.stem)
    return data


def new_plan(name: str) -> Path:
    dest = paths.PLANS_DIR / f"{name}.yaml"
    if dest.exists():
        raise PlanError(f"plan already exists: {dest}")
    src = paths.PLANS_DIR / "example.yaml"
    text = src.read_text() if src.exists() else "title: New show\ntarget: youtube\npreset: youtube-1080p30\nscenes: []\n"
    dest.write_text(text)
    return dest


def normalize_entry(e) -> dict:
    """A scene source entry is a string (old format) or a dict {source, layout, corner, width_pct, margin_px}."""
    if isinstance(e, dict):
        return dict(e)
    return {"source": e}


def normalize_scenes(plan: dict) -> list[dict]:
    out = []
    declared = [k for k in (plan.get("sources") or {}) if k in INPUT_NAMES]
    for s in plan.get("scenes") or []:
        if isinstance(s, str):
            out.append({"name": s, "sources": [{"source": k} for k in declared if k != "mic"]})
        else:
            out.append({"name": s["name"], "sources": [normalize_entry(e) for e in (s.get("sources") or [])]})
    return out


def input_name(plan: dict, src: str) -> str:
    cfg = (plan.get("sources") or {}).get(src) or {}
    return cfg.get("name") or INPUT_NAMES[src]


def validate(plan: dict) -> list[str]:
    errs = []
    if not plan.get("title"):
        errs.append("title is required")
    if plan.get("target") not in TARGETS:
        errs.append(f"target must be one of {sorted(TARGETS)}")
    try:
        profiles.load_preset(plan.get("preset", ""))
    except profiles.PresetError as e:
        errs.append(str(e))
    if plan.get("video_bitrate_kbps") is not None:
        try:
            profiles.with_bitrate({}, plan["video_bitrate_kbps"])
        except profiles.PresetError as e:
            errs.append(f"video_bitrate_kbps: {e}")
    scenes = normalize_scenes(plan)
    if not scenes:
        errs.append("at least one scene is required")
    known = set(INPUT_NAMES)
    for s in scenes:
        for e in s["sources"]:
            src = e.get("source")
            if src not in known:
                errs.append(f"scene '{s['name']}': unknown source '{src}' (use {sorted(known)})")
                continue
            if src not in (plan.get("sources") or {}):
                errs.append(f"scene '{s['name']}': source '{src}' is not declared under sources")
            extra = set(e) - ENTRY_KEYS - (TILE_KEYS if e.get("layout") == "tile" else set())
            if e.get("layout") == "tile":
                for k in sorted(TILE_KEYS):
                    v = e.get(k)
                    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or (k in "wh" and v <= 0):
                        errs.append(f"scene '{s['name']}' source '{src}': tile needs numeric {k} (x,y >= 0; w,h > 0)")
            if extra:
                errs.append(f"scene '{s['name']}' source '{src}': unknown keys {sorted(extra)}")
            lay = e.get("layout")
            if lay is not None and lay not in LAYOUTS:
                errs.append(f"scene '{s['name']}' source '{src}': layout must be one of {sorted(LAYOUTS)}")
            if e.get("corner") is not None and e["corner"] not in CORNERS:
                errs.append(f"scene '{s['name']}' source '{src}': corner must be one of {sorted(CORNERS)}")
            if "width_pct" in e:
                w = e["width_pct"]
                if isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 < w <= 100:
                    errs.append(f"scene '{s['name']}' source '{src}': width_pct must be a number in (0, 100]")
            if "margin_px" in e:
                m = e["margin_px"]
                if isinstance(m, bool) or not isinstance(m, (int, float)) or m < 0:
                    errs.append(f"scene '{s['name']}' source '{src}': margin_px must be a number >= 0")
    names: dict[str, str] = {}
    for k, v in (plan.get("sources") or {}).items():
        if isinstance(v, dict) and v.get("name") is not None and not isinstance(v["name"], str):
            errs.append(f"sources.{k}.name must be a string")
        if k in INPUT_NAMES:
            n = input_name(plan, k)
            if n in names:
                errs.append(f"sources '{names[n]}' and '{k}' share the OBS input name '{n}'")
            names[n] = k
    yt = plan.get("youtube") or {}
    if plan.get("target") == "youtube":
        if not yt.get("title"):
            errs.append("youtube.title is required for a youtube target")
        if yt.get("privacy", "unlisted") not in PRIVACY:
            errs.append(f"youtube.privacy must be one of {sorted(PRIVACY)}")
        if yt.get("latency", "low") not in LATENCY:
            errs.append(f"youtube.latency must be one of {sorted(LATENCY)}")
    if plan.get("date") and not plan.get("time"):
        errs.append("time is required when date is set")
    errs += validate_guests(plan)
    errs += validate_background(plan)
    if plan.get("lock_items") is not None and not isinstance(plan["lock_items"], bool):
        errs.append("lock_items must be true or false")
    return errs


def resolve_bg_image(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def bg_name(plan: dict) -> str:
    return (plan.get("background") or {}).get("name") or BG_NAME


def validate_background(plan: dict) -> list[str]:
    bg = plan.get("background")
    if bg is None:
        return []
    if not isinstance(bg, dict):
        return ["background must be a mapping (image or color, optional name, scenes)"]
    errs = []
    extra = set(bg) - BG_KEYS
    if extra:
        errs.append(f"background: unknown keys {sorted(extra)}")
    has_i, has_c = bg.get("image") is not None, bg.get("color") is not None
    if has_i == has_c:
        errs.append("background needs exactly one of image or color")
    if has_i:
        img = bg["image"]
        if not isinstance(img, str) or not img.strip():
            errs.append("background.image must be a file path string")
        elif Path(img).suffix.lower() not in BG_IMAGE_EXT:
            errs.append("background.image must be a .png, .jpg or .jpeg file")
        elif not resolve_bg_image(img).is_file():
            errs.append(f"background.image not found: {resolve_bg_image(img)}")
    if has_c and not (isinstance(bg["color"], str) and HEX_COLOR_RE.match(bg["color"])):
        errs.append("background.color must be a #RRGGBB string")
    if "name" in bg and (not isinstance(bg["name"], str) or not bg["name"].strip()):
        errs.append("background.name must be a non-empty string")
    if "scenes" in bg:
        sc = bg["scenes"]
        if not isinstance(sc, list) or not sc or not all(isinstance(x, str) and x.strip() for x in sc):
            errs.append("background.scenes must be a non-empty list of scene names")
    return errs


def color_to_abgr(color: str) -> int:
    """#RRGGBB to the OBS color int 0xFFBBGGRR."""
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return 0xFF000000 | (b << 16) | (g << 8) | r


def pick_color_kind(kinds: list[str] | None) -> str:
    if kinds and BG_COLOR_KIND not in kinds:
        for k in kinds:
            if k.startswith("color_source"):
                return k
    return BG_COLOR_KIND


def validate_guests(plan: dict) -> list[str]:
    errs = []
    for k in ("show_time", "tech_check", "host_contact"):
        if plan.get(k) is not None and not isinstance(plan[k], str):
            errs.append(f"{k} must be a quoted string")
    wu = (plan.get("youtube") or {}).get("watch_url")
    if wu is not None and not (isinstance(wu, str) and re.match(r"^https?://\S+$", wu)):
        errs.append("youtube.watch_url must be an http(s) URL")
    g = plan.get("guests")
    if g is None:
        return errs
    if not isinstance(g, dict):
        return errs + ["guests must be a mapping (max, panel_scene, solo_scenes, monitor)"]
    extra = set(g) - GUEST_KEYS
    if extra:
        errs.append(f"guests: unknown keys {sorted(extra)}")
    m = g.get("max", MAX_GUESTS)
    if isinstance(m, bool) or not isinstance(m, int) or not 1 <= m <= MAX_GUESTS:
        errs.append(f"guests.max must be an integer from 1 to {MAX_GUESTS}")
    if "panel_scene" in g and (not isinstance(g["panel_scene"], str) or not g["panel_scene"].strip()):
        errs.append("guests.panel_scene must be a non-empty string")
    if "solo_scenes" in g and not isinstance(g["solo_scenes"], bool):
        errs.append("guests.solo_scenes must be true or false")
    if "monitor" in g and g["monitor"] not in GUEST_MONITOR:
        errs.append(f"guests.monitor must be one of {', '.join(GUEST_MONITOR)}")
    srcs = plan.get("sources") or {}
    for need in ("camera", "mic"):
        if need not in srcs:
            errs.append(f"guests need sources.{need} (the host camera and mic)")
    return errs


def pick_camera_kind(kinds: list[str] | None) -> str:
    if kinds:
        for k in CAMERA_KINDS:
            if k in kinds:
                return k
    return CAMERA_KINDS[0]


def input_settings(src: str, plan: dict) -> dict:
    cfg = (plan.get("sources") or {}).get(src) or {}
    if src == "browser":
        return {"url": cfg.get("url", ""), "width": 1920, "height": 1080}
    # camera/mic/screen devices are picked in OBS the first time (device ids are machine specific)
    return {}


def pick_device(items: list, wanted: str, what: str) -> tuple[str, str]:
    """Return (itemName, itemValue) of the device whose name contains `wanted` (case-insensitive)."""
    rows = [(str(ws._g(i, "itemName", "") or ""), ws._g(i, "itemValue")) for i in items or []]
    w = wanted.lower()
    hits = [r for r in rows if w in r[0].lower()]
    if not hits:
        avail = ", ".join(repr(n) for n, _ in rows) or "none"
        raise PlanError(f"{what}: no device matching {wanted!r}. Available: {avail}")
    exact = [r for r in hits if r[0].lower() == w]
    return (exact or hits)[0]


def compute_transform(entry: dict, canvas: tuple[int, int], src: tuple[int, int]) -> dict:
    cw, ch = canvas
    t = {"rotation": 0.0, "scaleX": 1.0, "scaleY": 1.0, "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_INNER",
         "boundsAlignment": 0, "cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0}
    if entry.get("layout") == "tile":
        t.update(positionX=float(entry["x"]), positionY=float(entry["y"]),
                 boundsWidth=float(entry["w"]), boundsHeight=float(entry["h"]))
        return t
    if entry.get("layout") == "fill":
        t.update(positionX=0.0, positionY=0.0, boundsWidth=float(cw), boundsHeight=float(ch))
        return t
    pct = float(entry.get("width_pct", 25))
    margin = float(entry.get("margin_px", 0))
    sw, sh = src
    aspect = (sh / sw) if sw and sh else (ch / cw)
    w = cw * pct / 100.0
    h = w * aspect
    corner = entry.get("corner", "bottom-right")
    x = margin if corner.endswith("left") else cw - margin - w
    y = margin if corner.startswith("top") else ch - margin - h
    t.update(positionX=round(x, 2), positionY=round(y, 2), boundsWidth=round(w, 2), boundsHeight=round(h, 2))
    return t


def panel_tiles(n: int, canvas: tuple[int, int] = (1920, 1080)) -> list[dict]:
    """Tile rectangles for n people (host first). Designed on 1920x1080, scaled to the canvas."""
    if n < 2 or n > MAX_GUESTS + 1:
        raise PlanError(f"panel layout supports 2 to {MAX_GUESTS + 1} people, got {n}")
    rects = {
        2: [(0, 270), (960, 270)],
        3: [(0, 0), (960, 0), (480, 540)],
        4: [(0, 0), (960, 0), (0, 540), (960, 540)],
    }[n]
    sx, sy = canvas[0] / 1920.0, canvas[1] / 1080.0
    return [{"layout": "tile", "x": round(x * sx, 2), "y": round(y * sy, 2),
             "w": round(960 * sx, 2), "h": round(540 * sy, 2)} for x, y in rects]


def guest_input_name(g: dict) -> str:
    return f"Guest {g['slot']}: {g['name']}"


def guest_solo_scene(g: dict) -> str:
    return f"Guest: {g['name']}"


class _Runner:
    """Records every request; sends it unless this is a dry run or there is no client."""

    def __init__(self, cl, dry_run):
        self.cl, self.dry = cl, dry_run
        self.requests: list[dict] = []

    @property
    def live(self):
        return self.cl is not None and not self.dry

    def do(self, request: str, method: str, *args, **info):
        self.requests.append({"request": request, "action": request, **info})
        if self.live:
            return getattr(self.cl, method)(*args)
        return None


def _item_map(cl, scene) -> dict:
    try:
        r = cl.get_scene_item_list(scene)
    except Exception:
        return {}
    return {ws._g(i, "sourceName"): ws._g(i, "sceneItemId") for i in (ws._g(r, "scene_items", []) or [])}


def _source_size(cl, scene, item_id, wait: float, sleep, now) -> tuple[int, int, dict]:
    end = now() + wait
    while True:
        t = ws._g(cl.get_scene_item_transform(scene, item_id), "scene_item_transform", {}) or {}
        w, h = int(t.get("sourceWidth") or 0), int(t.get("sourceHeight") or 0)
        if (w and h) or now() >= end:
            return w, h, t
        sleep(0.25)


def apply_plan(plan: dict, cl, dry_run: bool = False, sleep=time.sleep, now=time.monotonic,
               guest_ctx: dict | None = None) -> dict:
    """Ensure scenes, inputs, devices, layout and mic exist. cl may be None only for dry runs
    (then everything is planned). Safe to re-run: existing scenes/inputs are reused and updated."""
    run = _Runner(cl, dry_run)
    notes = []
    existing_scenes: list[str] = []
    existing_inputs: dict[str, str] = {}
    kinds = None
    canvas = (1920, 1080)
    if cl is not None:
        existing_scenes = [ws._g(x, "sceneName") for x in (ws._g(cl.get_scene_list(), "scenes", []) or [])]
        existing_inputs = {ws._g(x, "inputName"): ws._g(x, "inputKind") for x in (ws._g(cl.get_input_list(), "inputs", []) or [])}
        try:
            kinds = list(getattr(cl.get_input_kind_list(False), "input_kinds", []) or [])
        except Exception:
            kinds = None
        try:
            vs = cl.get_video_settings()
            canvas = (int(ws._g(vs, "base_width")), int(ws._g(vs, "base_height")))
        except Exception:
            notes.append("could not read GetVideoSettings; assuming a 1920x1080 canvas")
    srcs = plan.get("sources") or {}
    scenes = normalize_scenes(plan)
    first_scene = scenes[0]["name"] if scenes else None
    mic_cfg = srcs.get("mic") or {}
    global_mic = "mic" in srcs and bool(mic_cfg.get("global"))
    present_inputs = set(existing_inputs)
    items: dict[str, dict] = {}  # scene -> {input name: scene item id}

    for sc in scenes:
        if sc["name"] not in existing_scenes:
            run.do("CreateScene", "create_scene", sc["name"], scene=sc["name"])
            existing_scenes.append(sc["name"])
            items[sc["name"]] = {}
        elif cl is not None:
            items[sc["name"]] = _item_map(cl, sc["name"])
        else:
            items[sc["name"]] = {}

    def base_settings(src):
        if src == "screen":
            # 0 = Display capture. display_uuid must be set explicitly: left empty, OBS logs
            # "init_screen_stream: Invalid target display ID: 0" and captures nothing.
            # Never query its property items over websocket (crashes OBS 33, see displays.py).
            disp = str((srcs.get("screen") or {}).get("display", "main"))
            uuid = displays.main_display_uuid() if disp == "main" else disp
            return {"type": 0, "display_uuid": uuid} if uuid else {"type": 0}
        return input_settings(src, plan)

    def ensure_device(src, name):
        cfg = srcs.get(src) or {}
        want = cfg.get("device")
        prop = DEVICE_PROPS.get(src)
        if not want or not prop or src == "screen":  # never list screen_capture items (crashes OBS 33)
            return None
        run.requests.append({"request": "GetInputPropertiesListPropertyItems", "action": "GetInputPropertiesListPropertyItems",
                             "inputName": name, "propertyName": prop})
        if not run.live:
            run.requests.append({"request": "SetInputSettings", "action": "SetInputSettings", "inputName": name,
                                 "inputSettings": {prop: f"<uid of device matching {want!r}>"}, "overlay": True})
            return None
        resp = cl.get_input_properties_list_property_items(name, prop)
        dname, dval = pick_device(ws._g(resp, "property_items", []) or [], want, f"{src} ({name})")
        run.do("SetInputSettings", "set_input_settings", name, {prop: dval}, True, inputName=name,
               inputSettings={prop: dval}, overlay=True, device=dname)
        return dname

    chosen: dict[str, str | None] = {}
    done_inputs: set[str] = set()

    def ensure_input(src, scene):
        """Make sure the input exists and has a scene item in `scene`. Returns the scene item id."""
        name = input_name(plan, src)
        kind = pick_camera_kind(kinds) if src == "camera" else KINDS[src]
        sm = items.setdefault(scene, {})
        if name in present_inputs:
            if name not in done_inputs:
                st = base_settings(src)
                if st:
                    run.do("SetInputSettings", "set_input_settings", name, st, True, inputName=name,
                           inputSettings=st, overlay=True)
                chosen[name] = ensure_device(src, name)
                done_inputs.add(name)
            if name in sm:
                return sm[name]
            resp = run.do("CreateSceneItem", "create_scene_item", scene, name, True, sceneName=scene, sourceName=name)
            sm[name] = ws._g(resp, "scene_item_id") if resp is not None else None
            if sm[name] is None and run.live:
                sm.update(_item_map(cl, scene))
            return sm.get(name)
        resp = run.do("CreateInput", "create_input", scene, name, kind, base_settings(src), True,
                      sceneName=scene, inputName=name, inputKind=kind, inputSettings=base_settings(src))
        present_inputs.add(name)
        done_inputs.add(name)
        sm[name] = ws._g(resp, "scene_item_id") if resp is not None else None
        if sm[name] is None and run.live:
            sm.update(_item_map(cl, scene))
        chosen[name] = ensure_device(src, name)
        return sm.get(name)

    lock_items = plan.get("lock_items", True) is not False

    def lock(scene, iid):
        if lock_items:
            run.do("SetSceneItemLocked", "set_scene_item_locked", scene, iid, True, sceneName=scene,
                   sceneItemId=iid if iid is not None else "<new item id>", sceneItemLocked=True)

    layout_rows = []
    for sc in scenes:
        order = []
        for e in sc["sources"]:
            iid = ensure_input(e["source"], sc["name"])
            order.append((e, iid))
        if global_mic:
            ensure_input("mic", sc["name"])
        for idx, (e, iid) in enumerate(order):
            name = input_name(plan, e["source"])
            if e.get("layout"):
                if run.live and iid is not None:
                    w, h, _ = _source_size(cl, sc["name"], iid, 3.0, sleep, now)
                    if not (w and h):
                        notes.append(f"{sc['name']}/{name}: source size still 0 after 3s; assumed canvas aspect")
                else:
                    w = h = 0
                tr = compute_transform(e, canvas, (w, h))
                run.do("SetSceneItemTransform", "set_scene_item_transform", sc["name"], iid, tr,
                       sceneName=sc["name"], sceneItemId=iid if iid is not None else "<new item id>",
                       sceneItemTransform=tr)
                layout_rows.append((sc["name"], name, e["layout"], tr))
                lock(sc["name"], iid)
            run.do("SetSceneItemIndex", "set_scene_item_index", sc["name"], iid, idx, sceneName=sc["name"],
                   sceneItemId=iid if iid is not None else "<new item id>", sceneItemIndex=idx)

    guest_scenes: list[dict] = []
    removed_guests: list[str] = []
    gcfg = plan.get("guests")
    if gcfg and guest_ctx is not None:
        guest_scenes, removed_guests = _apply_guests(plan, gcfg, guest_ctx, run, cl, canvas, notes, existing_scenes,
                                                     present_inputs, done_inputs, items, ensure_input, lock)
    elif gcfg:
        notes.append("guests: no show state yet; run `obsagent guests add <plan> --name NAME` first")

    if plan.get("background"):
        _apply_background(plan, run, cl, canvas, notes, kinds, existing_inputs, present_inputs, existing_scenes,
                          items, [sc["name"] for sc in scenes] + [g["name"] for g in guest_scenes])

    if "mic" in srcs and not global_mic:
        mn = input_name(plan, "mic")
        if mn not in present_inputs and first_scene:
            ensure_input("mic", first_scene)

    muted = []
    if plan.get("replace_default_mic"):
        mn = input_name(plan, "mic") if "mic" in srcs else None
        if DEFAULT_MIC in existing_inputs and DEFAULT_MIC != mn:
            run.do("SetInputMute", "set_input_mute", DEFAULT_MIC, True, inputName=DEFAULT_MIC, inputMuted=True)
            muted.append(DEFAULT_MIC)
            notes.append(f'muted the default "{DEFAULT_MIC}" input (not deleted)')
        else:
            notes.append(f'replace_default_mic: no "{DEFAULT_MIC}" input found, nothing muted')

    if first_scene:
        run.do("SetCurrentProgramScene", "set_current_program_scene", first_scene, sceneName=first_scene)

    if "screen" in srcs:
        notes.append("screen: display_uuid set from CoreGraphics (main display) unless the plan gives a UUID.")
    result = {"actions": run.requests, "requests": run.requests, "notes": notes}
    if removed_guests:
        result["removed_guest_inputs"] = removed_guests
    if run.live:
        result["verify"] = verify_state(plan, cl, scenes + guest_scenes, chosen, muted)
    return result


def _apply_background(plan, run, cl, canvas, notes, kinds, existing_inputs, present_inputs, existing_scenes, items,
                      default_scenes):
    """One shared Background input at the bottom of every target scene, sized to the canvas and locked.
    Runs after all other ordering so index 0 is final."""
    bg = plan["background"]
    name = bg_name(plan)
    if bg.get("image") is not None:
        kind = BG_IMAGE_KIND
        st = {"file": str(resolve_bg_image(bg["image"]))}
    else:
        kind = pick_color_kind(kinds)
        st = {"color": color_to_abgr(bg["color"]), "width": canvas[0], "height": canvas[1]}
    targets = list(dict.fromkeys(bg.get("scenes") or default_scenes))
    for t in list(targets):
        if t not in existing_scenes and not (cl is None or run.dry):
            notes.append(f"background: scene '{t}' does not exist, skipped")
            targets.remove(t)
    have = name in present_inputs
    if have and existing_inputs.get(name) not in (None, kind):
        # OBS can keep a removed source alive while scenes still reference it, so a
        # plain RemoveInput + CreateInput fails with code 601 (name in use). Rename the
        # old source out of the way, pull it from every scene, then remove it.
        old = f"{name} (replaced)"
        run.do("SetInputName", "set_input_name", name, old, inputName=name, newInputName=old)
        for sc, sm in items.items():
            if sm.get(name) is not None:
                run.do("RemoveSceneItem", "remove_scene_item", sc, sm[name], sceneName=sc,
                       sceneItemId=sm[name])
        run.do("RemoveInput", "remove_input", old, inputName=old)
        present_inputs.discard(name)
        for sm in items.values():
            sm.pop(name, None)
        have = False
        notes.append(f"background: replaced existing '{name}' input ({existing_inputs.get(name)} -> {kind})")
    created = have
    if have:
        run.do("SetInputSettings", "set_input_settings", name, st, True, inputName=name, inputSettings=st,
               overlay=True)
    tr = {"positionX": 0, "positionY": 0, "alignment": 5, "boundsType": "OBS_BOUNDS_STRETCH",
          "boundsWidth": canvas[0], "boundsHeight": canvas[1]}
    for scene in targets:
        sm = items.setdefault(scene, {})
        iid = sm.get(name)
        if iid is None:
            if not created:
                resp = run.do("CreateInput", "create_input", scene, name, kind, st, True, sceneName=scene,
                              inputName=name, inputKind=kind, inputSettings=st)
                created = True
            else:
                resp = run.do("CreateSceneItem", "create_scene_item", scene, name, True, sceneName=scene,
                              sourceName=name)
            iid = ws._g(resp, "scene_item_id") if resp is not None else None
            if iid is None and run.live:
                iid = _item_map(cl, scene).get(name)
            sm[name] = iid
        ref = iid if iid is not None else "<new item id>"
        run.do("SetSceneItemIndex", "set_scene_item_index", scene, iid, 0, sceneName=scene, sceneItemId=ref,
               sceneItemIndex=0)
        run.do("SetSceneItemTransform", "set_scene_item_transform", scene, iid, tr, sceneName=scene,
               sceneItemId=ref, sceneItemTransform=tr)
        run.do("SetSceneItemLocked", "set_scene_item_locked", scene, iid, True, sceneName=scene, sceneItemId=ref,
               sceneItemLocked=True)
    present_inputs.add(name)


def _apply_guests(plan, gcfg, ctx, run, cl, canvas, notes, existing_scenes, present_inputs, done_inputs, items,
                  ensure_input, lock=lambda scene, iid: None):
    """Guest browser sources, the panel scene, optional solo scenes, and pruning of removed guests.
    URLs sent to OBS carry the password; the recorded requests carry the masked form."""
    state = {"room": ctx["room"]}
    password = ctx["password"]
    roster = sorted(ctx["guests"], key=lambda g: g["slot"])
    wanted = {guest_input_name(g) for g in roster}
    panel = gcfg.get("panel_scene") or "Panel"
    solo = bool(gcfg.get("solo_scenes", True))
    mon_type = GUEST_MONITOR[gcfg.get("monitor", "monitor_and_output")]
    monitored: set[str] = set()
    made: list[dict] = []

    def ensure_scene(name):
        if name not in existing_scenes:
            run.do("CreateScene", "create_scene", name, scene=name)
            existing_scenes.append(name)
            items[name] = {}
        elif name not in items:
            items[name] = _item_map(cl, name) if cl is not None else {}
        if not any(m["name"] == name for m in made):
            made.append({"name": name})

    def ensure_guest_input(g, scene):
        name = guest_input_name(g)
        sm = items.setdefault(scene, {})
        real = guests.obs_link(state, g, password)
        masked = guests.obs_link(state, g, guests.MASK)
        st = {"url": real, "width": 1920, "height": 1080, "reroute_audio": True}
        shown = {**st, "url": masked}
        def set_monitor():
            # guest inputs only; once per input per apply
            if name not in monitored:
                run.do("SetInputAudioMonitorType", "set_input_audio_monitor_type", name, mon_type,
                       inputName=name, monitorType=mon_type)
                monitored.add(name)

        if name in present_inputs:
            if name not in done_inputs:
                run.do("SetInputSettings", "set_input_settings", name, st, True, inputName=name,
                       inputSettings=shown, overlay=True)
                done_inputs.add(name)
            set_monitor()
            if name in sm:
                return sm[name]
            resp = run.do("CreateSceneItem", "create_scene_item", scene, name, True, sceneName=scene, sourceName=name)
        else:
            resp = run.do("CreateInput", "create_input", scene, name, GUEST_KIND, st, True, sceneName=scene,
                          inputName=name, inputKind=GUEST_KIND, inputSettings=shown)
            present_inputs.add(name)
            done_inputs.add(name)
            set_monitor()
        sm[name] = ws._g(resp, "scene_item_id") if resp is not None else None
        if sm[name] is None and run.live:
            sm.update(_item_map(cl, scene))
        return sm.get(name)

    def place(scene, rows):
        for idx, (iid, entry) in enumerate(rows):
            ref = iid if iid is not None else "<new item id>"
            if entry:
                tr = compute_transform(entry, canvas, (0, 0))
                run.do("SetSceneItemTransform", "set_scene_item_transform", scene, iid, tr, sceneName=scene,
                       sceneItemId=ref, sceneItemTransform=tr)
                lock(scene, iid)
            run.do("SetSceneItemIndex", "set_scene_item_index", scene, iid, idx, sceneName=scene,
                   sceneItemId=ref, sceneItemIndex=idx)

    removed = []
    if cl is not None:
        for nm in list(present_inputs):
            if GUEST_INPUT_RE.match(nm) and nm not in wanted:
                run.do("RemoveInput", "remove_input", nm, inputName=nm)
                present_inputs.discard(nm)
                done_inputs.discard(nm)
                for sm in items.values():
                    sm.pop(nm, None)
                removed.append(nm)
        if removed:
            notes.append("removed guest inputs no longer in the roster: " + ", ".join(removed))
            keep = {guest_solo_scene(g) for g in roster}
            stale = [s for s in existing_scenes if s.startswith("Guest: ") and s not in keep]
            if stale:
                notes.append("stale guest scenes left in place (delete in OBS if unwanted): " + ", ".join(stale))

    if roster:
        ensure_scene(panel)
        tiles = panel_tiles(1 + len(roster), canvas)
        rows = [(ensure_input("camera", panel), tiles[0])]
        for g, tile in zip(roster, tiles[1:]):
            rows.append((ensure_guest_input(g, panel), tile))
        rows.append((ensure_input("mic", panel), None))
        place(panel, rows)
        if solo:
            for g in roster:
                sc = guest_solo_scene(g)
                ensure_scene(sc)
                gid = ensure_guest_input(g, sc)
                place(sc, [(gid, {"layout": "fill"}), (ensure_input("mic", sc), None)])
        notes.append("guest sources use 'Control audio via OBS' (reroute_audio); URLs are masked in this report")
    return made, removed


def verify_state(plan: dict, cl, scenes: list[dict], chosen: dict, muted: list) -> dict:
    rows = []
    for sc in scenes:
        try:
            lst = ws._g(cl.get_scene_item_list(sc["name"]), "scene_items", []) or []
        except Exception:
            lst = []
        for it in sorted(lst, key=lambda i: ws._g(i, "sceneItemIndex", 0) or 0):
            iid = ws._g(it, "sceneItemId")
            try:
                t = ws._g(cl.get_scene_item_transform(sc["name"], iid), "scene_item_transform", {}) or {}
            except Exception:
                t = {}
            rows.append({"scene": sc["name"], "item": ws._g(it, "sourceName"), "index": ws._g(it, "sceneItemIndex"),
                         "x": t.get("positionX"), "y": t.get("positionY"),
                         "w": t.get("boundsWidth") or t.get("width"), "h": t.get("boundsHeight") or t.get("height"),
                         "bounds": t.get("boundsType")})
    mutes = {}
    for n in set(muted) | ({input_name(plan, "mic")} if "mic" in (plan.get("sources") or {}) else set()):
        try:
            mutes[n] = bool(ws._g(cl.get_input_mute(n), "input_muted"))
        except Exception:
            mutes[n] = None
    return {"items": rows, "devices": {k: v for k, v in chosen.items() if v}, "mutes": mutes}


def format_verify(v: dict) -> str:
    L = ["scene | item | idx | x | y | w | h | bounds"]
    for r in v.get("items", []):
        L.append(f"{r['scene']} | {r['item']} | {r['index']} | {r['x']} | {r['y']} | {r['w']} | {r['h']} | {r['bounds']}")
    for k, d in (v.get("devices") or {}).items():
        L.append(f"device: {k} -> {d}")
    for k, m in (v.get("mutes") or {}).items():
        L.append(f"muted: {k} = {m}")
    return "\n".join(L)


def export_md(plan: dict) -> str:
    preset = None
    try:
        preset = profiles.load_preset(plan.get("preset", ""))
    except profiles.PresetError:
        pass
    L = [f"# {plan.get('title', plan['_name'])}", ""]
    when = " ".join(str(x) for x in (plan.get("date"), plan.get("time"), plan.get("timezone")) if x)
    L += [f"- When: {when or 'TBD'}", f"- Target: {plan.get('target')}", f"- Profile: {plan.get('profile', plan['_name'])}"]
    if preset:
        L.append(f"- Preset: {preset['name']} ({preset['width']}x{preset['height']} at {preset['fps']} fps, "
                 f"{preset['video_bitrate_kbps']} kbps video, {preset['audio_bitrate_kbps']} kbps audio)")
    L += ["", "## Scenes", ""]
    for s in normalize_scenes(plan):
        L.append(f"- {s['name']}: {', '.join(e['source'] for e in s['sources']) or 'no sources'}")
    L += ["", "## Sources", ""]
    for k, v in (plan.get("sources") or {}).items():
        detail = ", ".join(f"{a}={b}" for a, b in (v or {}).items() if b)
        L.append(f"- {k}{': ' + detail if detail else ''}")
    L += ["", "## Checklist", ""]
    L += [f"- [ ] {c}" for c in plan.get("checklist") or []]
    yt = plan.get("youtube")
    if yt:
        L += ["", "## YouTube broadcast", ""]
        for k in ("title", "description", "privacy", "latency"):
            if k in yt:
                L.append(f"- {k}: {yt[k]}")
    if preset and preset.get("warnings"):
        L += ["", "## Warnings", ""] + [f"- {w}" for w in preset["warnings"]]
    return "\n".join(L) + "\n"
