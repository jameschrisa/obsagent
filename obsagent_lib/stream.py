"""Stream destination (service) settings. Keys live in the macOS Keychain, never in files."""
from __future__ import annotations

import getpass
import os

from . import paths

# YouTube ingest URLs are documented at
# https://support.google.com/youtube/answer/2853702 (primary RTMP and RTMPS ingest).
# rtmp_custom is used so we do not depend on OBS's service-list names.
YOUTUBE_RTMP = "rtmp://a.rtmp.youtube.com/live2"
YOUTUBE_RTMPS = "rtmps://a.rtmps.youtube.com:443/live2"


class KeyError_(Exception):
    pass


def resolve_key(source: str, account: str) -> str:
    if source == "keychain":
        k = paths.keychain_get(account)
        if not k:
            raise KeyError_(f"no key in Keychain for '{account}'. Run `obsagent stream set-key --target {account}`.")
        return k
    if source == "env":
        k = os.environ.get("OBS_STREAM_KEY")
        if not k:
            raise KeyError_("OBS_STREAM_KEY is not set")
        return k
    if source == "prompt":
        k = getpass.getpass("Stream key (input hidden): ").strip()
        if not k:
            raise KeyError_("empty key")
        return k
    raise KeyError_(f"unknown key source '{source}'")


def service_settings(target: str, rtmps: bool, key: str, server: str | None = None) -> tuple[str, dict]:
    if target == "youtube":
        server = server or (YOUTUBE_RTMPS if rtmps else YOUTUBE_RTMP)
    elif not server:
        raise KeyError_(f"--server is required for target '{target}'")
    return "rtmp_custom", {"server": server, "key": key, "use_auth": False}


def masked(settings: dict) -> dict:
    d = dict(settings)
    if "key" in d:
        d["key"] = paths.mask(d["key"])
    return d


def set_service(cl, target: str, rtmps: bool, key: str, server: str | None = None, dry_run: bool = False) -> dict:
    ss_type, settings = service_settings(target, rtmps, key, server)
    if not dry_run:
        cl.set_stream_service_settings(ss_type, settings)
    return {"type": ss_type, "settings": masked(settings), "applied": not dry_run}
