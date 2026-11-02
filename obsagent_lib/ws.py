"""obs-websocket v5 helpers built on obsws-python.

API names verified against the installed obsws-python (ReqClient(**kwargs) with host, port,
password, timeout; snake_case methods; responses are dataclasses with snake_case attrs,
e.g. get_stats() -> cpu_usage, active_fps, render_skipped_frames, render_total_frames,
output_skipped_frames, output_total_frames, available_disk_space).
Tests replace `connect` with a fake client.
"""
from __future__ import annotations

import base64
import datetime as dt
import logging
import math
import os
import re
import struct
import time
import zlib
from pathlib import Path
from typing import Any

from . import paths


class WsUnavailable(Exception):
    """Could not connect or authenticate to obs-websocket."""


def ws_settings() -> dict:
    return {
        "host": os.environ.get("OBS_WS_HOST", "localhost"),
        "port": int(os.environ.get("OBS_WS_PORT", "4455")),
        "password": os.environ.get("OBS_WS_PASSWORD") or paths.keychain_get(paths.ACCT_WS) or "",
        "timeout": float(os.environ.get("OBS_WS_TIMEOUT", "15")),
    }


def connect():
    """Return a connected obsws_python.ReqClient or raise WsUnavailable."""
    s = ws_settings()
    # obsws-python logs full tracebacks at ERROR on connection failure; we report it ourselves.
    logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
    try:
        import obsws_python as obsws
    except ImportError as e:  # pragma: no cover
        raise WsUnavailable(f"obsws-python not installed: {e}")
    try:
        return obsws.ReqClient(host=s["host"], port=s["port"], password=s["password"], timeout=s["timeout"])
    except Exception as e:
        raise WsUnavailable(f"{type(e).__name__}: {e}")


def _g(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def stats_dict(cl) -> dict:
    st = cl.get_stats()
    keys = [
        "cpu_usage", "memory_usage", "available_disk_space", "active_fps",
        "average_frame_render_time", "render_skipped_frames", "render_total_frames",
        "output_skipped_frames", "output_total_frames",
        "web_socket_session_incoming_messages", "web_socket_session_outgoing_messages",
    ]
    return {k: _g(st, k) for k in keys}


def stream_status_dict(cl) -> dict:
    ss = cl.get_stream_status()
    keys = [
        "output_active", "output_reconnecting", "output_timecode", "output_duration",
        "output_congestion", "output_bytes", "output_skipped_frames", "output_total_frames",
    ]
    return {k: _g(ss, k) for k in keys}


def record_status_dict(cl) -> dict:
    rs = cl.get_record_status()
    return {k: _g(rs, k) for k in ["output_active", "output_paused", "output_timecode", "output_bytes"]}


def status(cl) -> dict:
    """Aggregate state for `ws status`."""
    out: dict = {}
    v = cl.get_version()
    out["obs_version"] = _g(v, "obs_version")
    out["websocket_version"] = _g(v, "obs_web_socket_version")
    pl = cl.get_profile_list()
    out["current_profile"] = _g(pl, "current_profile_name")
    out["profiles"] = list(_g(pl, "profiles", []) or [])
    out["current_scene"] = _g(cl.get_scene_list(), "current_program_scene_name")
    vs = cl.get_video_settings()
    out["video"] = {
        k: _g(vs, k)
        for k in ["base_width", "base_height", "output_width", "output_height", "fps_numerator", "fps_denominator"]
    }
    n, d = out["video"].get("fps_numerator"), out["video"].get("fps_denominator")
    out["video"]["fps"] = round(n / d, 3) if n and d else None
    out["stream"] = stream_status_dict(cl)
    out["record"] = record_status_dict(cl)
    out["stats"] = stats_dict(cl)
    return out


def scenes(cl) -> list[str]:
    sl = cl.get_scene_list()
    return [_g(s, "sceneName") for s in (_g(sl, "scenes", []) or [])]


def inputs(cl) -> list[dict]:
    il = cl.get_input_list()
    return [
        {"name": _g(i, "inputName"), "kind": _g(i, "inputKind")}
        for i in (_g(il, "inputs", []) or [])
    ]


def _pct(a, b) -> float:
    return round(100.0 * a / b, 2) if b else 0.0


def monitor(cl, seconds: int, interval: float = 2.0, sleep=time.sleep, guest_sources: list[str] | None = None,
            on_sample=None) -> dict:
    """Sample GetStats and GetStreamStatus, return samples plus flagged problems.

    guest_sources adds a per-guest row to each sample. A guest counts as connected once its source
    has rendered, and is flagged when it then renders blank for 2 consecutive samples.
    on_sample(sample) is called after each sample (used for the JSON-lines session log)."""
    samples = []
    n = max(1, int(seconds // interval)) if seconds >= interval else 1
    prev_bytes = None
    prev_t = None
    seen: dict[str, bool] = {}
    blank: dict[str, int] = {}
    for i in range(n):
        st = stats_dict(cl)
        ss = stream_status_dict(cl)
        t = time.monotonic()
        kbps = None
        if ss.get("output_active") and prev_bytes is not None and ss.get("output_bytes") is not None:
            dt_ = max(t - prev_t, 1e-6)
            kbps = round((ss["output_bytes"] - prev_bytes) * 8 / 1000 / dt_, 1)
        if ss.get("output_active"):
            prev_bytes, prev_t = ss.get("output_bytes"), t
        sample = {"stats": st, "stream": ss, "kbps": kbps}
        if guest_sources:
            rows = []
            for name in guest_sources:
                ratio = render_ratio(cl, name)
                vis = ratio is not None and ratio > BLANK_BELOW
                if vis:
                    seen[name] = True
                    blank[name] = 0
                elif ratio is not None:
                    blank[name] = blank.get(name, 0) + 1
                rows.append({"name": name, "active": source_active(cl, name), "render_ratio": ratio,
                             "connected": bool(seen.get(name)),
                             "flagged": bool(seen.get(name)) and blank.get(name, 0) >= 2})
            sample["guests"] = rows
        samples.append(sample)
        if on_sample:
            on_sample(sample)
        if i < n - 1:
            sleep(interval)
    return {"samples": samples, "summary": summarize(samples), "problems": problems(samples)}


def summarize(samples: list[dict]) -> dict:
    last = samples[-1]
    cpu = [s["stats"].get("cpu_usage") or 0 for s in samples]
    fps = [s["stats"].get("active_fps") or 0 for s in samples]
    kb = [s["kbps"] for s in samples if s["kbps"] is not None]
    st, ss = last["stats"], last["stream"]
    return {
        "samples": len(samples),
        "live": bool(ss.get("output_active")),
        "cpu_avg": round(sum(cpu) / len(cpu), 1),
        "cpu_max": round(max(cpu), 1),
        "fps_min": round(min(fps), 2),
        "render_skipped_pct": _pct(st.get("render_skipped_frames") or 0, st.get("render_total_frames") or 0),
        "encode_skipped_pct": _pct(st.get("output_skipped_frames") or 0, st.get("output_total_frames") or 0),
        "dropped_pct": _pct(ss.get("output_skipped_frames") or 0, ss.get("output_total_frames") or 0),
        "congestion_max": max((s["stream"].get("output_congestion") or 0) for s in samples),
        "kbps_avg": round(sum(kb) / len(kb), 1) if kb else None,
    }


def problems(samples: list[dict]) -> list[dict]:
    s = summarize(samples)
    out: list[dict] = []
    if s["render_skipped_pct"] > 1:
        out.append({"id": "render_lag", "detail": f"{s['render_skipped_pct']}% frames skipped due to rendering lag",
                    "fix": "Lower canvas/output resolution or FPS, close GPU-heavy apps, reduce browser sources."})
    if s["encode_skipped_pct"] > 1:
        out.append({"id": "encode_overload", "detail": f"{s['encode_skipped_pct']}% frames skipped due to encoding lag",
                    "fix": "Use the VideoToolbox hardware encoder, lower bitrate/resolution/FPS, disable B-frames."})
    if s["live"] and s["dropped_pct"] > 1:
        out.append({"id": "network_drops", "detail": f"{s['dropped_pct']}% frames dropped by the network",
                    "fix": "Lower bitrate, use wired Ethernet, check upload speed with `doctor --net`."})
    if s["live"] and s["congestion_max"] > 0.5:
        out.append({"id": "congestion", "detail": f"stream congestion up to {s['congestion_max']}",
                    "fix": "The link cannot sustain the bitrate. Lower video bitrate."})
    flagged = sorted({g["name"] for smp in samples for g in smp.get("guests", []) if g.get("flagged")})
    if flagged:
        out.append({"id": "guest_blank", "detail": "connected guest went blank: " + ", ".join(flagged),
                    "fix": "Run `obsagent guests refresh SHOW`; if it stays blank check Browser Source Hardware Acceleration "
                           "(OBS Settings > Advanced) and ask the guest to rejoin."})
    if s["cpu_max"] > 85:
        out.append({"id": "cpu_high", "detail": f"OBS CPU max {s['cpu_max']}%, min fps {s['fps_min']}",
                    "fix": "Close other apps, use hardware encoding, lower scene complexity."})
    return out


# ---- scenes, snapshots, refresh ----

BLANK_BELOW = 0.01  # render_ratio at or under this counts as blank


def current_scene(cl) -> str | None:
    return _g(cl.get_scene_list(), "current_program_scene_name")


def set_scene(cl, name: str) -> dict:
    """SetCurrentProgramScene, then read the program scene back to verify."""
    cl.set_current_program_scene(name)
    now = current_scene(cl)
    return {"requested": name, "current": now, "verified": now == name}


def source_active(cl, name: str):
    try:
        return bool(_g(cl.get_source_active(name), "video_active"))
    except Exception:
        return None


def screenshot_png(cl, source: str, width: int = 1280) -> bytes | None:
    """GetSourceScreenshot as PNG bytes, or None if OBS cannot render the source."""
    try:
        r = cl.get_source_screenshot(source, "png", width, None, -1)
    except Exception:
        return None
    data = _g(r, "image_data")
    if not data:
        return None
    try:
        return base64.b64decode(data.split(",", 1)[1] if data.startswith("data:") else data)
    except Exception:
        return None


def _alpha_ratio_pure(png: bytes, thresh: int = 8) -> float | None:
    """Fraction of pixels with alpha above thresh. Handles 8-bit RGBA and gray+alpha, non-interlaced.
    Other PNG types have no alpha channel, so every pixel counts as visible. None if unparseable."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, hdr = 8, [], None
    while pos + 8 <= len(png):
        ln, typ = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat.append(body)
        elif typ == b"IEND":
            break
    if not hdr:
        return None
    w, h, depth, ctype, _, _, interlace = hdr
    if ctype not in (4, 6):
        return 1.0 if w * h else None
    if depth != 8 or interlace:
        return None
    bpp = 4 if ctype == 6 else 2
    stride = w * bpp
    raw = zlib.decompress(b"".join(idat))
    prev = bytearray(stride)
    visible = 0
    for y in range(h):
        base = y * (stride + 1)
        ft = raw[base]
        line = bytearray(raw[base + 1:base + 1 + stride])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        visible += sum(1 for x in range(bpp - 1, stride, bpp) if line[x] > thresh)
        prev = line
    return visible / (w * h) if w * h else None


def alpha_ratio(png: bytes) -> float | None:
    """Fraction of non-transparent pixels. Pillow when importable, else a minimal PNG decoder."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(png)).convert("RGBA")
        hist = im.getchannel("A").histogram()
        total = sum(hist)
        return sum(hist[9:]) / total if total else None
    except ImportError:
        pass
    except Exception:
        return None
    try:
        return _alpha_ratio_pure(png)
    except Exception:
        return None


def render_ratio(client, source: str, width: int = 320) -> float | None:
    """Fraction of non-transparent pixels in the source's rendered output, None if unknown."""
    png = screenshot_png(client, source, width)
    if png is None:
        return None
    r = alpha_ratio(png)
    return None if r is None else round(r, 4)


def snapshot(cl, source: str | None, out: str | None, width: int = 1280, now=None) -> dict:
    source = source or current_scene(cl)
    if not source:
        raise ValueError("no source given and no current program scene")
    png = screenshot_png(cl, source, width)
    if png is None:
        raise ValueError(f"OBS could not render a screenshot of {source!r}")
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    fname = re.sub(r"[^A-Za-z0-9._-]+", "-", source).strip("-") or "source"
    dest = Path(out).expanduser() if out else paths.snapshots_dir()
    if out is None or dest.is_dir() or str(out).endswith("/") or not dest.suffix:
        dest = dest / f"{fname}-{stamp}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(png)
    ratio = alpha_ratio(png)
    return {"source": source, "path": str(dest), "bytes": len(png),
            "visible_ratio": None if ratio is None else round(ratio, 4)}


def refresh(cl, source: str) -> dict:
    """Press the browser source 'Refresh cache of current page' button."""
    cl.press_input_properties_button(source, "refreshnocache")
    return {"source": source, "refreshed": True}


def sample_input_level(name: str, seconds: float = 3.0) -> float | None:
    """Peak level in dB of an input over `seconds` via InputVolumeMeters, None if unavailable."""
    try:
        import obsws_python as obsws
        s = ws_settings()
        ev = obsws.EventClient(host=s["host"], port=s["port"], password=s["password"],
                               subs=obsws.Subs.INPUTVOLUMEMETERS)
    except Exception:
        return None
    peak = [0.0]

    def on_input_volume_meters(data):
        for i in _g(data, "inputs", []) or []:
            if _g(i, "inputName") != name:
                continue
            for ch in _g(i, "inputLevelsMul", []) or []:
                if len(ch) > 1:
                    peak[0] = max(peak[0], float(ch[1]))
    try:
        ev.callback.register(on_input_volume_meters)
        time.sleep(seconds)
    except Exception:
        return None
    finally:
        try:
            ev.disconnect()
        except Exception:
            pass
    return round(20 * math.log10(peak[0]), 1) if peak[0] > 0 else -100.0
