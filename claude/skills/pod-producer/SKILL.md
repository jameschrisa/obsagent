---
name: pod-producer
description: Live AI producer for podcasts and live shows. Prep a show (guest research, run of show, playful question bank), check the audio setup, run live cues in an OBS dock, and recap afterwards. Triggers on "pod producer", "podcast producer", "show producer", "question bank", "run of show", "live cues", "recap".
---

# pod-producer

The tool is `./podproducer` in the obs-agent repo (symlinked to PATH by the user). Show plans live in `plans/<show>.yaml`; the producer brief is its `producer:` block. Guest rosters live in `~/.config/obsagent/shows/<show>.json` (read only).

## Routing

- `prep <show>`: run `podproducer prep <show>` to add the skeleton. Then research each guest with WebSearch if available (public, professional facts only, with sources). Build a run of show with target minutes per segment, a playful question bank (10-15 questions, mixed depth and fun), must_ask and avoid lists, and a cold open. Write them into the plan's `producer:` block with Edit. Never overwrite values the host already wrote; propose changes instead. Show the host a short summary.
- `check`: run `podproducer check`, explain any FAIL line with the fix. Remind the host of the OBS monitoring steps (Settings > Audio > Advanced > Monitoring Device = BlackHole 2ch; each guest source Advanced Audio Properties > Audio Monitoring = Monitor and Output).
- `live <show>`: first state the consent rule below. Start `podproducer run <show>` in the background (Bash run_in_background), then tell the host to open OBS > Docks > Custom Browser Docks, name "Producer", URL http://127.0.0.1:8765. Do not start streams or change OBS settings.
- `cues`: read the latest session's `cues.json` under `~/.local/state/podproducer/sessions/` and summarize open cues in order of priority.
- `recap [session]`: run `podproducer recap [session] --show <show>`, then give the host the highlights with timestamps and the three title ideas.
- `sessions`: run `podproducer sessions`.

## Tone rules

- Playful, warm, witty, never mean. Short cues (120 characters or fewer).
- Never put words in a guest's mouth. Quote only what the transcript shows.
- Cues are private to the host. The dock is never added to a scene or the stream.
- Never push a guest on a sensitive topic. Respect the avoid list.
- No em-dashes or en-dashes.

## Consent rule

Before any live run, remind the host to tell every guest that the show is transcribed by an AI assistant that gives the host private cues. Transcripts and audio stay local (mode 700 under `~/.local/state/podproducer`); audio is deleted after transcription unless `--keep-audio`.

Return at most 200 words.
