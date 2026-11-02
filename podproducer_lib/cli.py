"""podproducer command line."""
import argparse
import configparser
import json
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

from . import brain, capture, dock, paths, plan, transcribe

CONSENT = ("Reminder: tell every guest that this show is transcribed by an AI assistant that gives the host private cues. "
           "Transcripts stay local under ~/.local/state/podproducer. Audio is deleted after transcription unless --keep-audio.")

RECAP_SYSTEM = ("You are a podcast producer writing a post-show recap for the host. Be warm and a little playful. "
                "Only use what is in the transcript; never invent quotes or put words in anyone's mouth. "
                "Do not use em-dashes or en-dashes. Output Markdown with these sections: "
                "## Summary, ## Highlight moments (5-8 items, each with a start timestamp m:ss and why it works as a clip), "
                "## Show notes, ## Title ideas (3).")


def cmd_prep(a) -> int:
    path = paths.plan_path(a.show)
    try:
        action, missing = plan.prep(path)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}")
        return 1
    print(f"{path}: producer block {action}" + (f" (added: {', '.join(missing)})" if missing else ""))
    print("Next: fill in topics, goals, segments (with target minutes), must_ask, avoid and the guest bios.\n"
          "Tip: in Claude Code, ask the pod-producer skill to research your guests and draft the run of show and question bank.")
    return 0


def _line(ok, label, detail="") -> bool:
    print(f"[{'ok' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    return ok


def obs_monitoring_device():
    base = Path.home() / "Library" / "Application Support" / "obs-studio"
    # Read the ACTIVE profile only (user.ini/global.ini [Basic] ProfileDir); other profiles keep their own value.
    files = []
    for ini in (base / "user.ini", base / "global.ini"):
        try:
            cp = configparser.ConfigParser(strict=False, interpolation=None)
            cp.optionxform = str
            cp.read(ini)
            d = cp.get("Basic", "ProfileDir", fallback="")
            if d:
                files.append(base / "basic" / "profiles" / d / "basic.ini")
                break
        except Exception:
            continue
    if not files:
        files = [base / "global.ini", *sorted(base.glob("basic/profiles/*/basic.ini"))]
    for f in files:
        try:
            cp = configparser.ConfigParser(strict=False, interpolation=None)
            cp.read(f)
            for sec in cp.sections():
                for k, v in cp.items(sec):
                    if k.lower() in ("monitoringdevicename", "monitoringdeviceid") and v:
                        return v
        except Exception:
            continue
    return None


def cmd_check(a) -> int:
    ok = True
    try:
        devices = capture.list_audio_devices()
        ok &= _line(True, "ffmpeg audio devices", ", ".join(devices) or "none")
    except Exception as e:
        devices = {}
        ok &= _line(False, "ffmpeg", str(e))
    ok &= _line(capture.resolve_device(devices, a.host_device) is not None, f"host mic {a.host_device!r}")
    bh = capture.resolve_device(devices, a.guest_device) is not None
    ok &= _line(bh, f"guest track {a.guest_device!r}", "" if bh else "install BlackHole 2ch (brew install --cask blackhole-2ch), then reboot or restart coreaudiod")
    m = paths.model_path()
    ok &= _line(m.exists(), "whisper model", str(m))
    if m.exists():
        try:
            with tempfile.TemporaryDirectory() as td:
                wav = Path(td) / "silence.wav"
                with wave.open(str(wav), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(16000)
                    w.writeframes(b"\x00\x00" * 16000)
                t0 = time.time()
                transcribe.run_whisper(wav, m, timeout=60)
                ok &= _line(True, "whisper-cli runs", f"{time.time() - t0:.1f}s for 1s of silence")
        except Exception as e:
            ok &= _line(False, "whisper-cli", str(e))
    ok &= _line(shutil.which(paths.claude_bin()) is not None or Path(paths.claude_bin()).exists(), "claude CLI", paths.claude_bin())
    mon = obs_monitoring_device()
    print(f"[info] OBS monitoring device: {mon or 'not set or not readable'}")
    if not mon or "blackhole" not in str(mon).lower():
        print("[todo] OBS Settings > Audio > Advanced > Monitoring Device = BlackHole 2ch")
    print("[todo] For each guest source: Advanced Audio Properties > Audio Monitoring = Monitor and Output")
    print("[todo] Do not monitor your own mic into BlackHole, or the host track will leak into the guests track")
    return 0 if ok else 1


def _load_brief(show):
    p = paths.plan_path(show)
    if not p.exists():
        print(f"warning: no plan at {p}; running with an empty brief")
        return {}
    return plan.load_brief(p)


def cmd_run(a) -> int:
    print(CONSENT)
    brief = _load_brief(a.show)
    guests = plan.merged_guests(brief, a.show)
    sess = paths.new_session_dir()
    store = brain.CueStore(sess, brief)
    transcript = sess / "transcript.jsonl"
    cap = trans = None
    if a.no_capture:
        if not a.transcript:
            print("error: --no-capture needs --transcript FILE")
            return 1
        transcript = Path(a.transcript)
        recs = transcribe.read_transcript(transcript)
        store.replay_elapsed = max([r.get("t_end", 0) for r in recs] or [0])
    else:
        model = paths.model_path()
        if not model.exists():
            print(f"error: whisper model missing at {model}")
            return 1
        cap = capture.Capture(sess, a.host_device, a.guest_device)
        for w in cap.start():
            print(f"warning: {w}")
        if not cap.procs:
            print("error: no audio tracks started")
            return 1
        trans = transcribe.Transcriber(sess, model, keep_audio=a.keep_audio)
        trans.start()
    b = brain.Brain(store, transcript, guests, a.interval, a.model)
    srv = None
    try:
        if not a.no_dock:
            srv = dock.start_dock(store, a.port)
            port = srv.server_address[1]
            print(f"Dock: http://127.0.0.1:{port}")
            print(dock.DOCK_HELP.format(port=port))
        print(f"Session: {sess}")
        if a.once:
            for c in b.step(store.replay_elapsed):
                print(f"[cue p{c['priority']}] ({c['type']}) {c['text']}")
            return 0
        print("Running. Ctrl-C to stop.")
        b.run()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        b.stop()
        if cap:
            cap.stop()
        if trans:
            trans.stop()
        if srv:
            srv.shutdown()
    return 0


def _resolve_session(name):
    if name:
        p = Path(name)
        if not p.is_dir():
            p = paths.sessions_dir() / name
        return p if p.is_dir() else None
    return paths.latest_session()


def cmd_recap(a) -> int:
    sess = _resolve_session(a.session)
    if not sess:
        print("error: no session found")
        return 1
    recs = transcribe.read_transcript(sess / "transcript.jsonl")
    if not recs:
        print(f"error: empty transcript in {sess}")
        return 1
    text = "\n".join(f"[{brain.fmt_clock(r.get('t_start', 0))}] {r.get('speaker')}: {r.get('text')}" for r in recs)
    brief_note = ""
    if a.show:
        b = _load_brief(a.show)
        brief_note = f"Show brief: {json.dumps(b)[:2000]}\n\n"
    try:
        out = brain.call_claude(f"{brief_note}Transcript:\n{text}", model=a.model, system_text=RECAP_SYSTEM, timeout=300)
    except Exception as e:
        print(f"error: recap failed: {e}")
        return 1
    (sess / "recap.md").write_text(out.strip() + "\n")
    print(f"Wrote {sess / 'recap.md'}")
    return 0


def cmd_sessions(a) -> int:
    for s in paths.list_sessions():
        n = len(transcribe.read_transcript(s / "transcript.jsonl"))
        print(f"{s.name}  {n} lines" + ("  recap" if (s / "recap.md").exists() else ""))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="podproducer", description="Live AI producer: private, playful cues for the host.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prep", help="add a producer brief skeleton to plans/SHOW.yaml")
    s.add_argument("show")
    s.set_defaults(fn=cmd_prep)
    s = sub.add_parser("check", help="check devices, model, whisper, claude, OBS monitoring")
    s.add_argument("--host-device", default=capture.DEFAULT_HOST_DEVICE)
    s.add_argument("--guest-device", default=capture.DEFAULT_GUEST_DEVICE)
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("run", help="listen and produce live")
    s.add_argument("show")
    s.add_argument("--interval", type=int, default=40)
    s.add_argument("--model", default="sonnet")
    s.add_argument("--host-device", default=capture.DEFAULT_HOST_DEVICE)
    s.add_argument("--guest-device", default=capture.DEFAULT_GUEST_DEVICE)
    s.add_argument("--port", type=int, default=dock.PORT)
    s.add_argument("--keep-audio", action="store_true")
    s.add_argument("--no-capture", action="store_true", help="replay mode, use with --transcript")
    s.add_argument("--transcript")
    s.add_argument("--no-dock", action="store_true")
    s.add_argument("--once", action="store_true", help="one brain cycle then exit (replay and testing)")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("recap", help="summary, highlights, show notes and titles for a session")
    s.add_argument("session", nargs="?")
    s.add_argument("--show")
    s.add_argument("--model", default="sonnet")
    s.set_defaults(fn=cmd_recap)
    s = sub.add_parser("sessions", help="list sessions")
    s.set_defaults(fn=cmd_sessions)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)
