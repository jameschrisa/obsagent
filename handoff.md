# obsagent handoff

Last session: 2026-10-31, ended about 21:10 PT. Read this first, then SPEC.md if you need the design.

## Where we are

OBS is fully set up for a YouTube talking-head show, and one short test stream reached YouTube. **Next step: a test stream of at least 60 seconds, unlisted, watched with `/obsagent live`.**

The first test (21:03) connected to `rtmps://a.rtmps.youtube.com:443/live2` and sent 1,003 frames at about 17 Mbps with 0 dropped frames. The user stopped it after 17 seconds, before the YouTube Studio preview could appear, and nothing has streamed since. YouTube Studio needs about 10-30 s of data before the preview shows.

## What exists

- **Repo** `~/CODE/obs-agent`: `git init` was run but nothing is committed, so every file is untracked.
- **CLI** `obsagent`: a uv script, symlinked at `~/.local/bin/obsagent`. Code is in `obsagent_lib/`, and `--json` works on every command.
- **Claude Code entry points:**
  - Skill: `claude/skills/obsagent/SKILL.md`, symlinked to `~/.claude/skills/obsagent`. Called as `/obsagent`.
  - Subagent: `claude/agents/obsagent.md`, symlinked to `~/.claude/agents/obsagent.md`. Called as `obsagent`.
- **Knowledge base** `knowledge/`: 8 files, indexed in `knowledge/INDEX.md`. Every number has a source URL.
- **Presets** `presets/`: 9 presets. The YouTube ones use YouTube's recommended H.264 bitrates and 128k AAC.
- **Show plans** `plans/`: `talking-head.yaml` is the live plan, and `example.yaml` is the sample.
- **Tests:** 62 pass. Run them with `uv run -q --with pytest --with pyyaml --with obsws-python pytest -q`.

## Live OBS state (OBS 33.0.1, obs-websocket 5.7.6)

- **Profile `obsagent-youtube-1080p60`** (active; its directory is named `obsagentyoutube1080p60`):
  - Advanced output mode.
  - Apple VideoToolbox H.264, 17000 kbps CBR, keyframe 2 s, High profile, no B-frames.
  - 1920x1080 at 60 fps.
  - CoreAudio AAC at 128k, 48 kHz.
  - ApplyServiceSettings=false.
- **Profile `Untitled`:** restored to the original defaults (Simple mode, x264).
- **Stream service:** rtmp_custom to YouTube RTMPS, currently using the default key.
- **Stream keys** (Keychain service `obsagent`, see `obsagent stream list-keys`):
  - `youtube`: the default account, ends `awft`. This is the one OBS uses now.
  - `youtube:alt`: the second YouTube account, ends `8zpy`, added 2026-11-01. Switch with `obsagent stream set-service --target youtube --rtmps --key-name alt`.
  - The `youtube:alt` key was pasted into a chat. Resetting it in YouTube Studio is recommended, followed by re-storing it with `set-key --key-name alt`.
- **WebSocket:** port 4455, auth on. The password is in the Keychain (service `obsagent`, account `obs-websocket`).
- **Scenes** (collection "Untitled"):
  - `Camera`: StreamCam full frame, plus StreamCam Mic.
  - `Screen + camera`: main display full frame (`display_uuid` 0EAFFB01-D888-45A3-933A-9B23725F24CC), with a StreamCam inset of 480x270 at (1400, 770), plus StreamCam Mic.
  - The default `Scene` is untouched.
  - `Mic/Aux` (MacBook mic) is muted, not deleted.
- **macOS permissions:** granted. The OBS log at startup confirms camera, mic, screen capture and input monitoring.
- **Machine:**
  - M1 Pro, 16 GB, macOS 14.6.1.
  - Upload measured at 393 Mbps.
  - About 50 GB free on disk.

## Open items, in order

1. **60-second unlisted test stream.** In YouTube Studio go to Create > Go live, open the **Stream** tab (not Webcam), and select the stored key. Start in OBS (or the user says "start the stream"), then run `obsagent ws monitor --seconds 120 --json`. Confirm the preview appears and stream health is good.
2. **Watch render lag.** The first run had 0.7% lagged frames (7 of 1003) from rendering stalls. 60 fps screen capture is the most likely cause. If it rises above 1%, try 30 fps on the screen source or the 1080p30 preset.
3. **Screen privacy.** The `Screen + camera` scene captures the whole main display, including OBS itself, the terminal, tabs and notifications. The user was offered window capture instead and hasn't answered.
4. **Claude Docs sync for plans** (SKILL.md, "Pre-planning with Claude Docs"). It is written but not tried yet. The user asked to link a "Claude Duet" account. No such connector exists, so Claude Docs stands in, and the user hasn't confirmed whether Duet is something else.
5. **YouTube API commands** (`obsagent youtube ...`). These are untested and need `~/.config/obsagent/client_secret.json` (an OAuth desktop client from Google Cloud).
6. **Zoom** (`/obsagent zoom`, preset `zoom-720p30`, `knowledge/zoom.md`). Not tried. Zoom publishes no bitrate figure, so the 3000 kbps setting is our own choice.
7. **Housekeeping:**
   - First git commit.
   - Delete `.profiles-backup-*` once the setup is stable.
   - `src_ref/` (OBS source copies) is gitignored.

## Known problems already fixed (do not regress)

- **Profile switches over websocket are slow and asynchronous.** `profile apply` now waits for each switch and verifies it before writing, then reads every value back. Once, OBS ignored SetCurrentProfile entirely; the fix was to quit OBS and edit the files directly. The CLI writes files only while OBS is closed.
- **OBS rewrites FPSType to 2** (FPSNum/FPSDen) by itself. `already_current()` treats this as equal when the live fps matches, so `plan apply` skips the profile step and does not switch profiles.
- **Never call `GetInputPropertiesListPropertyItems` on a `screen_capture` input.** It crashes OBS 33 (SIGSEGV in strlen inside obs-websocket). `obsagent_lib/displays.py` gets the display UUID from CoreGraphics instead.
- **A screen source created without `display_uuid` captures nothing.** The log line is "Invalid target display ID: 0".
- **Use CoreAudio_AAC, not ffmpeg_aac,** for the audio encoder.
- All of these are written up in `knowledge/troubleshooting.md`.

## Rules the agent follows

- Start or stop a stream only when the user asks in that message, and pass `--yes` only then.
- Run a dry run before every `profile apply` or `plan apply`.
- Never print stream keys, tokens or passwords.
- Before writing to live OBS, check for an open OBS dialog. A blocked UI causes switch timeouts.
- User preference: no em or en dashes anywhere.

## How to resume

```
cd ~/CODE/obs-agent
obsagent doctor            # OBS running, websocket reachable, no fails
obsagent ws status         # profile obsagent-youtube-1080p60, 1920x1080 @ 60
```

Then in Claude Code, use `/obsagent live` once the user has started the test stream.
