import json
import subprocess
import urllib.request
import wave

import pytest
import yaml

from podproducer_lib import brain, capture, cli, dock, plan, transcribe

LIST_OUT = """[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] Logitech StreamCam
[AVFoundation indev @ 0x1] [1] Capture screen 0
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] Logitech StreamCam
[AVFoundation indev @ 0x1] [1] BlackHole 2ch
[AVFoundation indev @ 0x1] [2] MacBook Pro Microphone
"""

BRIEF = {"topics": ["bees"], "must_ask": ["why bees?"], "avoid": ["divorce"], "tone": "playful",
         "segments": [{"name": "Open", "minutes": 2}, {"name": "Main", "minutes": 10}]}
TRANSCRIPT = [{"t_start": 0, "t_end": 10, "speaker": "Host", "text": "Welcome to the show about bees."},
              {"t_start": 10, "t_end": 20, "speaker": "Guests", "text": "Thanks, I keep forty hives."},
              {"t_start": 20, "t_end": 30, "speaker": "Guests", "text": "One hive named itself Gerald."}]
GOOD = {"cues": [{"type": "follow_up", "text": "Ask about Gerald the hive", "why": "story hook", "priority": 1}],
        "energy": "high", "talk_share": {"Host": 0.3, "Guests": 0.7}}


def test_parse_devices():
    d = capture.parse_audio_devices(LIST_OUT)
    assert d == {"Logitech StreamCam": 0, "BlackHole 2ch": 1, "MacBook Pro Microphone": 2}
    assert capture.resolve_device(d, "BlackHole 2ch") == 1
    assert capture.resolve_device(d, "macbook") == 2
    assert capture.resolve_device(d, "nope") is None


def test_ffmpeg_cmd(tmp_path):
    c = capture.ffmpeg_cmd(1, tmp_path, "guests")
    assert ":1" in c and "segment" in c and c[-1].endswith("guests_%05d.wav")


def make_wav(path, amp, n=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        import struct
        w.writeframes(b"".join(struct.pack("<h", amp if i % 2 else -amp) for i in range(n)))


def test_filters(tmp_path):
    make_wav(tmp_path / "s.wav", 0)
    make_wav(tmp_path / "l.wav", 6000)
    assert transcribe.wav_rms(tmp_path / "s.wav") < transcribe.SILENCE_RMS
    assert transcribe.wav_rms(tmp_path / "l.wav") > 0.1
    assert transcribe.clean_text("hello there", 0.0001) == ""
    assert transcribe.clean_text("[BLANK_AUDIO]", 0.5) == ""
    assert transcribe.clean_text("Thank you.", 0.01) == ""
    assert transcribe.clean_text("Thank you.", 0.2) == "Thank you."
    assert transcribe.clean_text("Same thing", 0.01, "same thing") == ""
    assert transcribe.clean_text("  real   words ", 0.1) == "real words"


def test_transcriber_segment(tmp_path):
    make_wav(tmp_path / "host_00000.wav", 6000)
    make_wav(tmp_path / "guests_00000.wav", 0)
    t = transcribe.Transcriber(tmp_path, tmp_path / "m.bin", whisper=lambda w, m: "Hello world")
    t.scan(final=True)
    recs = transcribe.read_transcript(tmp_path / "transcript.jsonl")
    assert recs == [{"t_start": 0, "t_end": 10, "speaker": "Host", "text": "Hello world"}]
    assert not list(tmp_path.glob("*.wav"))


def test_segment_status():
    assert brain.segment_status(BRIEF, 30)["name"] == "Open"
    s = brain.segment_status(BRIEF, 200)
    assert s["name"] == "Main" and not s["over"]
    assert brain.segment_status(BRIEF, 9999)["over"]


def test_prompt_builder():
    p = brain.build_prompt(BRIEF, [{"name": "Ann", "bio": "beekeeper"}], 125, TRANSCRIPT,
                           [{"type": "fun", "text": "Prior cue text"}])
    for needle in ("bees", "why bees?", "divorce", "Ann: beekeeper", "2:05", "Current segment: Main", "Gerald", "Prior cue text"):
        assert needle in p


def test_prompt_window_trims_old():
    old = [{"t_start": 0, "t_end": 5, "speaker": "Host", "text": "ancient"}]
    assert "ancient" not in brain.build_prompt({}, [], 1000, old, [])


def test_parse_response():
    assert brain.parse_response(json.dumps(GOOD))["cues"][0]["priority"] == 1
    fenced = "Sure!\n```json\n" + json.dumps(GOOD) + "\n```"
    assert brain.parse_response(fenced)["energy"] == "high"
    assert brain.parse_response("blah " + json.dumps(GOOD) + " trailing")["cues"]
    assert brain.parse_response("total garbage") is None
    assert brain.parse_response("") is None
    many = {"cues": [{"type": "weird", "text": "x" * 300, "priority": 9}] * 5}
    r = brain.parse_response(json.dumps(many))
    assert len(r["cues"]) == 3 and len(r["cues"][0]["text"]) == 120 and r["cues"][0]["priority"] == 3
    assert r["cues"][0]["type"] == "fun" and r["energy"] == "ok"


def test_call_claude_envelope(monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw["input"]
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "hi", "is_error": False}), "")
    monkeypatch.setattr(brain.subprocess, "run", fake)
    assert brain.call_claude("PROMPT", model="sonnet") == "hi"
    assert "--output-format" in seen["cmd"] and "json" in seen["cmd"] and seen["input"] == "PROMPT"


def test_brain_survives_failures(tmp_path, monkeypatch):
    tp = tmp_path / "t.jsonl"
    tp.write_text("\n".join(json.dumps(r) for r in TRANSCRIPT))
    store = brain.CueStore(tmp_path, BRIEF)
    calls = []

    def caller(prompt, model):
        calls.append(1)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("claude", 60)
        if len(calls) == 2:
            raise RuntimeError("boom")
        if len(calls) == 3:
            return "garbage"
        return json.dumps(GOOD)
    b = brain.Brain(store, tp, caller=caller)
    for _ in range(3):
        b.last_seen = -1
        assert b.step(30) == []
    b.last_seen = -1
    got = b.step(30)
    assert len(got) == 1 and store.energy == "high"
    assert b.step(30) == []  # no new transcript, no new call
    assert len(calls) == 4
    assert json.loads((tmp_path / "cues.json").read_text())["cues"][0]["text"].startswith("Ask about")


def test_dock(tmp_path):
    store = brain.CueStore(tmp_path, BRIEF)
    store.add(GOOD["cues"], 5)
    srv = dock.start_dock(store, 0)
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        assert srv.server_address[0] == "127.0.0.1"
        assert "Producer" in urllib.request.urlopen(base + "/").read().decode()
        d = json.load(urllib.request.urlopen(base + "/cues.json"))
        assert d["cues"][0]["status"] == "new" and d["segment"]["name"] == "Open"
        req = urllib.request.Request(base + "/ack", json.dumps({"id": 1, "status": "done"}).encode(), {"Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(req))["ok"]
        assert json.load(urllib.request.urlopen(base + "/cues.json"))["cues"][0]["status"] == "done"
        bad = urllib.request.Request(base + "/ack", json.dumps({"id": 99, "status": "done"}).encode())
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
    finally:
        srv.shutdown()


def test_prep(tmp_path, monkeypatch):
    monkeypatch.setenv("PODPRODUCER_PLANS_DIR", str(tmp_path))
    f = tmp_path / "demo.yaml"
    f.write_text("# keep me\ntitle: Demo\n")
    assert cli.main(["prep", "demo"]) == 0
    text = f.read_text()
    assert text.startswith("# keep me") and "producer:" in text
    d = yaml.safe_load(text)
    assert d["title"] == "Demo" and d["producer"]["segments"]
    before = f.read_text()
    assert cli.main(["prep", "demo"]) == 0
    assert f.read_text() == before
    f.write_text("title: X\nproducer:\n  tone: spicy\n")
    cli.main(["prep", "demo"])
    d = yaml.safe_load(f.read_text())
    assert d["producer"]["tone"] == "spicy" and "question_bank" in d["producer"]
    assert cli.main(["prep", "missing"]) == 1


def test_replay_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PODPRODUCER_PLANS_DIR", str(tmp_path))
    monkeypatch.setenv("PODPRODUCER_STATE", str(tmp_path / "state"))
    (tmp_path / "demo.yaml").write_text(yaml.safe_dump({"producer": BRIEF}))
    tr = tmp_path / "t.jsonl"
    tr.write_text("\n".join(json.dumps(r) for r in TRANSCRIPT))
    monkeypatch.setattr(brain, "call_claude", lambda prompt, model="sonnet", **kw: json.dumps(GOOD))
    rc = cli.main(["run", "demo", "--no-capture", "--transcript", str(tr), "--once", "--no-dock"])
    out = capsys.readouterr().out
    assert rc == 0 and "Gerald" in out and "transcribed by an AI" in out
    sess = list((tmp_path / "state" / "sessions").iterdir())[0]
    assert json.loads((sess / "cues.json").read_text())["cues"]


def test_recap(tmp_path, monkeypatch):
    monkeypatch.setenv("PODPRODUCER_STATE", str(tmp_path))
    s = tmp_path / "sessions" / "20260101-000000"
    s.mkdir(parents=True)
    (s / "transcript.jsonl").write_text("\n".join(json.dumps(r) for r in TRANSCRIPT))
    monkeypatch.setattr(brain, "call_claude", lambda prompt, **kw: "## Summary\nBees.")
    assert cli.main(["recap"]) == 0
    assert "Bees" in (s / "recap.md").read_text()


def test_no_dashes_in_sources():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    bad = ("\u2013", "\u2014", "\u2015")
    for p in list((root / "podproducer_lib").rglob("*")) + [root / "docs" / "pod-producer.md"]:
        if p.is_file() and p.suffix in (".py", ".md"):
            assert not any(c in p.read_text() for c in bad), p
