"""Show plans: YAML files describing a stream, validated and applied against OBS."""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from . import displays, paths, profiles, ws

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
LAYOUTS = {"fill", "inset"}
CORNERS = {"top-left", "top-right", "bottom-left", "bottom-right"}
ENTRY_KEYS = {"source", "layout", "corner", "width_pct", "margin_px"}
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
            extra = set(e) - ENTRY_KEYS
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


def apply_plan(plan: dict, cl, dry_run: bool = False, sleep=time.sleep, now=time.monotonic) -> dict:
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
            run.do("SetSceneItemIndex", "set_scene_item_index", sc["name"], iid, idx, sceneName=sc["name"],
                   sceneItemId=iid if iid is not None else "<new item id>", sceneItemIndex=idx)

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
    if run.live:
        result["verify"] = verify_state(plan, cl, scenes, chosen, muted)
    return result


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
