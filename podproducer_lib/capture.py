"""Audio capture: one ffmpeg avfoundation process per track, rolling 10 s 16 kHz mono WAV segments."""
import re
import subprocess
from pathlib import Path

from . import paths

SEGMENT_SECONDS = 10
DEFAULT_HOST_DEVICE = "Logitech StreamCam"
DEFAULT_GUEST_DEVICE = "BlackHole 2ch"
TRACKS = {"host": "Host", "guests": "Guests"}


def parse_audio_devices(stderr: str) -> dict:
    """Parse `ffmpeg -f avfoundation -list_devices true -i ""` stderr into {name: index} for audio devices."""
    out, in_audio = {}, False
    for line in stderr.splitlines():
        if "AVFoundation audio devices" in line:
            in_audio = True
            continue
        if "AVFoundation video devices" in line:
            in_audio = False
            continue
        m = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
        if in_audio and m and "AVFoundation" not in m.group(2):
            out[m.group(2)] = int(m.group(1))
    return out


def list_audio_devices() -> dict:
    r = subprocess.run([paths.ffmpeg_bin(), "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True, timeout=20)
    return parse_audio_devices(r.stderr)


def resolve_device(devices: dict, name: str):
    """Exact match first, then case-insensitive substring. Returns index or None."""
    if name in devices:
        return devices[name]
    low = name.lower()
    for n, i in devices.items():
        if low in n.lower():
            return i
    return None


def ffmpeg_cmd(index: int, out_dir: Path, track: str) -> list:
    return [paths.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "avfoundation", "-i", f":{index}", "-ac", "1", "-ar", "16000",
            "-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-reset_timestamps", "1",
            str(Path(out_dir) / f"{track}_%05d.wav")]


class Capture:
    def __init__(self, session_dir: Path, host_device=DEFAULT_HOST_DEVICE, guest_device=DEFAULT_GUEST_DEVICE):
        self.dir = Path(session_dir)
        self.names = {"host": host_device, "guests": guest_device}
        self.procs = {}

    def start(self) -> list:
        """Start ffmpeg per track. Returns a list of warnings for tracks that could not start."""
        warnings = []
        devices = list_audio_devices()
        for track, name in self.names.items():
            idx = resolve_device(devices, name)
            if idx is None:
                warnings.append(f"audio device not found for {track}: {name!r} (found: {', '.join(devices) or 'none'})")
                continue
            self.procs[track] = subprocess.Popen(ffmpeg_cmd(idx, self.dir, track), stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return warnings

    def stop(self) -> None:
        for p in self.procs.values():
            if p.poll() is None:
                p.terminate()
        for p in self.procs.values():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs.clear()
