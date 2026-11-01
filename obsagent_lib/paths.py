"""Paths, secrets (macOS Keychain via `security`), masking and small helpers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

KEYCHAIN_SERVICE = "obsagent"
ACCT_WS = "obs-websocket"
ACCT_YOUTUBE = "youtube"

REPO_DIR = Path(__file__).resolve().parent.parent
PRESETS_DIR = REPO_DIR / "presets"
PLANS_DIR = REPO_DIR / "plans"

OBS_APP = Path("/Applications/OBS.app")


def obs_config_dir() -> Path:
    """OBS user config dir on macOS (env OBSAGENT_OBS_DIR overrides, used by tests)."""
    override = os.environ.get("OBSAGENT_OBS_DIR")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "obs-studio"


def profiles_dir() -> Path:
    return obs_config_dir() / "basic" / "profiles"


def scenes_dir() -> Path:
    return obs_config_dir() / "basic" / "scenes"


def agent_config_dir() -> Path:
    return Path(os.environ.get("OBSAGENT_CONFIG_DIR", str(Path.home() / ".config" / "obsagent")))


def mask(value: str | None, keep: int = 4) -> str:
    """Mask a secret, keeping only the last `keep` chars."""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


def run(cmd: list[str], timeout: float = 15) -> tuple[int, str, str]:
    """Run a command, never raise. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out after {timeout}s"
    except OSError as e:
        return 126, "", str(e)


def obs_running() -> bool:
    """True if an OBS process is running (the app binary is named `OBS`)."""
    rc, out, _ = run(["pgrep", "-x", "OBS"], timeout=5)
    if rc == 0 and out.strip():
        return True
    rc, out, _ = run(["pgrep", "-x", "obs"], timeout=5)
    return rc == 0 and bool(out.strip())


def obs_version() -> str | None:
    plist = OBS_APP / "Contents" / "Info.plist"
    if not plist.exists():
        return None
    import plistlib

    try:
        with plist.open("rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString")
    except Exception:
        return None


# ---- Keychain ----

def keychain_get(account: str) -> str | None:
    rc, out, _ = run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"])
    if rc == 0:
        return out.rstrip("\n") or None
    return None


def keychain_set(account: str, secret: str) -> bool:
    # -U updates if present. Note: the secret is visible in the process list briefly; acceptable
    # for a single-user Mac, and `security` offers no stdin form for -w on add.
    rc, _, _ = run(
        ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", account, "-w", secret]
    )
    return rc == 0


def keychain_delete(account: str) -> bool:
    rc, _, _ = run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account])
    return rc == 0


def emit(data, as_json: bool, text_fn=None) -> None:
    """Print data as JSON, or call text_fn(data) for human output."""
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    elif text_fn:
        text_fn(data)
    else:
        print(data)


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def which(name: str) -> str | None:
    return shutil.which(name)


# Registry of stored stream-key names (no secrets: names and labels only), so list-keys
# does not have to dump the Keychain. The default YouTube key is always listed.
KEY_REGISTRY = Path.home() / ".config" / "obsagent" / "keys.json"


def key_registry() -> dict:
    import json
    reg = {ACCT_YOUTUBE: {"label": "default"}}
    try:
        reg.update(json.loads(KEY_REGISTRY.read_text()))
    except (OSError, ValueError):
        pass
    return reg


def key_registry_add(account: str, label: str | None = None) -> None:
    import json
    reg = key_registry()
    entry = reg.get(account, {})
    if label:
        entry["label"] = label
    reg[account] = entry
    KEY_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    KEY_REGISTRY.write_text(json.dumps(reg, indent=2) + "\n")
