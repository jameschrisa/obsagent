"""obs-websocket v5 helpers built on obsws-python.

API names verified against the installed obsws-python (ReqClient(**kwargs) with host, port,
password, timeout; snake_case methods; responses are dataclasses with snake_case attrs,
e.g. get_stats() -> cpu_usage, active_fps, render_skipped_frames, render_total_frames,
output_skipped_frames, output_total_frames, available_disk_space).
Tests replace `connect` with a fake client.
"""
from __future__ import annotations

import logging
import os
import time
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


def monitor(cl, seconds: int, interval: float = 2.0, sleep=time.sleep) -> dict:
    """Sample GetStats and GetStreamStatus, return samples plus flagged problems."""
    samples = []
    n = max(1, int(seconds // interval)) if seconds >= interval else 1
    prev_bytes = None
    prev_t = None
    for i in range(n):
        st = stats_dict(cl)
        ss = stream_status_dict(cl)
        t = time.monotonic()
        kbps = None
        if ss.get("output_active") and prev_bytes is not None and ss.get("output_bytes") is not None:
            dt = max(t - prev_t, 1e-6)
            kbps = round((ss["output_bytes"] - prev_bytes) * 8 / 1000 / dt, 1)
        if ss.get("output_active"):
            prev_bytes, prev_t = ss.get("output_bytes"), t
        samples.append({"stats": st, "stream": ss, "kbps": kbps})
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
    if s["cpu_max"] > 85:
        out.append({"id": "cpu_high", "detail": f"OBS CPU max {s['cpu_max']}%, min fps {s['fps_min']}",
                    "fix": "Close other apps, use hardware encoding, lower scene complexity."})
    return out
