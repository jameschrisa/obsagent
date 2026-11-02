"""State and plan locations."""
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def state_dir() -> Path:
    d = Path(os.environ.get("PODPRODUCER_STATE") or Path.home() / ".local" / "state" / "podproducer")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def sessions_dir() -> Path:
    d = state_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def new_session_dir() -> Path:
    base = time.strftime("%Y%m%d-%H%M%S")
    d = sessions_dir() / base
    n = 1
    while d.exists():
        n += 1
        d = sessions_dir() / f"{base}-{n}"
    d.mkdir(mode=0o700)
    return d


def list_sessions() -> list:
    return sorted(p for p in sessions_dir().iterdir() if p.is_dir())


def latest_session():
    s = list_sessions()
    return s[-1] if s else None


def plans_dir() -> Path:
    return Path(os.environ.get("PODPRODUCER_PLANS_DIR") or REPO / "plans")


def plan_path(show: str) -> Path:
    if not show or "/" in show or ".." in show:
        raise ValueError(f"invalid show name: {show!r}")
    return plans_dir() / f"{show}.yaml"


def model_path() -> Path:
    return Path(os.environ.get("PODPRODUCER_MODEL") or Path.home() / ".local" / "share" / "podproducer" / "models" / "ggml-small.en.bin")


def claude_bin() -> str:
    cand = Path.home() / ".local" / "bin" / "claude"
    return str(cand) if cand.exists() else "claude"


def whisper_bin() -> str:
    cand = Path("/opt/homebrew/bin/whisper-cli")
    return str(cand) if cand.exists() else "whisper-cli"


def ffmpeg_bin() -> str:
    cand = Path("/opt/homebrew/bin/ffmpeg")
    return str(cand) if cand.exists() else "ffmpeg"
