"""Producer brain: build a prompt, call `claude -p`, parse cues, keep a shared cue store."""
import json
import re
import subprocess
import threading
import time
from pathlib import Path

from . import paths
from .transcribe import read_transcript

CUE_TYPES = {"follow_up", "bring_in", "pace", "segue", "fun", "fact_check", "time"}
MAX_CUES = 3
MAX_TEXT = 120
WINDOW_SECONDS = 240
PROMPT_FILE = Path(__file__).parent / "prompts" / "producer.md"
CALL_TIMEOUT = 60


def fmt_clock(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def segment_status(brief: dict, elapsed: float) -> dict:
    """Which planned segment we are in. Returns {name, index, minutes_left, over, next}."""
    segs = [s for s in (brief.get("segments") or []) if isinstance(s, dict)]
    t = 0.0
    for i, s in enumerate(segs):
        end = t + float(s.get("minutes", 0) or 0) * 60
        if elapsed < end or i == len(segs) - 1:
            return {"name": s.get("name", f"Segment {i + 1}"), "index": i,
                    "minutes_left": round((end - elapsed) / 60, 1), "over": elapsed > end,
                    "next": segs[i + 1].get("name") if i + 1 < len(segs) else None}
        t = end
    return {"name": None, "index": None, "minutes_left": None, "over": False, "next": None}


def _lines(v) -> str:
    if isinstance(v, (list, tuple)):
        return "\n".join(f"  - {x}" for x in v)
    return f"  {v}"


def build_prompt(brief: dict, guests: list, elapsed: float, transcript: list, prior_cues: list) -> str:
    out = ["## Show brief"]
    for key in ("topics", "goals", "must_ask", "avoid"):
        if brief.get(key):
            out.append(f"{key}:\n{_lines(brief[key])}")
    if brief.get("tone"):
        out.append(f"tone: {brief['tone']}")
    if guests:
        out.append("## Guests")
        for g in guests:
            out.append(f"- {g.get('name', '?')}: {g.get('bio', '') or 'no bio'}")
    seg = segment_status(brief, elapsed)
    out.append("## Clock")
    out.append(f"Elapsed show time: {fmt_clock(elapsed)}")
    if seg["name"]:
        state = "OVER its planned time" if seg["over"] else f"{seg['minutes_left']} min left"
        out.append(f"Current segment: {seg['name']} ({state}). Next: {seg['next'] or 'none'}")
        out.append("Plan: " + ", ".join(f"{s.get('name')} {s.get('minutes')}m" for s in brief.get("segments", [])))
    window = [r for r in transcript if r.get("t_end", 0) >= elapsed - WINDOW_SECONDS]
    out.append(f"## Transcript (last {WINDOW_SECONDS // 60} minutes)")
    out.append("\n".join(f"[{fmt_clock(r.get('t_start', 0))}] {r.get('speaker', '?')}: {r.get('text', '')}" for r in window)
               or "(no speech yet)")
    out.append("## Previous cues (do not repeat)")
    out.append("\n".join(f"- ({c.get('type')}) {c.get('text')}" for c in prior_cues[-12:]) or "(none)")
    out.append("Respond with the strict JSON object only.")
    return "\n\n".join(out)


def parse_response(raw: str) -> dict | None:
    """Parse model output into {cues, energy, talk_share}. Tolerates fences and surrounding prose."""
    if not raw:
        return None
    s = raw.strip()
    obj = None
    for cand in [s] + re.findall(r"```(?:json)?\s*(.*?)```", s, re.S):
        try:
            obj = json.loads(cand)
            break
        except ValueError:
            continue
    if obj is None:
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j > i:
            try:
                obj = json.loads(s[i:j + 1])
            except ValueError:
                obj = None
    if not isinstance(obj, dict):
        return None
    cues = []
    for c in obj.get("cues") or []:
        if not isinstance(c, dict) or not str(c.get("text", "")).strip():
            continue
        t = c.get("type") if c.get("type") in CUE_TYPES else "fun"
        try:
            pr = min(3, max(1, int(c.get("priority", 2))))
        except (TypeError, ValueError):
            pr = 2
        cues.append({"type": t, "text": str(c["text"]).strip()[:MAX_TEXT], "why": str(c.get("why", "")).strip()[:80], "priority": pr})
    energy = obj.get("energy") if obj.get("energy") in ("low", "ok", "high") else "ok"
    share = obj.get("talk_share") if isinstance(obj.get("talk_share"), dict) else {}
    ts = {}
    for k in ("Host", "Guests"):
        try:
            ts[k] = max(0.0, min(1.0, float(share.get(k, 0))))
        except (TypeError, ValueError):
            ts[k] = 0.0
    return {"cues": cues[:MAX_CUES], "energy": energy, "talk_share": ts}


def call_claude(prompt: str, model="sonnet", system_file=PROMPT_FILE, timeout=CALL_TIMEOUT, system_text=None) -> str:
    """Run `claude -p` and return the model's text (the `result` field of the json envelope)."""
    system = system_text if system_text is not None else Path(system_file).read_text()
    cmd = [paths.claude_bin(), "-p", "--output-format", "json", "--model", model, "--system-prompt", system]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout)[:200]}")
    try:
        env = json.loads(r.stdout)
        if isinstance(env, dict) and "result" in env:
            if env.get("is_error"):
                raise RuntimeError(f"claude error: {str(env.get('result'))[:200]}")
            return str(env["result"])
    except ValueError:
        pass
    return r.stdout


class CueStore:
    """Thread-safe cue list and live status, persisted to cues.json in the session dir."""

    def __init__(self, session_dir: Path | None = None, brief: dict | None = None):
        self.lock = threading.Lock()
        self.cues = []
        self.next_id = 1
        self.energy = "ok"
        self.talk_share = {"Host": 0.0, "Guests": 0.0}
        self.brief = brief or {}
        self.started = time.time()
        self.replay_elapsed = None
        self.file = Path(session_dir) / "cues.json" if session_dir else None

    def elapsed(self) -> float:
        return self.replay_elapsed if self.replay_elapsed is not None else time.time() - self.started

    def add(self, cues: list, elapsed: float) -> list:
        added = []
        with self.lock:
            for c in cues:
                rec = dict(c, id=self.next_id, at=round(elapsed, 1), status="new")
                self.next_id += 1
                self.cues.append(rec)
                added.append(rec)
            self._save()
        return added

    def ack(self, cue_id: int, status: str) -> bool:
        if status not in ("done", "skipped"):
            return False
        with self.lock:
            for c in self.cues:
                if c["id"] == cue_id:
                    c["status"] = status
                    self._save()
                    return True
        return False

    def snapshot(self) -> dict:
        with self.lock:
            el = self.elapsed()
            return {"elapsed": round(el, 1), "clock": fmt_clock(el), "segment": segment_status(self.brief, el),
                    "energy": self.energy, "talk_share": dict(self.talk_share),
                    "cues": [dict(c) for c in reversed(self.cues)]}

    def _save(self) -> None:
        if self.file:
            try:
                self.file.write_text(json.dumps({"cues": self.cues}, indent=1))
            except OSError:
                pass


class Brain:
    def __init__(self, store: CueStore, transcript_path, guests=None, interval=40, model="sonnet", caller=call_claude):
        self.store = store
        self.transcript_path = transcript_path
        self.guests = guests or []
        self.interval = interval
        self.model = model
        self.caller = caller
        self.stop_evt = threading.Event()
        self.last_seen = -1

    def step(self, elapsed: float | None = None) -> list:
        """One cycle. Returns newly added cues; never raises."""
        try:
            transcript = read_transcript(self.transcript_path)
            if elapsed is None:
                elapsed = self.store.elapsed()
            sig = len(transcript)
            if sig == self.last_seen and sig > 0:
                return []  # nothing new was said
            self.last_seen = sig
            prompt = build_prompt(self.store.brief, self.guests, elapsed, transcript, self.store.cues)
            raw = self.caller(prompt, model=self.model)
            parsed = parse_response(raw)
            if parsed is None:
                print(f"[brain] could not parse model output: {str(raw)[:120]!r}")
                return []
            self.store.energy = parsed["energy"]
            self.store.talk_share = parsed["talk_share"]
            return self.store.add(parsed["cues"], elapsed)
        except subprocess.TimeoutExpired:
            print(f"[brain] claude call timed out after {CALL_TIMEOUT}s; continuing")
        except Exception as e:
            print(f"[brain] cycle failed: {e}; continuing")
        return []

    def run(self) -> None:
        while not self.stop_evt.is_set():
            for c in self.step():
                print(f"[cue p{c['priority']}] ({c['type']}) {c['text']}")
            self.stop_evt.wait(self.interval)

    def stop(self) -> None:
        self.stop_evt.set()
