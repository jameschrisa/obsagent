# Pod producer

A live AI producer. It listens to the show and gives the host private, playful cues in an OBS dock: follow-ups, pacing nudges, bring-in-a-guest prompts, fun questions.

## Setup

1. Install BlackHole: `brew install --cask blackhole-2ch`, then restart audio or reboot. Device name: "BlackHole 2ch".
2. Whisper model at `~/.local/share/podproducer/models/ggml-small.en.bin` (whisper.cpp small.en).
3. Symlink the script: `ln -s ~/CODE/obs-agent/podproducer ~/.local/bin/podproducer`.
4. Run `podproducer check` and fix any FAIL line.

## OBS monitoring

The guests track is whatever OBS sends to BlackHole.

- OBS Settings > Audio > Advanced > Monitoring Device = BlackHole 2ch.
- For each guest source: Advanced Audio Properties > Audio Monitoring = Monitor and Output.
- Do not monitor the host mic; the host track comes straight from the Logitech StreamCam mic.

## Running a show

1. `podproducer prep my-show` adds a `producer:` block to `plans/my-show.yaml`. Fill in topics, goals, segments with target minutes, must_ask, avoid, tone, and guest bios.
2. `podproducer run my-show` (options: `--interval 40`, `--model sonnet`, `--keep-audio`).
3. In OBS: Docks > Custom Browser Docks, name "Producer", URL `http://127.0.0.1:8765`. The dock is for the host only. Never add it to a scene.
4. Mark cues Done or Skip as you go. Ctrl-C stops everything.

Replay without audio: `podproducer run my-show --no-capture --transcript FILE --once`.

## After the show

`podproducer recap [SESSION] --show my-show` writes `recap.md` in the session folder: summary, 5-8 clip moments with timestamps, show notes, three title ideas. `podproducer sessions` lists sessions.

## Privacy and consent

Tell guests the show is transcribed by an AI assistant that gives the host private cues. Transcripts and cues stay under `~/.local/state/podproducer` (mode 700). The transcript text is sent to Claude to generate cues. Audio segments are deleted after transcription unless you pass `--keep-audio`.
