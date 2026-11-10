"""Local bridge for the Chrome side panel: `obsagent serve`.

A small JSON API on 127.0.0.1:8767 (stdlib only). Every action is a fixed, allowlisted operation:
no endpoint runs an arbitrary command. Requests need a Bearer token (created on first run, mode 0600),
a loopback Host header (DNS rebinding guard) and, if an Origin is sent, a chrome-extension:// origin.
Stream keys, room passwords and the token never leave this process: status shows the key name and
last 4 characters only, and job output is scrubbed before it is returned.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, paths, plans, show, ws

HOST = "127.0.0.1"
PORT = 8767
LABEL = "com.obsagent.bridge"
DOCK_URL = "http://127.0.0.1:8765"
MAX_BODY = 64 * 1024
MAX_JOBS = 40
TAIL_LINES = 200
MAX_CHAT_CHARS = 4000
AGENTS = {"obsagent": "/obsagent", "pod-producer": "/pod-producer"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")

CHAT_ALLOWED_TOOLS = "Read,Grep,Glob,Bash(obsagent *),Bash(podproducer *)"
CHAT_DENIED_TOOLS = ",".join([
    "Bash(obsagent ws start-stream *)", "Bash(obsagent ws stop-stream *)", "Bash(obsagent ws start-stream)",
    "Bash(obsagent ws stop-stream)", "Bash(obsagent guests invite *--send*)", "Bash(obsagent stream set-key *)",
])
CHAT_NOTE = ("This request came from the Chrome side panel. Do not start or stop a stream and do not send email "
             "or invites: the panel buttons do that. You may read state, run checks and explain.")


class ApiError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


# ---- token ----

def token_path() -> Path:
    return paths.agent_config_dir() / "bridge-token"


def load_or_create_token() -> str:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        tok = p.read_text().strip()
    except OSError:
        tok = ""
    if tok:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return tok
    tok = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    os.chmod(p, 0o600)
    return tok


# ---- jobs ----

class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:16]
        self.kind = kind
        self.status = "running"
        self.exit_code: int | None = None
        self.result: dict | None = None
        self.lines: deque = deque(maxlen=TAIL_LINES * 2)
        self.created = time.time()
        self.captured = ""
        self.lock = threading.Lock()

    def log(self, line: str) -> None:
        with self.lock:
            self.lines.append(line.rstrip("\n"))

    def finish(self, code: int, result: dict | None = None) -> None:
        self.exit_code = code
        self.result = result
        self.status = "succeeded" if code == 0 else "failed"


# ---- the bridge ----

class Bridge:
    """All state and actions. The HTTP handler only routes into this object, so tests can inject fakes."""

    def __init__(self, token: str, port: int = PORT, connect=None, repo: Path | None = None):
        self.token = token
        self.port = port
        self.connect = connect or ws.connect
        self.repo = Path(repo) if repo else paths.REPO_DIR
        self.jobs: dict[str, Job] = {}
        self.jobs_lock = threading.Lock()
        self._key_cache: dict[str, tuple[float, tuple]] = {}
        self._secret_cache: tuple[float, list] = (0.0, [])
        self.obs_running = paths.obs_running
        self.open_obs = lambda: paths.run(["open", "-a", "OBS"], timeout=15)
        self.spawn = self._spawn
        self.pid_alive = self._pid_alive
        self.kill_group = self._kill_group
        self.sleep = time.sleep

    # -- command lines (overridable in tests) --

    def cli(self, tool: str, *args: str) -> list[str]:
        """argv for our own CLIs, run with the same interpreter and dependencies as this process."""
        return [sys.executable, str(self.repo / tool), *args]

    def claude_cmd(self, prompt: str, session_id: str | None) -> list[str]:
        cmd = [self.claude_bin(), "-p", "--output-format", "json", "--permission-mode", "dontAsk",
               "--allowedTools", CHAT_ALLOWED_TOOLS, "--disallowedTools", CHAT_DENIED_TOOLS,
               "--append-system-prompt", CHAT_NOTE]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd + ["--", prompt]

    @staticmethod
    def claude_bin() -> str:
        cand = Path.home() / ".local" / "bin" / "claude"
        return str(cand) if cand.exists() else (shutil.which("claude") or "claude")

    # -- secrets and masking --

    def secrets_list(self) -> list[str]:
        t, cached = self._secret_cache
        if time.time() - t > 60:
            try:
                cached = show._known_secrets()
            except Exception:
                cached = []
            self._secret_cache = (time.time(), cached)
        return [*cached, self.token]

    def scrub(self, text: str) -> str:
        return show.scrub(text or "", self.secrets_list())

    # -- OBS access --

    def client(self):
        if not self.obs_running():
            return None
        try:
            return self.connect()
        except Exception:
            return None

    @staticmethod
    def close(cl) -> None:
        if cl is not None and hasattr(cl, "disconnect"):
            try:
                cl.disconnect()
            except Exception:
                pass

    def need_client(self):
        cl = self.client()
        if cl is None:
            raise ApiError(503, "OBS is not reachable. Open OBS and check the WebSocket server.")
        return cl

    def active_key(self, cl) -> dict:
        """Name, label and last 4 of the stream key OBS holds. The key itself is never returned."""
        out = {"name": None, "label": None, "last4": None, "service": None}
        try:
            s = ws._g(cl.get_stream_service_settings(), "stream_service_settings", {}) or {}
        except Exception:
            return out
        key = s.get("key") or ""
        server = s.get("server") or ""
        out["service"] = "youtube" if "youtube" in server else ("custom" if server else None)
        if not key:
            return out
        out["last4"] = key[-4:]
        h = hashlib.sha256(key.encode()).hexdigest()
        hit = self._key_cache.get(h)
        if hit and time.time() - hit[0] < 30:
            name = hit[1][0]
        else:
            name = show.active_key_name(key)
            self._key_cache[h] = (time.time(), (name,))
            if len(self._key_cache) > 8:
                self._key_cache.pop(next(iter(self._key_cache)))
        out["name"] = name
        out["label"] = (paths.key_registry().get(name) or {}).get("label") if name else None
        return out

    # -- plans --

    def plan_names(self) -> list[str]:
        d = paths.PLANS_DIR
        return sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []

    def check_plan(self, name) -> str:
        if not isinstance(name, str) or not NAME_RE.match(name) or name not in self.plan_names():
            raise ApiError(400, "Unknown plan.")
        return name

    # -- status --

    def status(self) -> dict:
        running = bool(self.obs_running())
        st: dict = {"obs_running": running, "websocket": False, "streaming": False, "recording": False,
                    "stream_seconds": None, "scene": None, "scenes": [], "profile": None, "key": None,
                    "producer": self.producer_status(), "plans": self.plan_names(), "version": __version__}
        cl = None
        if running:
            try:
                cl = self.connect()
            except Exception:
                cl = None
        if cl is None:
            return st
        try:
            st["websocket"] = True
            ss = ws.stream_status_dict(cl)
            st["streaming"] = bool(ss.get("output_active"))
            dur = ss.get("output_duration")
            st["stream_seconds"] = int(dur // 1000) if st["streaming"] and isinstance(dur, (int, float)) else None
            st["reconnecting"] = bool(ss.get("output_reconnecting"))
            st["recording"] = bool(ws._g(cl.get_record_status(), "output_active"))
            sl = cl.get_scene_list()
            st["scene"] = ws._g(sl, "current_program_scene_name")
            st["scenes"] = [ws._g(s, "sceneName") for s in (ws._g(sl, "scenes", []) or [])]
            st["profile"] = ws._g(cl.get_profile_list(), "current_profile_name")
            st["key"] = self.active_key(cl)
        except Exception as e:
            st["error"] = self.scrub(f"{type(e).__name__}: {e}")[:200]
        finally:
            self.close(cl)
        return st

    # -- jobs --

    def new_job(self, kind: str) -> Job:
        with self.jobs_lock:
            if any(j.kind == kind and j.status == "running" for j in self.jobs.values()):
                raise ApiError(409, f"A {kind} job is already running.")
            while len(self.jobs) >= MAX_JOBS:
                done = [j for j in self.jobs.values() if j.status != "running"]
                if not done:
                    raise ApiError(429, "Too many running jobs.")
                self.jobs.pop(min(done, key=lambda j: j.created).id)
            job = Job(kind)
            self.jobs[job.id] = job
            return job

    def start_job(self, kind: str, fn) -> dict:
        job = self.new_job(kind)

        def runner():
            try:
                fn(job)
            except Exception as e:
                job.log(f"error: {type(e).__name__}: {e}")
                job.finish(1)

        threading.Thread(target=runner, daemon=True).start()
        return {"job_id": job.id}

    def job_view(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if not job:
            raise ApiError(404, "Unknown job.")
        with job.lock:
            tail = list(job.lines)[-TAIL_LINES:]
        view = {"id": job.id, "kind": job.kind, "status": job.status, "exit_code": job.exit_code,
                "output_tail": self.scrub("\n".join(tail)).split("\n") if tail else []}
        if job.result is not None:
            view["result"] = {k: (self.scrub(v) if isinstance(v, str) else v) for k, v in job.result.items()}
        return view

    def run_cmd(self, job: Job, argv: list[str], timeout: float = 900, cwd: Path | None = None, capture: bool = False) -> int:
        """Run argv and stream output lines into the job. Returns the exit code.

        capture=False finishes the job itself. capture=True keeps stdout in job.captured and leaves the job open
        so the caller can parse it and finish with a result."""
        try:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 text=True, cwd=str(cwd or self.repo))
        except OSError as e:
            job.log(f"could not start: {e}")
            job.captured = ""
            if not capture:
                job.finish(127)
            return 127
        timer = threading.Timer(timeout, p.kill)
        timer.start()
        buf = []
        try:
            for line in p.stdout:
                buf.append(line)
                if not capture:
                    job.log(line)
            p.wait()
        finally:
            timer.cancel()
        text = "".join(buf)
        if p.returncode in (-9, 137):
            job.log(f"stopped after {int(timeout)} s")
        job.captured = text
        if not capture:
            job.finish(p.returncode)
        return p.returncode

    # -- actions --

    def launch_obs(self) -> dict:
        def fn(job: Job):
            if self.obs_running() and self.client_reachable():
                job.log("OBS is already running.")
                return job.finish(0)
            if not self.obs_running():
                job.log("Opening OBS...")
                rc, _, err = self.open_obs()
                if rc != 0:
                    job.log(f"open failed: {err.strip()[:200]}")
                    return job.finish(1)
            deadline = time.time() + 30
            while time.time() < deadline:
                if self.obs_running() and self.client_reachable():
                    job.log("OBS is up and the WebSocket answers.")
                    return job.finish(0)
                self.sleep(1)
            job.log("OBS did not answer on the WebSocket within 30 seconds. Check Tools > WebSocket Server Settings.")
            job.finish(1)
        return self.start_job("obs-launch", fn)

    def client_reachable(self) -> bool:
        try:
            cl = self.connect()
        except Exception:
            return False
        self.close(cl)
        return True

    def plan_apply(self, body: dict) -> dict:
        name = self.check_plan(body.get("plan"))
        dry = body.get("dry_run", True)
        if not isinstance(dry, bool):
            raise ApiError(400, "dry_run must be true or false.")
        argv = self.cli("obsagent", "plan", "apply", name, *(["--dry-run"] if dry else []))
        return self.start_job("plan-apply", lambda job: self.run_cmd(job, argv, timeout=600))

    def preflight(self, body: dict) -> dict:
        name = self.check_plan(body.get("plan"))
        argv = self.cli("obsagent", "preflight", name)
        return self.start_job("preflight", lambda job: self.run_cmd(job, argv, timeout=300))

    def set_scene(self, body: dict) -> dict:
        name = body.get("name")
        cl = self.need_client()
        try:
            if not isinstance(name, str) or name not in ws.scenes(cl):
                raise ApiError(400, "Unknown scene.")
            return ws.set_scene(cl, name)
        finally:
            self.close(cl)

    def stream_start(self, body: dict) -> dict:
        given = body.get("confirm_last4")
        cl = self.need_client()
        try:
            key = self.active_key(cl)
            if not key.get("last4"):
                raise ApiError(409, "OBS has no stream key set.")
            if not isinstance(given, str) or len(given) != 4 or not hmac.compare_digest(given, key["last4"]):
                raise ApiError(403, "confirm_last4 does not match the active stream key.")
            if ws.stream_status_dict(cl).get("output_active"):
                raise ApiError(409, "Already streaming.")
            cl.start_stream()
            return {"ok": True, "action": "start-stream", "key": {k: key[k] for k in ("name", "label", "last4")}}
        finally:
            self.close(cl)

    def stream_stop(self) -> dict:
        cl = self.need_client()
        try:
            if not ws.stream_status_dict(cl).get("output_active"):
                raise ApiError(409, "Not streaming.")
            cl.stop_stream()
            return {"ok": True, "action": "stop-stream"}
        finally:
            self.close(cl)

    # -- pod producer --

    def _pidfile(self) -> Path:
        return paths.state_dir() / "bridge" / "producer.json"

    def _read_pid(self) -> dict | None:
        try:
            d = json.loads(self._pidfile().read_text())
            return d if isinstance(d, dict) and isinstance(d.get("pid"), int) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        rc, out, _ = paths.run(["ps", "-o", "stat=,command=", "-p", str(pid)], timeout=5)
        line = out.strip()
        return rc == 0 and bool(line) and not line.startswith("Z") and "podproducer" in line

    @staticmethod
    def _kill_group(pid: int, sig: int) -> None:
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            pass

    @staticmethod
    def _spawn(argv: list[str], log_path: Path) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            os.close(fd)
        threading.Thread(target=p.wait, daemon=True).start()  # reap when it exits
        return p.pid

    def producer_status(self) -> dict:
        rec = self._read_pid()
        if not rec or not self.pid_alive(rec["pid"]):
            return {"running": False}
        out = {"running": True, "pid": rec["pid"], "plan": rec.get("plan"), "dock_url": DOCK_URL,
               "started": rec.get("started"), "max_minutes": rec.get("max_minutes"), "session": None}
        try:
            sd = Path(os.environ.get("PODPRODUCER_STATE") or Path.home() / ".local" / "state" / "podproducer") / "sessions"
            newest = max((d for d in sd.iterdir() if d.is_dir() and d.stat().st_mtime >= (rec.get("started") or 0) - 2),
                         key=lambda d: d.name, default=None)
            out["session"] = newest.name if newest else None
        except OSError:
            pass
        return out

    def producer_start(self, body: dict) -> dict:
        name = self.check_plan(body.get("plan"))
        mm = body.get("max_minutes", 90)
        if isinstance(mm, bool) or not isinstance(mm, int) or not 1 <= mm <= 600:
            raise ApiError(400, "max_minutes must be a whole number from 1 to 600.")
        if self.producer_status()["running"]:
            raise ApiError(409, "The producer is already running.")
        argv = self.cli("podproducer", "run", name, "--max-minutes", str(mm))
        started = time.time()
        pid = self.spawn(argv, paths.state_dir() / "bridge" / "producer.log")
        pf = self._pidfile()
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(json.dumps({"pid": pid, "plan": name, "started": started, "max_minutes": mm}))
        return {"ok": True, "pid": pid, "plan": name, "max_minutes": mm, "dock_url": DOCK_URL}

    def producer_stop(self) -> dict:
        rec = self._read_pid()
        if not rec or not self.pid_alive(rec["pid"]):
            raise ApiError(409, "The producer is not running.")
        pid = rec["pid"]
        self.kill_group(pid, signal.SIGTERM)
        for _ in range(30):
            if not self.pid_alive(pid):
                break
            self.sleep(0.5)
        else:
            self.kill_group(pid, signal.SIGKILL)
        try:
            self._pidfile().unlink()
        except OSError:
            pass
        return {"ok": True, "stopped_pid": pid}

    # -- chat --

    def chat(self, body: dict) -> dict:
        agent = body.get("agent")
        msg = body.get("message")
        sid = body.get("session_id")
        if agent not in AGENTS:
            raise ApiError(400, "agent must be obsagent or pod-producer.")
        if not isinstance(msg, str) or not msg.strip() or len(msg) > MAX_CHAT_CHARS:
            raise ApiError(400, f"message must be 1 to {MAX_CHAT_CHARS} characters.")
        if sid is not None and (not isinstance(sid, str) or not UUID_RE.match(sid)):
            raise ApiError(400, "Bad session_id.")
        prompt = f"{AGENTS[agent]} {msg.strip()}\n\n[{CHAT_NOTE}]"
        argv = self.claude_cmd(prompt, sid)

        def fn(job: Job):
            rc = self.run_cmd(job, argv, timeout=600, capture=True)
            raw = job.captured
            text, new_sid, ok = "", sid, False
            try:
                data = json.loads(raw)
                text = str(data.get("result") or "")
                new_sid = data.get("session_id") or sid
                ok = not data.get("is_error")
            except (ValueError, AttributeError):
                job.log((raw or "claude returned no JSON").strip()[-800:])
            if job.status == "running":
                job.finish(0 if (ok and rc == 0) else 1, {"text": text, "session_id": new_sid or ""})
        return self.start_job("chat", fn)

    # -- routing --

    def route(self, method: str, path: str, body: dict) -> tuple[int, dict]:
        path = path.split("?")[0].rstrip("/") or "/"
        if method == "GET":
            if path == "/health":
                return 200, {"ok": True, "version": __version__}
            if path == "/status":
                return 200, self.status()
            if path.startswith("/jobs/"):
                return 200, self.job_view(path[len("/jobs/"):])
        elif method == "POST":
            table = {
                "/obs/launch": lambda: self.launch_obs(),
                "/plan/apply": lambda: self.plan_apply(body),
                "/preflight": lambda: self.preflight(body),
                "/scene": lambda: self.set_scene(body),
                "/producer/start": lambda: self.producer_start(body),
                "/producer/stop": lambda: self.producer_stop(),
                "/stream/start": lambda: self.stream_start(body),
                "/stream/stop": lambda: self.stream_stop(),
                "/chat": lambda: self.chat(body),
            }
            if path in table:
                return 200, table[path]()
        raise ApiError(404, "Not found.")


# ---- HTTP layer ----

def make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = "obsagent-bridge"
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # -- helpers --
        def allowed_hosts(self) -> set:
            p = self.server.server_address[1]
            return {f"127.0.0.1:{p}", f"localhost:{p}"}

        def cors_origin(self) -> str | None:
            o = self.headers.get("Origin")
            return o if o and o.startswith("chrome-extension://") else None

        def send_json(self, code: int, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            o = self.cors_origin()
            if o:
                self.send_header("Access-Control-Allow-Origin", o)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(data)

        def authed(self) -> bool:
            h = self.headers.get("Authorization", "")
            if not h.startswith("Bearer "):
                return False
            return hmac.compare_digest(h[7:].strip().encode(), bridge.token.encode())

        def handle_any(self, method: str) -> None:
            if (self.headers.get("Host") or "").lower() not in self.allowed_hosts():
                return self.send_json(403, {"error": "Bad Host header."})
            origin = self.headers.get("Origin")
            if origin and not self.cors_origin():
                return self.send_json(403, {"error": "Origin not allowed."})
            if method == "OPTIONS":
                self.send_response(204)
                o = self.cors_origin()
                if o:
                    self.send_header("Access-Control-Allow-Origin", o)
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                    self.send_header("Access-Control-Max-Age", "600")
                    self.send_header("Vary", "Origin")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path = self.path.split("?")[0]
            if not (method == "GET" and path == "/health") and not self.authed():
                return self.send_json(401, {"error": "Missing or wrong token."})
            body: dict = {}
            if method == "POST":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n < 0 or n > MAX_BODY:
                    return self.send_json(413, {"error": "Body too large."})
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw.strip() else {}
                except ValueError:
                    return self.send_json(400, {"error": "Body must be JSON."})
                if not isinstance(body, dict):
                    return self.send_json(400, {"error": "Body must be a JSON object."})
            try:
                code, obj = bridge.route(method, self.path, body)
            except ApiError as e:
                code, obj = e.code, {"error": e.msg}
            except Exception as e:  # never leak internals or secrets
                code, obj = 500, {"error": bridge.scrub(f"{type(e).__name__}: {e}")[:200]}
            self.send_json(code, obj)

        def do_GET(self):
            self.handle_any("GET")

        def do_POST(self):
            self.handle_any("POST")

        def do_OPTIONS(self):
            self.handle_any("OPTIONS")

        def do_PUT(self):
            self.send_json(405, {"error": "Method not allowed."})

        do_DELETE = do_PATCH = do_PUT

    return Handler


def make_server(bridge: Bridge, port: int = PORT) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((HOST, port), make_handler(bridge))
    srv.daemon_threads = True
    bridge.port = srv.server_address[1]
    return srv


def serve_forever(port: int = PORT) -> int:
    token = load_or_create_token()
    bridge = Bridge(token, port)
    try:
        srv = make_server(bridge, port)
    except OSError as e:
        paths.err(f"error: cannot listen on {HOST}:{port}: {e}")
        return 1
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    print(f"obsagent bridge listening on http://{HOST}:{bridge.port} (Ctrl+C to stop)", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


# ---- launchd ----

def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "obsagent-bridge.log"


def build_plist(port: int = PORT) -> dict:
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    home = str(Path.home())
    return {
        "Label": LABEL,
        "ProgramArguments": [uv, "run", "--quiet", "--script", str(paths.REPO_DIR / "obsagent"), "serve", "--port", str(port)],
        "WorkingDirectory": str(paths.REPO_DIR),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_path()),
        "StandardErrorPath": str(log_path()),
        "EnvironmentVariables": {"PATH": f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                                 "HOME": home},
    }


def _launchctl(*args: str):
    return paths.run(["launchctl", *args], timeout=20)


def install(port: int = PORT) -> dict:
    load_or_create_token()
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        plistlib.dump(build_plist(port), f)
    dom = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{dom}/{LABEL}")  # ignore: it may not be loaded yet
    rc, _, err = _launchctl("bootstrap", dom, str(p))
    if rc != 0:
        rc, _, err2 = _launchctl("load", "-w", str(p))
        err = err2 or err
    return {"installed": rc == 0, "plist": str(p), "log": str(log_path()), "error": None if rc == 0 else err.strip()[:200]}


def uninstall() -> dict:
    dom = f"gui/{os.getuid()}"
    rc, _, _ = _launchctl("bootout", f"{dom}/{LABEL}")
    p = plist_path()
    existed = p.exists()
    if existed:
        p.unlink()
    return {"uninstalled": True, "unloaded": rc == 0, "plist_removed": existed}


def status(port: int = PORT) -> dict:
    import urllib.request
    rc, _, _ = _launchctl("print", f"gui/{os.getuid()}/{LABEL}")
    healthy = False
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/health", timeout=2) as r:
            healthy = json.loads(r.read()).get("ok") is True
    except Exception:
        pass
    return {"plist": str(plist_path()), "plist_exists": plist_path().exists(), "launchd_loaded": rc == 0,
            "responding": healthy, "port": port, "token_file": str(token_path()), "token_exists": token_path().exists()}
