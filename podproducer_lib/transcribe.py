"""Transcription: watch finished segments, run whisper-cli, append to transcript.jsonl."""
import array
import json
import math
import re
import subprocess
import threading
import time
import wave
from pathlib import Path

from . import paths
from .capture import SEGMENT_SECONDS, TRACKS

SILENCE_RMS = 0.004          # below this (full scale 0-1) the segment is treated as silence
QUIET_RMS = 0.02             # below this, stock whisper hallucinations are dropped
HALLUCINATIONS = {"thank you", "thanks", "thanks for watching", "thank you for watching", "you", "bye", "bye bye",
                  "okay", "uh", "um", "hmm", "so"}
_BRACKETED = re.compile(r"^[\[\(\*].*[\]\)\*]$")


def wav_rms(path) -> float:
    """RMS of a 16-bit mono WAV, 0-1. Returns 0.0 if unreadable."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                return 1.0
            frames = w.readframes(w.getnframes())
    except (OSError, wave.Error, EOFError):
        return 0.0
    a = array.array("h")
    a.frombytes(frames[: len(frames) // 2 * 2])
    if not a:
        return 0.0
    return math.sqrt(sum(s * s for s in a) / len(a)) / 32768.0


def _norm(text: str) -> str:
    return re.sub(r"[^a-z' ]", "", text.lower()).strip()


def clean_text(text: str, rms: float, prev_text: str = "") -> str:
    """Return cleaned text, or '' if the segment is silence or a likely hallucination."""
    t = " ".join((text or "").split())
    if not t or rms < SILENCE_RMS:
        return ""
    if _BRACKETED.match(t) or "[BLANK_AUDIO]" in t or "[SILENCE]" in t.upper():
        return ""
    n = _norm(t)
    if not n:
        return ""
    if rms < QUIET_RMS and (n in HALLUCINATIONS or n == _norm(prev_text)):
        return ""
    return t


def run_whisper(wav: Path, model: Path, timeout: int = 120) -> str:
    cmd = [paths.whisper_bin(), "-m", str(model), "-l", "en", "-nt", "-np", "-t", "4", "-f", str(wav)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return " ".join(r.stdout.split())


def append_line(transcript: Path, t_start: float, t_end: float, speaker: str, text: str) -> dict:
    rec = {"t_start": round(t_start, 1), "t_end": round(t_end, 1), "speaker": speaker, "text": text}
    with open(transcript, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def read_transcript(path) -> list:
    out = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        try:
            rec = json.loads(line)
            if isinstance(rec, dict) and "text" in rec:
                out.append(rec)
        except ValueError:
            continue
    return out


class Transcriber(threading.Thread):
    def __init__(self, session_dir: Path, model: Path, keep_audio=False, whisper=run_whisper, poll=2.0):
        super().__init__(daemon=True)
        self.dir = Path(session_dir)
        self.model = Path(model)
        self.keep_audio = keep_audio
        self.whisper = whisper
        self.poll = poll
        self.stop_evt = threading.Event()
        self.done = {t: -1 for t in TRACKS}
        self.last_text = {t: "" for t in TRACKS}
        self.transcript = self.dir / "transcript.jsonl"
        self.last_speech = time.monotonic()  # idle clock starts at launch; reset by any transcribed line

    def process_segment(self, track: str, idx: int, wav: Path) -> None:
        rms = wav_rms(wav)
        text = ""
        if rms >= SILENCE_RMS:
            try:
                text = clean_text(self.whisper(wav, self.model), rms, self.last_text[track])
            except Exception as e:  # never kill the thread
                print(f"[transcribe] {wav.name}: {e}")
        if text:
            self.last_text[track] = text
            append_line(self.transcript, idx * SEGMENT_SECONDS, (idx + 1) * SEGMENT_SECONDS, TRACKS[track], text)
            self.last_speech = time.monotonic()
        if not self.keep_audio:
            try:
                wav.unlink()
            except OSError:
                pass

    def scan(self, final=False) -> None:
        for track in TRACKS:
            files = sorted(self.dir.glob(f"{track}_*.wav"))
            for i, f in enumerate(files):
                m = re.search(r"_(\d+)\.wav$", f.name)
                idx = int(m.group(1)) if m else i
                if idx <= self.done[track]:
                    continue
                if i == len(files) - 1 and not final:
                    continue  # newest file is still being written
                self.done[track] = idx
                self.process_segment(track, idx, f)

    def run(self) -> None:
        while not self.stop_evt.is_set():
            self.scan()
            self.stop_evt.wait(self.poll)
        self.scan(final=True)

    def stop(self) -> None:
        self.stop_evt.set()
        self.join(timeout=60)
