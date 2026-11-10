import http.client
import json
import sys
import time
from types import SimpleNamespace as NS

import pytest

from obsagent_lib import bridge, paths, show
from tests.conftest import FakeClient

KEY = "abcd-efgh-ijkl-mnop"
TOKEN = "t" * 43
EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


class Fake(FakeClient):
    def __init__(self, *a, **k):
        super().__init__(*a, scenes=("Camera", "Panel"), **k)
        self.stream_service = NS(stream_service_type="rtmp_custom", stream_service_settings={
            "server": "rtmps://a.rtmps.youtube.com:443/live2", "key": KEY})

    def disconnect(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSAGENT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("OBSAGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PODPRODUCER_STATE", str(tmp_path / "pp"))
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "talking-head.yaml").write_text("title: x\n")
    monkeypatch.setattr(paths, "PLANS_DIR", plans)
    monkeypatch.setattr(show, "_known_secrets", lambda: [KEY, "ws-secret-pass"])
    monkeypatch.setattr(show, "active_key_name", lambda key: "youtube" if key == KEY else None)
    monkeypatch.setattr(paths, "key_registry", lambda: {"youtube": {"label": "Holistiplan"}})
    fake = Fake()
    b = bridge.Bridge(TOKEN, connect=lambda: fake, repo=tmp_path)
    b.obs_running = lambda: True
    b.sleep = lambda s: None
    srv = bridge.make_server(b, 0)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield NS(b=b, fake=fake, port=srv.server_address[1], tmp=tmp_path)
    srv.shutdown()
    srv.server_close()


def call(env, method, path, body=None, token=TOKEN, host="default", origin=None):
    c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=10)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", f"127.0.0.1:{env.port}" if host == "default" else host)
    if token:
        c.putheader("Authorization", f"Bearer {token}")
    if origin:
        c.putheader("Origin", origin)
    data = json.dumps(body).encode() if body is not None else b""
    if method == "POST":
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        obj = json.loads(raw) if raw else {}
    except ValueError:
        obj = {}
    return r.status, obj, dict(r.getheaders())


def wait_job(env, job_id, tries=100):
    for _ in range(tries):
        _, j, _ = call(env, "GET", f"/jobs/{job_id}")
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ---- host, auth, CORS ----

def test_host_check(env):
    assert call(env, "GET", "/health", token=None)[0] == 200
    assert call(env, "GET", "/health", token=None, host=f"localhost:{env.port}")[0] == 200
    assert call(env, "GET", "/health", token=None, host="evil.example:8767")[0] == 403
    assert call(env, "GET", "/status", host="evil.example")[0] == 403
    assert call(env, "GET", "/health", token=None, host="127.0.0.1")[0] == 403  # wrong port form


def test_token_required(env):
    assert call(env, "GET", "/status", token=None)[0] == 401
    assert call(env, "GET", "/status", token="wrong")[0] == 401
    assert call(env, "POST", "/scene", {"name": "Panel"}, token=None)[0] == 401
    assert call(env, "GET", "/status")[0] == 200


def test_cors_only_for_extension_origins(env):
    s, _, h = call(env, "OPTIONS", "/status", origin=EXT, token=None)
    assert s == 204 and h["Access-Control-Allow-Origin"] == EXT
    assert "Authorization" in h["Access-Control-Allow-Headers"]
    s, _, h = call(env, "GET", "/status", origin=EXT)
    assert s == 200 and h["Access-Control-Allow-Origin"] == EXT
    s, _, h = call(env, "GET", "/status")
    assert "Access-Control-Allow-Origin" not in h
    for bad in ("https://evil.example", "http://127.0.0.1:8767", "null"):
        s, _, h = call(env, "GET", "/status", origin=bad)
        assert s == 403 and "Access-Control-Allow-Origin" not in h
        s, _, h = call(env, "OPTIONS", "/status", origin=bad, token=None)
        assert s == 403 and "Access-Control-Allow-Origin" not in h
    assert not any(v == "*" for v in h.values())


def test_token_file_mode(env):
    t = bridge.load_or_create_token()
    p = bridge.token_path()
    assert oct(p.stat().st_mode & 0o777) == "0o600" and len(t) >= 40
    assert bridge.load_or_create_token() == t


def test_health(env):
    s, o, _ = call(env, "GET", "/health", token=None)
    assert s == 200 and o["ok"] is True and o["version"]


# ---- status ----

def test_status_masks_key_and_token(env):
    env.fake.stream_active = True
    s, o, _ = call(env, "GET", "/status")
    assert s == 200 and o["obs_running"] and o["websocket"] and o["streaming"]
    assert o["scene"] == "Camera" and o["scenes"] == ["Camera", "Panel"]
    assert o["key"] == {"name": "youtube", "label": "Holistiplan", "last4": "mnop", "service": "youtube"}
    assert o["plans"] == ["talking-head"] and o["producer"] == {"running": False}
    raw = json.dumps(o)
    assert KEY not in raw and TOKEN not in raw and "abcd-efgh" not in raw


def test_status_obs_closed(env):
    env.b.obs_running = lambda: False
    s, o, _ = call(env, "GET", "/status")
    assert s == 200 and o["obs_running"] is False and o["websocket"] is False and o["scenes"] == []


def test_status_websocket_down(env):
    def boom():
        raise RuntimeError("refused")
    env.b.connect = boom
    o = call(env, "GET", "/status")[1]
    assert o["obs_running"] is True and o["websocket"] is False


# ---- allowlists ----

def test_unknown_plan_rejected(env):
    for path in ("/plan/apply", "/preflight", "/producer/start"):
        for plan in ("nope", "../talking-head", "talking-head; rm -rf /", "", None, 5):
            s, o, _ = call(env, "POST", path, {"plan": plan, "dry_run": True})
            assert s == 400, (path, plan)


def test_unknown_scene_rejected(env):
    assert call(env, "POST", "/scene", {"name": "Nope"})[0] == 400
    assert call(env, "POST", "/scene", {"name": 7})[0] == 400
    assert env.fake.current_scene is None
    s, o, _ = call(env, "POST", "/scene", {"name": "Panel"})
    assert s == 200 and o["verified"] is True and env.fake.current_scene == "Panel"


def test_unknown_route_and_method(env):
    assert call(env, "GET", "/nope")[0] == 404
    assert call(env, "POST", "/shell", {"cmd": "ls"})[0] == 404
    assert call(env, "PUT", "/status")[0] == 405
    assert call(env, "GET", "/jobs/zzz")[0] == 404


# ---- streaming ----

def test_stream_start_wrong_last4(env):
    for bad in ("xxxx", "", "mno", "mnopq", None, 1234):
        s, o, _ = call(env, "POST", "/stream/start", {"confirm_last4": bad})
        assert s == 403, bad
    assert ("start_stream",) not in env.fake.calls
    assert call(env, "POST", "/stream/start", {})[0] == 403


def test_stream_start_and_stop(env):
    s, o, _ = call(env, "POST", "/stream/start", {"confirm_last4": "mnop"})
    assert s == 200 and ("start_stream",) in env.fake.calls
    assert o["key"]["last4"] == "mnop" and KEY not in json.dumps(o)
    assert call(env, "POST", "/stream/stop")[0] == 409  # fake reports not active
    env.fake.stream_active = True
    assert call(env, "POST", "/stream/start", {"confirm_last4": "mnop"})[0] == 409
    assert call(env, "POST", "/stream/stop")[0] == 200 and ("stop_stream",) in env.fake.calls


def test_stream_needs_obs(env):
    env.b.obs_running = lambda: False
    assert call(env, "POST", "/stream/start", {"confirm_last4": "mnop"})[0] == 503


# ---- jobs ----

def fake_cli(env, code):
    env.b.cli = lambda tool, *a: [sys.executable, "-c", code]


def test_job_flow_masks_secrets(env):
    fake_cli(env, f"print('applying'); print('stream {KEY} pw=hunter2 token={TOKEN}'); import sys; sys.exit(0)")
    s, o, _ = call(env, "POST", "/plan/apply", {"plan": "talking-head", "dry_run": True})
    assert s == 200 and "job_id" in o
    j = wait_job(env, o["job_id"])
    assert j["status"] == "succeeded" and j["exit_code"] == 0
    text = "\n".join(j["output_tail"])
    assert "applying" in text and KEY not in text and "hunter2" not in text and TOKEN not in text


def test_job_failure_and_tail_limit(env):
    fake_cli(env, "import sys\nfor i in range(500): print('line', i)\nsys.exit(3)")
    o = call(env, "POST", "/preflight", {"plan": "talking-head"})[1]
    j = wait_job(env, o["job_id"])
    assert j["status"] == "failed" and j["exit_code"] == 3
    assert len(j["output_tail"]) == 200 and j["output_tail"][-1] == "line 499"


def test_apply_dry_run_flag(env):
    seen = []
    env.b.cli = lambda tool, *a: (seen.append((tool, a)), [sys.executable, "-c", "pass"])[1]
    wait_job(env, call(env, "POST", "/plan/apply", {"plan": "talking-head", "dry_run": True})[1]["job_id"])
    wait_job(env, call(env, "POST", "/plan/apply", {"plan": "talking-head", "dry_run": False})[1]["job_id"])
    assert seen[0][1] == ("plan", "apply", "talking-head", "--dry-run")
    assert seen[1][1] == ("plan", "apply", "talking-head")
    assert call(env, "POST", "/plan/apply", {"plan": "talking-head", "dry_run": "no"})[0] == 400


def test_same_kind_job_conflict(env):
    fake_cli(env, "import time; time.sleep(1)")
    assert call(env, "POST", "/preflight", {"plan": "talking-head"})[0] == 200
    assert call(env, "POST", "/preflight", {"plan": "talking-head"})[0] == 409


def test_launch_obs(env):
    state = {"opened": False}
    env.b.obs_running = lambda: state["opened"]
    env.b.open_obs = lambda: (state.__setitem__("opened", True), (0, "", ""))[1]
    o = call(env, "POST", "/obs/launch")[1]
    j = wait_job(env, o["job_id"])
    assert j["status"] == "succeeded" and state["opened"]


def test_launch_obs_timeout(env):
    env.b.obs_running = lambda: False
    env.b.open_obs = lambda: (0, "", "")
    env.b.sleep = lambda s: setattr(env, "ticks", getattr(env, "ticks", 0) + 1)
    import obsagent_lib.bridge as br
    t = iter([0, 0, 0, 100, 100])
    orig = br.time.time
    br.time.time = lambda: next(t, 100)
    try:
        j = wait_job(env, call(env, "POST", "/obs/launch")[1]["job_id"])
    finally:
        br.time.time = orig
    assert j["status"] == "failed"


# ---- chat ----

def test_chat_validation(env):
    assert call(env, "POST", "/chat", {"agent": "root", "message": "hi"})[0] == 400
    assert call(env, "POST", "/chat", {"agent": "obsagent", "message": ""})[0] == 400
    assert call(env, "POST", "/chat", {"agent": "obsagent", "message": "x" * 5000})[0] == 400
    assert call(env, "POST", "/chat", {"agent": "obsagent", "message": "hi", "session_id": "a b; ls"})[0] == 400


def test_chat_job_returns_text_and_session(env):
    seen = {}
    out = json.dumps({"type": "result", "result": f"All good. key {KEY}", "session_id": "11111111-2222-3333-4444-555555555555", "is_error": False})
    real = env.b.claude_cmd

    def spy(prompt, sid):
        seen["argv"] = real(prompt, sid)
        return [sys.executable, "-c", f"print({out!r})"]
    env.b.claude_cmd = spy
    o = call(env, "POST", "/chat", {"agent": "pod-producer", "message": "how is the show", "session_id": "11111111-2222-3333-4444-555555555555"})[1]
    j = wait_job(env, o["job_id"])
    assert j["status"] == "succeeded"
    assert j["result"]["session_id"] == "11111111-2222-3333-4444-555555555555"
    assert "All good" in j["result"]["text"] and KEY not in j["result"]["text"]
    a = seen["argv"]
    assert "--output-format" in a and "json" in a and "--resume" in a
    assert a[a.index("--allowedTools") + 1] == bridge.CHAT_ALLOWED_TOOLS
    prompt = a[-1]
    assert prompt.startswith("/pod-producer how is the show") and "Chrome" in prompt and "Do not start or stop a stream" in prompt


def test_chat_bad_output_fails(env):
    env.b.claude_cmd = lambda prompt, sid: [sys.executable, "-c", "print('not json')"]
    j = wait_job(env, call(env, "POST", "/chat", {"agent": "obsagent", "message": "hi"})[1]["job_id"])
    assert j["status"] == "failed"


# ---- producer ----

def test_producer_start_stop(env):
    alive = {"pid": None}
    spawned, killed = [], []
    env.b.spawn = lambda argv, log: (spawned.append(argv), alive.__setitem__("pid", 4242), 4242)[2]
    env.b.pid_alive = lambda pid: alive["pid"] == pid
    env.b.kill_group = lambda pid, sig: (killed.append((pid, sig)), alive.__setitem__("pid", None))
    assert call(env, "POST", "/producer/start", {"plan": "talking-head", "max_minutes": 0})[0] == 400
    assert call(env, "POST", "/producer/start", {"plan": "talking-head", "max_minutes": "90"})[0] == 400
    s, o, _ = call(env, "POST", "/producer/start", {"plan": "talking-head", "max_minutes": 45})
    assert s == 200 and o["pid"] == 4242
    assert spawned[0][-5:] == ["podproducer", "run", "talking-head", "--max-minutes", "45"][-5:] or spawned[0][-4:] == ["run", "talking-head", "--max-minutes", "45"]
    st = call(env, "GET", "/status")[1]["producer"]
    assert st["running"] and st["pid"] == 4242 and st["dock_url"] == "http://127.0.0.1:8765"
    assert call(env, "POST", "/producer/start", {"plan": "talking-head"})[0] == 409
    s, o, _ = call(env, "POST", "/producer/stop")
    assert s == 200 and killed and killed[0][0] == 4242
    assert call(env, "GET", "/status")[1]["producer"] == {"running": False}
    assert call(env, "POST", "/producer/stop")[0] == 409


def test_producer_stop_escalates_to_kill(env):
    import signal
    killed = []
    env.b.pid_alive = lambda pid: True
    env.b.kill_group = lambda pid, sig: killed.append(sig)
    pf = env.b._pidfile()
    pf.parent.mkdir(parents=True)
    pf.write_text(json.dumps({"pid": 99, "plan": "talking-head", "started": time.time()}))
    assert call(env, "POST", "/producer/stop")[0] == 200
    assert killed == [signal.SIGTERM, signal.SIGKILL]


# ---- launchd and CLI ----

def test_plist_contents():
    p = bridge.build_plist()
    assert p["RunAtLoad"] is True and p["KeepAlive"] is True
    assert p["StandardOutPath"].endswith("Library/Logs/obsagent-bridge.log")
    assert all(a.startswith("/") or a in ("run", "--quiet", "--script", "serve", "--port", "8767") for a in p["ProgramArguments"])
    assert str(paths.REPO_DIR / "obsagent") in p["ProgramArguments"]
    assert "token" not in json.dumps(p).lower()


def test_serve_token_refuses_without_tty(env, capsys, monkeypatch):
    from obsagent_lib import cli
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    rc = cli.main(["serve", "token"])
    cap = capsys.readouterr()
    assert rc == 2 and "terminal" in cap.err.lower()
    assert not (paths.agent_config_dir() / "bridge-token").exists() or bridge.load_or_create_token() not in cap.out + cap.err


def test_serve_token_prints_on_tty(env, capsys, monkeypatch):
    from obsagent_lib import cli
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["serve", "token"]) == 0
    assert capsys.readouterr().out.strip() == bridge.load_or_create_token()
