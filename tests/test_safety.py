import json
import os
import subprocess
import sys

import pytest

from obsagent_lib import bridge, panelguard
from podproducer_lib import brain, cli as pcli, paths as ppaths


def test_call_claude_flags(monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "ok"}), "")
    monkeypatch.setattr(brain.subprocess, "run", fake)
    assert brain.call_claude("hi", system_text="sys") == "ok"
    c = seen["cmd"]
    assert c[c.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in c and "--mcp-config" not in c
    assert c[c.index("--setting-sources") + 1] == ""


BLOCKED = [
    ("obsagent", "ws start-stream --yes"), ("obsagent", "ws stop-stream"), ("obsagent", "ws start-record"),
    ("obsagent", "ws stop-record"), ("obsagent", "ws scene Main"), ("obsagent", "ws refresh X"),
    ("obsagent", "plan apply show"), ("obsagent", "plan apply show --bitrate 4000"), ("obsagent", "profile apply yt"),
    ("obsagent", "stream set-key youtube"), ("obsagent", "stream set-service youtube"),
    ("obsagent", "guests add show --name A"), ("obsagent", "guests remove show A"), ("obsagent", "guests rotate show"),
    ("obsagent", "guests set-email show A a@b.c"), ("obsagent", "guests set-sender a@b.c"),
    ("obsagent", "guests invite show --all --send"), ("obsagent", "guests guide show"), ("obsagent", "guests refresh show"),
    ("obsagent", "guests links show --reveal"), ("obsagent", "guests links show --rev"),
    ("obsagent", "preflight show --record 30"), ("obsagent", "show postshow show"),
    ("obsagent", "serve install"), ("obsagent", "serve uninstall"), ("obsagent", "serve token"), ("obsagent", "serve"),
    ("podproducer", "run show"), ("podproducer", "prep show"), ("podproducer", "recap"),
    ("podproducer", "sessions prune --yes"), ("podproducer", "sessions --delete x --yes"),
]
ALLOWED = [
    ("obsagent", "doctor"), ("obsagent", "ws status"), ("obsagent", "ws scenes"), ("obsagent", "ws inputs"),
    ("obsagent", "ws snapshot Main"), ("obsagent", "ws monitor --seconds 5"), ("obsagent", "stream list-keys"),
    ("obsagent", "plan validate show"), ("obsagent", "plan export show"), ("obsagent", "plan apply show --dry-run"),
    ("obsagent", "guests list show"), ("obsagent", "guests links show"), ("obsagent", "preflight show"),
    ("obsagent", "triage collect"), ("obsagent", "serve status"),
    ("podproducer", "check"), ("podproducer", "sessions"),
]


@pytest.mark.parametrize("tool,cmd", BLOCKED)
def test_guard_blocks(tool, cmd):
    assert panelguard.blocked(tool, cmd.split())


@pytest.mark.parametrize("tool,cmd", ALLOWED)
def test_guard_allows(tool, cmd):
    assert panelguard.blocked(tool, cmd.split()) is None


def test_guard_only_active_with_env(monkeypatch, capsys):
    monkeypatch.delenv("OBSAGENT_CALLER", raising=False)
    assert panelguard.check("obsagent", ["ws", "start-stream"]) is None
    monkeypatch.setenv("OBSAGENT_CALLER", "panel")
    assert panelguard.check("obsagent", ["ws", "start-stream"]) == 3
    assert "refused" in capsys.readouterr().err
    assert panelguard.check("obsagent", ["ws", "status"]) is None


def test_cli_main_refuses_under_panel(monkeypatch):
    from obsagent_lib import cli
    monkeypatch.setenv("OBSAGENT_CALLER", "panel")
    assert cli.main(["ws", "stop-stream", "--yes"]) == 3
    assert pcli.main(["run", "show"]) == 3


def test_child_env_only_for_panel(monkeypatch):
    monkeypatch.setenv("OBSAGENT_CALLER", "panel")
    assert "OBSAGENT_CALLER" not in bridge.Bridge.child_env()
    monkeypatch.delenv("OBSAGENT_CALLER")
    assert bridge.Bridge.child_env(panel=True)["OBSAGENT_CALLER"] == "panel"


def test_allowlist_and_denies():
    rules = bridge.CHAT_ALLOWED_TOOLS.split(",")
    assert "Bash(obsagent *)" not in rules and "Bash(podproducer *)" not in rules
    assert "Bash(obsagent ws status)" in rules and "Bash(obsagent plan apply * --dry-run)" in rules
    assert not any("start-stream" in r or "set-key" in r or "invite" in r for r in rules)
    den = bridge.Bridge.chat_denied_tools().split(",")
    for p in ("~/.config/obsagent/**", "~/.local/state/podproducer/**", "~/Library/Keychains/**"):
        assert f"Read({p})" in den
    assert any(d.startswith("Read(//") and d.endswith("/**)") for d in den)  # bridge token dir


# ---- sessions prune ----

def _mk(root, name, age_days, size=10):
    d = root / name
    d.mkdir()
    (d / "transcript.jsonl").write_text("x" * size)
    t = os.path.getmtime(d) - age_days * 86400
    os.utime(d, (t, t))
    return d


@pytest.fixture
def sess(tmp_path, monkeypatch):
    monkeypatch.setenv("PODPRODUCER_STATE", str(tmp_path))
    root = ppaths.sessions_dir()
    return root, _mk(root, "old", 60, 2048), _mk(root, "new", 1)


def test_prune_dry_run_default(sess, capsys):
    root, old, new = sess
    assert pcli.main(["sessions", "prune"]) == 0
    out = capsys.readouterr().out
    assert "would delete  old" in out and "new" not in out.split("Dry run")[0].replace("would delete  old", "")
    assert old.exists() and new.exists()


def test_prune_yes_deletes_only_old(sess, capsys):
    root, old, new = sess
    assert pcli.main(["sessions", "prune", "--older-than", "30", "--yes"]) == 0
    assert not old.exists() and new.exists()


def test_delete_one_needs_yes(sess, capsys):
    root, old, new = sess
    assert pcli.main(["sessions", "--delete", "new"]) == 0
    assert new.exists()
    assert pcli.main(["sessions", "--delete", "new", "--yes"]) == 0
    assert not new.exists() and old.exists()
    assert pcli.main(["sessions", "--delete", "../x", "--yes"]) == 1


def test_sessions_list_shows_size(sess, capsys):
    assert pcli.main(["sessions"]) == 0
    assert "2.0 KB" in capsys.readouterr().out


def test_run_cmd_env_chat_only(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSAGENT_CALLER", "panel")  # even a polluted bridge env must not reach button jobs
    b = bridge.Bridge("t" * 20, connect=lambda: None, repo=tmp_path)
    code = [sys.executable, "-c", "import os;print(os.environ.get('OBSAGENT_CALLER','none'))"]
    j = bridge.Job("t")
    b.run_cmd(j, code, capture=True)
    assert j.captured.strip() == "none"
    b.run_cmd(j, code, capture=True, panel=True)
    assert j.captured.strip() == "panel"
