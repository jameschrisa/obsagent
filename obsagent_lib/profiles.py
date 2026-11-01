"""Encoding presets and OBS profile application.

Sources of truth (OBS master, fetched while writing this module, copies in src_ref/):
- plugins/mac-videotoolbox/encoder.c: encoder settings keys read by the VideoToolbox encoder are
  "rate_control" (strings "CBR" | "ABR" | "CRF"; CBR only on Apple Silicon hardware encoders),
  "bitrate" (kbps), "keyint_sec", "profile" (H.264: baseline|main|high; HEVC: main|main10),
  "bframes" (bool), "limit_bitrate", "max_bitrate", "quality", "spatial_aq_mode".
- Encoder ids come from VTCopyVideoEncoderList at runtime (encoder.c registers info.id from it).
  Confirmed on this Mac with a Swift probe:
  com.apple.videotoolbox.videoencoder.ave.avc (H.264, hardware),
  com.apple.videotoolbox.videoencoder.ave.hevc (HEVC, hardware),
  com.apple.videotoolbox.videoencoder.h264 (H.264, software).
- frontend/utility/AdvancedOutput.cpp: reads basic.ini [AdvOut] Encoder, AudioEncoder, TrackIndex,
  ApplyServiceSettings, TrackNBitrate (Track1Bitrate..Track6Bitrate), and loads the encoder
  settings from the profile's streamEncoder.json (GetDataFromJsonFile("streamEncoder.json")).
- frontend/settings/OBSBasicSettings.cpp: [Video] BaseCX BaseCY OutputCX OutputCY FPSType FPSCommon
  FPSInt FPSNum FPSDen; [Audio] SampleRate ChannelSetup.
- Not confirmed from source: that [Output] Mode=Advanced is the key for advanced mode (taken from
  long-standing OBS behavior), the AudioEncoder id "CoreAudio_AAC" (seen in the installed
  obs-ffmpeg plugin binary), and the numeric meaning of FPSType (0 = common list, which is what
  we set).
"""
from __future__ import annotations

import configparser
import json
import time
from pathlib import Path

import yaml

from . import paths

ENCODER_IDS = {
    "videotoolbox_h264": "com.apple.videotoolbox.videoencoder.ave.avc",
    "videotoolbox_hevc": "com.apple.videotoolbox.videoencoder.ave.hevc",
    "x264": "obs_x264",
}
AUDIO_ENCODER = "CoreAudio_AAC"
FPS_COMMON = {24: "24 NTSC", 25: "25 PAL", 30: "30", 48: "48", 60: "60"}


class PresetError(Exception):
    pass


def list_presets(presets_dir: Path | None = None) -> list[str]:
    d = presets_dir or paths.PRESETS_DIR
    return sorted(p.stem for p in d.glob("*.yaml"))


def load_preset(name: str, presets_dir: Path | None = None) -> dict:
    d = presets_dir or paths.PRESETS_DIR
    f = d / f"{name}.yaml"
    if not f.exists():
        raise PresetError(f"unknown preset '{name}'. Available: {', '.join(list_presets(d))}")
    data = yaml.safe_load(f.read_text())
    for k in ("encoder", "width", "height", "fps", "video_bitrate_kbps", "audio_bitrate_kbps"):
        if k not in data:
            raise PresetError(f"preset {name} missing field '{k}'")
    if data["encoder"] not in ENCODER_IDS:
        raise PresetError(f"preset {name}: unknown encoder '{data['encoder']}'")
    return data


BITRATE_MIN, BITRATE_MAX = 500, 60000


def with_bitrate(preset: dict, kbps) -> dict:
    """Copy of preset with video_bitrate_kbps overridden (None keeps the preset value)."""
    if kbps is None:
        return preset
    try:
        v = int(kbps)
    except (TypeError, ValueError):
        raise PresetError(f"bitrate must be an integer between {BITRATE_MIN} and {BITRATE_MAX} kbps")
    if isinstance(kbps, bool) or not BITRATE_MIN <= v <= BITRATE_MAX:
        raise PresetError(f"bitrate {kbps} out of range ({BITRATE_MIN}-{BITRATE_MAX} kbps)")
    return {**preset, "video_bitrate_kbps": v}


def encoder_json(preset: dict) -> dict:
    """Contents of streamEncoder.json for the preset."""
    enc = preset["encoder"]
    base = {
        "rate_control": preset.get("rate_control", "CBR"),
        "bitrate": int(preset["video_bitrate_kbps"]),
        "keyint_sec": int(preset.get("keyint_sec", 2)),
        "profile": preset.get("profile", "high"),
    }
    if enc.startswith("videotoolbox"):
        base["bframes"] = bool(preset.get("bframes", False))
    else:
        base["preset"] = "veryfast"
    return base


def ini_changes(preset: dict) -> list[tuple[str, str, str]]:
    """(section, key, value) tuples to write into the profile's basic.ini."""
    fps = int(preset["fps"])
    return [
        ("Output", "Mode", "Advanced"),
        ("AdvOut", "Encoder", ENCODER_IDS[preset["encoder"]]),
        ("AdvOut", "ApplyServiceSettings", "false"),
        ("AdvOut", "TrackIndex", "1"),
        ("AdvOut", "AudioEncoder", AUDIO_ENCODER),
        ("AdvOut", "Track1Bitrate", str(int(preset["audio_bitrate_kbps"]))),
        ("Video", "BaseCX", str(preset["width"])),
        ("Video", "BaseCY", str(preset["height"])),
        ("Video", "OutputCX", str(preset["width"])),
        ("Video", "OutputCY", str(preset["height"])),
        ("Video", "FPSType", "0"),
        ("Video", "FPSCommon", FPS_COMMON.get(fps, str(fps))),
        ("Audio", "SampleRate", str(int(preset.get("sample_rate", 48000)))),
        ("Audio", "ChannelSetup", preset.get("channels", "Stereo")),
    ]


# ---- ini files ----

def _parser() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # keep OBS key case
    return cp


def read_ini(path: Path) -> configparser.ConfigParser:
    cp = _parser()
    if path.exists():
        cp.read(path, encoding="utf-8-sig")
    return cp


def find_profile_dir(name: str) -> Path | None:
    """Profile dirs are named by a sanitized profile name; match on [General] Name first."""
    root = paths.profiles_dir()
    if not root.exists():
        return None
    for d in sorted(root.iterdir()):
        ini = d / "basic.ini"
        if ini.exists():
            cp = read_ini(ini)
            if cp.get("General", "Name", fallback=None) == name:
                return d
    cand = root / name
    return cand if cand.exists() else None


def list_profile_names() -> list[str]:
    root = paths.profiles_dir()
    names = []
    if root.exists():
        for d in sorted(root.iterdir()):
            ini = d / "basic.ini"
            if ini.exists():
                names.append(read_ini(ini).get("General", "Name", fallback=d.name))
    return names


def current_values_from_files(profile: str, changes) -> dict:
    d = find_profile_dir(profile)
    if not d:
        return {}
    cp = read_ini(d / "basic.ini")
    return {(s, k): cp.get(s, k, fallback=None) for s, k, _ in changes}


def current_values_from_ws(cl, changes) -> dict:
    out = {}
    for s, k, _ in changes:
        try:
            r = cl.get_profile_parameter(s, k)
            out[(s, k)] = getattr(r, "parameter_value", None)
        except Exception:
            out[(s, k)] = None
    return out


def diff_rows(changes, current: dict) -> list[dict]:
    rows = []
    for s, k, v in changes:
        cur = current.get((s, k))
        rows.append({"section": s, "key": k, "current": cur, "new": v, "changed": cur != v})
    return rows


def write_files(profile: str, preset: dict) -> dict:
    """Write the profile directly (OBS must not be running)."""
    if paths.obs_running():
        raise RuntimeError("OBS is running; refusing to edit profile files. Quit OBS or use the websocket.")
    d = find_profile_dir(profile) or (paths.profiles_dir() / profile)
    d.mkdir(parents=True, exist_ok=True)
    ini = d / "basic.ini"
    cp = read_ini(ini)
    if not cp.has_section("General"):
        cp.add_section("General")
    cp.set("General", "Name", profile)
    for s, k, v in ini_changes(preset):
        if not cp.has_section(s):
            cp.add_section(s)
        cp.set(s, k, v)
    with ini.open("w", encoding="utf-8") as f:
        cp.write(f, space_around_delimiters=False)
    ej = d / "streamEncoder.json"
    ej.write_text(json.dumps(encoder_json(preset), indent=4) + "\n")
    return {"mode": "files", "profile_dir": str(d), "files": [str(ini), str(ej)]}


class ProfileError(RuntimeError):
    """A profile switch or write could not be done safely."""


SWITCH_TIMEOUT = 30.0
POLL_INTERVAL = 0.25
_sleep = time.sleep
_now = time.monotonic


def current_profile(cl) -> str | None:
    return getattr(cl.get_profile_list(), "current_profile_name", None)


def _is_timeout(e: Exception) -> bool:
    return "timeout" in type(e).__name__.lower() or isinstance(e, TimeoutError)


def wait_for_profile(cl, target: str, timeout: float | None = None) -> None:
    """Poll GetProfileList until currentProfileName == target, else raise ProfileError."""
    limit = SWITCH_TIMEOUT if timeout is None else timeout
    start = _now()
    while True:
        if current_profile(cl) == target:
            return
        if _now() - start >= limit:
            raise ProfileError(
                f"OBS did not switch to profile '{target}' within {limit:g}s "
                f"(current: '{current_profile(cl)}'). Nothing was written after this point.")
        _sleep(POLL_INTERVAL)


def _request_then_wait(cl, request, target: str) -> None:
    try:
        request()
    except Exception as e:
        if not _is_timeout(e):
            raise
        # OBS switches are slow; the request timing out does not mean the switch failed.
    wait_for_profile(cl, target)


def switch_profile(cl, target: str) -> bool:
    """Make `target` current and verify. Returns True if a switch was needed."""
    if current_profile(cl) == target:
        return False
    _request_then_wait(cl, lambda: cl.set_current_profile(target), target)
    return True


def create_profile(cl, name: str) -> None:
    """CreateProfile auto-switches to the new profile; wait until that has happened."""
    _request_then_wait(cl, lambda: cl.create_profile(name), name)


def ensure_current(cl, target: str) -> None:
    cur = current_profile(cl)
    if cur != target:
        raise ProfileError(f"refusing to write: current OBS profile is '{cur}', expected '{target}'")


def write_params(cl, target: str, changes) -> None:
    ensure_current(cl, target)
    for s, k, v in changes:
        cl.set_profile_parameter(s, k, v)


def verify_params(cl, target: str, changes) -> list[dict]:
    ensure_current(cl, target)
    rows = []
    for s, k, v in changes:
        try:
            actual = getattr(cl.get_profile_parameter(s, k), "parameter_value", None)
        except Exception as e:
            actual = f"<error {type(e).__name__}>"
        rows.append({"section": s, "key": k, "expected": v, "actual": actual,
                     "ok": actual is not None and str(actual) == str(v)})
    return rows


def _profile_dir_after_switch(profile: str) -> Path:
    d = find_profile_dir(profile)
    if d is None:
        raise ProfileError(f"cannot find the directory of profile '{profile}' (no basic.ini with [General] Name={profile})")
    return d


def apply_via_ws(cl, profile: str, preset: dict) -> dict:
    """Create/switch to the profile (verified), write params, verify them by reading back.

    streamEncoder.json is written right after we are inside the profile and before any
    parameter writes. OBS loads the encoder file when the profile loads, so the reload
    (switch away and back, each verified) is done only when the file content changed.
    This covers a freshly created profile too, because CreateProfile auto-switches into
    it before we can place the file. Every parameter write is preceded by a check that
    the current profile is the target.
    """
    steps = []
    existing = list(getattr(cl.get_profile_list(), "profiles", []) or [])
    if profile in existing:
        if switch_profile(cl, profile):
            steps.append(f"switched to profile {profile}")
    else:
        create_profile(cl, profile)
        steps.append(f"created profile {profile}")
    changes = ini_changes(preset)
    write_params(cl, profile, changes)
    steps.append("set profile parameters")
    try:
        ensure_current(cl, profile)
        cl.set_video_settings(int(preset["fps"]), 1, preset["width"], preset["height"], preset["width"], preset["height"])
        steps.append("set video settings")
    except ProfileError:
        raise
    except Exception as e:  # fails while an output is active
        steps.append(f"video settings not applied via ws ({type(e).__name__}); ini values still set")
    d = _profile_dir_after_switch(profile)
    ej = d / "streamEncoder.json"
    text = json.dumps(encoder_json(preset), indent=4) + "\n"
    needs_reload = not ej.exists() or ej.read_text() != text
    ej.write_text(text)
    steps.append(f"wrote {ej}")
    if needs_reload:
        others = [p for p in list(getattr(cl.get_profile_list(), "profiles", []) or []) if p != profile]
        if others:
            other = others[0]
            switch_profile(cl, other)
        else:
            other = "obsagent-temp"
            create_profile(cl, other)
            steps.append(f"created helper profile {other} (delete it in OBS if not wanted)")
        switch_profile(cl, profile)
        steps.append("reloaded profile")
    else:
        steps.append("streamEncoder.json unchanged; reload skipped")
    rows = verify_params(cl, profile, changes)
    return {"mode": "websocket", "steps": steps, "verification": rows,
            "verified": all(r["ok"] for r in rows)}


def set_params_via_ws(cl, profile: str, changes, restore: bool = False) -> dict:
    """Set arbitrary (section, key, value) triplets on an existing profile, then verify."""
    pl = cl.get_profile_list()
    if profile not in list(getattr(pl, "profiles", []) or []):
        raise ProfileError(f"profile '{profile}' does not exist. Known: {', '.join(getattr(pl, 'profiles', []) or [])}")
    original = getattr(pl, "current_profile_name", None)
    steps = []
    if switch_profile(cl, profile):
        steps.append(f"switched to profile {profile}")
    write_params(cl, profile, changes)
    steps.append("set profile parameters")
    rows = verify_params(cl, profile, changes)
    if restore and original and original != profile:
        switch_profile(cl, original)
        steps.append(f"restored profile {original}")
    return {"mode": "websocket", "steps": steps, "verification": rows,
            "verified": all(r["ok"] for r in rows)}


def plan_apply(profile: str, preset: dict, cl=None) -> dict:
    """Compute what profile apply would do. cl is a connected ws client or None."""
    changes = ini_changes(preset)
    if cl is not None:
        current = current_values_from_ws(cl, changes)
        existing = list(getattr(cl.get_profile_list(), "profiles", []) or [])
        mode = "websocket"
        exists = profile in existing
    else:
        current = current_values_from_files(profile, changes)
        mode = "files"
        exists = find_profile_dir(profile) is not None
    return {
        "profile": profile,
        "preset": preset["name"],
        "mode": mode,
        "profile_exists": exists,
        "obs_config_dir_exists": paths.obs_config_dir().exists(),
        "obs_running": paths.obs_running(),
        "ini": diff_rows(changes, current),
        "streamEncoder.json": encoder_json(preset),
        "warnings": preset.get("warnings", []),
    }


def already_current(profile: str, preset: dict, pa: dict, cl) -> bool:
    """True when OBS already runs `profile` and every preset value and the encoder file match."""
    if cl is None:
        return False
    try:
        if getattr(cl.get_profile_list(), "current_profile_name", None) != profile:
            return False
    except Exception:
        return False
    # OBS rewrites FPSType itself (for example to 2 = fraction, FPSNum/FPSDen) after a
    # SetVideoSettings call. Treat FPS keys as equal when the live frame rate matches the preset.
    fps_keys = {("Video", "FPSType"), ("Video", "FPSCommon")}
    try:
        vs = cl.get_video_settings()
        live_fps = vs.fps_numerator / vs.fps_denominator
    except Exception:
        live_fps = None
    fps_ok = live_fps is not None and abs(live_fps - float(preset.get("fps", 0))) < 0.01
    for r in pa.get("ini", []):
        if not r.get("changed"):
            continue
        if fps_ok and (r.get("section"), r.get("key")) in fps_keys:
            continue
        return False
    d = find_profile_dir(profile)
    ej = d / "streamEncoder.json" if d else None
    return bool(ej and ej.exists() and ej.read_text() == json.dumps(encoder_json(preset), indent=4) + "\n")
