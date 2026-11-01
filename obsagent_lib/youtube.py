"""Optional YouTube Data API v3 helpers (liveBroadcasts / liveStreams) with an installed-app OAuth flow.

Google client libraries are imported lazily, so the rest of obsagent works without them.
Client secret: ~/.config/obsagent/client_secret.json. Token: Keychain (obsagent / youtube-token)
with ~/.config/obsagent/token.json (chmod 600) as the fallback.
"""
from __future__ import annotations

import json
import os

from . import paths

SCOPES = ["https://www.googleapis.com/auth/youtube"]
ACCT_TOKEN = "youtube-token"

SETUP_HELP = """YouTube API is not configured. One-time setup:
  1. console.cloud.google.com > create a project > enable "YouTube Data API v3".
  2. APIs & Services > Credentials > Create OAuth client ID > type "Desktop app".
  3. Download the JSON to ~/.config/obsagent/client_secret.json
  4. Run `obsagent youtube auth` and approve in the browser.
  5. The channel must be enabled for live streaming (youtube.com/features, can take 24h)."""


class YoutubeNotConfigured(Exception):
    pass


def client_secret_path():
    return paths.agent_config_dir() / "client_secret.json"


def token_path():
    return paths.agent_config_dir() / "token.json"


def configured() -> bool:
    return client_secret_path().exists()


def _import_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise YoutubeNotConfigured(f"Google client libraries missing ({e}). Run via the uv script so deps resolve.")
    return Request, Credentials, InstalledAppFlow, build


def _save_token(creds_json: str) -> str:
    if paths.keychain_set(ACCT_TOKEN, creds_json):
        return "keychain"
    d = paths.agent_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    tp = token_path()
    tp.write_text(creds_json)
    os.chmod(tp, 0o600)
    return str(tp)


def _load_token() -> str | None:
    t = paths.keychain_get(ACCT_TOKEN)
    if t:
        return t
    tp = token_path()
    return tp.read_text() if tp.exists() else None


def auth() -> dict:
    if not configured():
        raise YoutubeNotConfigured(SETUP_HELP)
    _, _, InstalledAppFlow, _ = _import_google()
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path()), SCOPES)
    creds = flow.run_local_server(port=0)
    where = _save_token(creds.to_json())
    return {"authorized": True, "token_stored_in": where}


def service():
    if not configured():
        raise YoutubeNotConfigured(SETUP_HELP)
    Request, Credentials, _, build = _import_google()
    raw = _load_token()
    if not raw:
        raise YoutubeNotConfigured("Not authorized yet. Run `obsagent youtube auth`.")
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def broadcast_body(title, start_iso, description="", privacy="unlisted", latency="low") -> dict:
    return {
        "snippet": {"title": title, "description": description, "scheduledStartTime": start_iso},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        "contentDetails": {"enableAutoStart": False, "enableAutoStop": False, "latencyPreference": latency},
    }


def create_broadcast(title, start_iso, description="", privacy="unlisted", latency="low") -> dict:
    yt = service()
    body = broadcast_body(title, start_iso, description, privacy, latency)
    r = yt.liveBroadcasts().insert(part="snippet,status,contentDetails", body=body).execute()
    return {"id": r["id"], "title": r["snippet"]["title"], "watch_url": f"https://youtube.com/watch?v={r['id']}"}


def bind_stream(broadcast_id: str, stream_id: str | None = None, title: str = "obsagent stream") -> dict:
    """Bind a broadcast to a stream (creates a reusable stream if none given). Returns masked key info."""
    yt = service()
    if not stream_id:
        s = yt.liveStreams().insert(
            part="snippet,cdn",
            body={"snippet": {"title": title}, "cdn": {"frameRate": "variable", "ingestionType": "rtmp", "resolution": "variable"}},
        ).execute()
        stream_id = s["id"]
        info = s["cdn"]["ingestionInfo"]
        key = info.get("streamName")
        addr = info.get("ingestionAddress")
        if key:
            paths.keychain_set(paths.ACCT_YOUTUBE, key)
    else:
        addr = key = None
    yt.liveBroadcasts().bind(part="id,contentDetails", id=broadcast_id, streamId=stream_id).execute()
    return {"broadcast": broadcast_id, "stream": stream_id, "ingestion_address": addr,
            "stream_key": paths.mask(key), "key_saved_to_keychain": bool(key)}


def status(broadcast_id: str | None = None) -> dict:
    yt = service()
    if broadcast_id:
        items = yt.liveBroadcasts().list(part="id,snippet,status", id=broadcast_id).execute().get("items", [])
    else:
        items = yt.liveBroadcasts().list(part="id,snippet,status", mine=True, maxResults=10).execute().get("items", [])
    return {"broadcasts": [{"id": i["id"], "title": i["snippet"]["title"], "life_cycle": i["status"]["lifeCycleStatus"],
                            "privacy": i["status"]["privacyStatus"]} for i in items]}
