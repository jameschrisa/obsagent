"""macOS display lookup for screen_capture.

OBS 33 / obs-websocket 5.7.6 crashes (SIGSEGV in strlen inside obs-websocket) when asked for
GetInputPropertiesListPropertyItems on a screen_capture input, so obsagent never sends that
request. The display UUID that OBS stores in `display_uuid` comes from CoreGraphics instead.
"""
from __future__ import annotations

import ctypes
import ctypes.util


def main_display_uuid() -> str | None:
    """UUID string of the main display, as CGDisplayCreateUUIDFromDisplayID reports it."""
    try:
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        try:
            cs = ctypes.CDLL("/System/Library/Frameworks/ColorSync.framework/ColorSync")
            create_uuid = cs.CGDisplayCreateUUIDFromDisplayID
        except (OSError, AttributeError):
            create_uuid = cg.CGDisplayCreateUUIDFromDisplayID
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        create_uuid.restype = ctypes.c_void_p
        create_uuid.argtypes = [ctypes.c_uint32]
        cf.CFUUIDCreateString.restype = ctypes.c_void_p
        cf.CFUUIDCreateString.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        u = create_uuid(cg.CGMainDisplayID())
        if not u:
            return None
        s = cf.CFUUIDCreateString(None, u)
        buf = ctypes.create_string_buffer(64)
        ok = cf.CFStringGetCString(s, buf, 64, 0x08000100)  # kCFStringEncodingUTF8
        cf.CFRelease(s)
        cf.CFRelease(u)
        return buf.value.decode() if ok else None
    except Exception:
        return None
