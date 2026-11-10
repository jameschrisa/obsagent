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
4. Mark cues Done or Skip as you go. Ctrl-C stops everything. The run also stops by itself after `--max-minutes` (default 120, 0 for no limit) or after `--idle-minutes` with no transcribed speech on either track (default 15, 0 turns it off). The Chrome panel's Stop producer button ends it too (`obsagent serve`, see chrome-extension.md).

Replay without audio: `podproducer run my-show --no-capture --transcript FILE --once`.

## After the show

`podproducer recap [SESSION] --show my-show` writes `recap.md` in the session folder: summary, 5-8 clip moments with timestamps, show notes, three title ideas. `podproducer sessions` lists sessions.

## Privacy and consent

Tell guests the show is transcribed by an AI assistant that gives the host private cues. Speech to text runs locally (whisper.cpp). Audio stays on the Mac and is deleted after transcription unless you pass `--keep-audio`. The transcript and cues are saved locally under `~/.local/state/podproducer` (mode 700). Transcript windows are sent to Anthropic through the Claude CLI to generate cues and recaps. Make sure guests know that.

Sessions are kept until you remove them. `podproducer sessions` lists them with sizes. `podproducer sessions prune --older-than 30` shows what would be deleted (a dry run); add `--yes` to delete. `podproducer sessions --delete NAME --yes` removes one session. Nothing is ever deleted without `--yes`.
