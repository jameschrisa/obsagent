# obsagent handoff

Last session: 2026-11-01, ended about 21:50 PT. Read this first, then SPEC.md if you need the design.

## Where we are

OBS is set up for a YouTube talking-head show with remote guests. Guest video through VDO.Ninja works, invite emails send from the host's own address, and the new wallpaper is live. A second agent, **pod producer**, gives the host private, playful live cues.

**Next steps:**
1. The user installs BlackHole (`brew install --cask blackhole-2ch`, needs their password). Then run `podproducer check` and set up the OBS monitoring steps it lists.
2. Turn off **Browser Source Hardware Acceleration** for good. The OBS log at 21:29 still showed it on. Do it from OBS > Settings > Advanced, or edit `global.ini` while OBS is closed. Confirm with `obsagent preflight plans/talking-head.yaml`.
3. **YouTube test streaming is on hold** at the user's request. Don't push it.

## What exists

- **Repo** `~/CODE/obs-agent`, public at https://github.com/jameschrisa/obsagent (branch `main`).
- **CLI `obsagent`** (`obsagent_lib/`, symlinked at `~/.local/bin/obsagent`). `--json` works on every command.
  - `doctor [--net] [--plan P]`: checks the machine, plus a show plan's background, guest roster, emails, Keychain password and mail sender. It also flags the BrowserHWAccel setting.
  - `profile`, `preset`, `stream` (named YouTube keys: `youtube` default, `youtube:alt`), `plan validate|apply`.
  - `ws status|monitor|scene|snapshot|refresh|scenes|inputs|start/stop-stream|start/stop-record`. `monitor` takes `--guests PLAN` (flags a guest tile that goes blank) and `--log`.
  - `guests add|remove|list|links|rotate|guide|refresh|invite|set-email|set-sender|senders`.
  - `preflight PLAN [--record N]`: one pass/warn/fail report. It never streams.
  - `triage collect`: gathers OBS log errors, stats, settings and crash report names, with secrets masked.
  - `show postshow PLAN [--rotate]`.
- **CLI `podproducer`** (`podproducer_lib/`, symlinked at `~/.local/bin/podproducer`).
  - `prep SHOW`: writes a `producer:` brief into the plan.
  - `check`: confirms devices, model, whisper and the claude CLI.
  - `run SHOW`: live capture, transcription, cues, and a local dock page.
  - `recap`, `sessions`.
  - The design and user guide are in `docs/pod-producer.md`.
- **Claude Code entry points**, symlinked into `~/.claude`:
  - `/obsagent` skill and `obsagent` subagent: setup, plans, guests, prep, preflight, live, triage, postshow, Zoom.
  - `/pod-producer` skill and `pod-producer` subagent: prep, check, live, cues, recap.
- **Knowledge** `knowledge/`: OBS settings, YouTube, macOS, troubleshooting (every bug below), websocket, Zoom, guests, preflight checklist.
- **Docs** `docs/`:
  - `guest-guide.html`: the guest guide draft for the user to review, then convert to DOCX with pandoc. It covers email links, phone held sideways, newest link wins, and AI transcription consent.
  - `pod-producer.md`.
- **Tests:** 155 pass. Run them with `uv run --with pytest --with pyyaml --with obsws-python pytest -q`.

## Live state

- **OBS 33.0.1**, obs-websocket on port 4455 with auth. The password is in the Keychain (service `obsagent`, account `obs-websocket`).
- **Profile `obsagent-youtube-1080p60`** (active):
  - Advanced output mode.
  - Apple VideoToolbox H.264, 17000 kbps CBR, keyframe 2 s.
  - 1080p60.
  - CoreAudio AAC at 128k, 48 kHz.
  - The `Untitled` profile is the restored original.
- **Stream service:** YouTube RTMPS with key `youtube` (ends `awft`). `youtube:alt` (ends `8zpy`) is stored but not active. That alt key was pasted in chat, so a reset is recommended.
- **Scenes** (all built by `plan apply plans/talking-head.yaml`, every positioned item locked):
  - `Camera`: StreamCam full frame.
  - `Screen + camera`: main display with a 480x270 StreamCam inset at the bottom right.
  - `Panel`: host and guests. 1 guest is side by side, 2 are two on top and one below, 3 are a 2x2 grid.
  - `Guest: <name>`: a close-up per guest.
  - A shared `Background` image is at the bottom of each scene.
  - `Mic/Aux` is muted.
- **Background:** `~/Documents/obsagent/backgrounds/blue-peach-diagonal.jpg`, a Pexels photo cropped to 1920x1080. It is kept outside the repo for licensing. The in-repo fallback is `assets/backgrounds/slate-soft.png`.
- **Guests:**
  - Show `talking-head`, room `obsa1f85z6lxkx`, rotated 2026-11-01. Its password is in the Keychain (`vdo:talking-head`), and its state is in `~/.config/obsagent/shows/`.
  - The roster has only "Test Guest" (email jchrisa@me.com). Remove it before a real show.
- **Email:**
  - Invites go through Mail.app. The default sender is `James <james@ausvrn.com>` (Fastmail account added to Mail on 2026-11-01).
  - `james@signetscience.com` (Google Workspace) also works with `--from`.
  - Both were tested end to end. The Gmail connector in Claude is read-only, so it isn't used.
- **Pod producer:**
  - whisper.cpp model at `~/.local/share/podproducer/models/ggml-small.en.bin` (sha1 db8a495a...).
  - Session data is in `~/.local/state/podproducer/sessions/`.
  - Verified with synthetic voices: whisper transcribed them exactly, and a replay through `claude -p` (sonnet) returned 2 good cues in 14 s.
  - Not yet run with live audio, because BlackHole is not installed.
- **Machine:** M1 Pro, 16 GB, macOS 14.6.1, about 393 Mbps upload, about 45 GB free.

## Open items, in order

1. **BlackHole and OBS monitoring.** Install BlackHole, then set OBS > Settings > Audio > Advanced > Monitoring Device to BlackHole 2ch. Set each guest source to Monitor and Output, and leave the host mic off. Then do a live `podproducer run talking-head` with a guest on the phone, and open the dock in OBS (Docks > Custom Browser Docks, URL http://127.0.0.1:8765).
2. **Turn off BrowserHWAccel** (see above).
3. **Guest guide review** by the user, then DOCX conversion.
4. **Unverified live behaviour:**
   - The preflight host-mic meter. It worked once and read -47 dB.
   - `render_ratio` cannot tell a guest who hasn't joined from a blank tile.
   - Pod producer dock and recap on a real session.
   - A producer brief for the real show (`/pod-producer prep talking-head`).
5. **On hold or untested:**
   - YouTube test stream (on hold).
   - YouTube API commands (need `client_secret.json`).
   - Zoom.
   - Screen privacy (whole display versus window capture).
   - Claude Docs sync. "Claude Duet" was never identified.

## Known problems already fixed (do not regress)

- **Slow profile switches:** they are asynchronous, so apply waits for each switch, verifies it and reads every value back.
- **Screen capture crash:** never call `GetInputPropertiesListPropertyItems` on `screen_capture`, because it crashes OBS 33. The display UUID comes from CoreGraphics instead.
- **Blank screen source:** a screen source without `display_uuid` captures nothing.
- **Audio encoder:** use CoreAudio_AAC, not ffmpeg_aac.
- **FPSType:** OBS rewrites it to 2, and that counts as equal.
- **Blank guest tiles:** a browser source renders fully transparent while Browser Source Hardware Acceleration is on. The guest's audio still works, which makes it misleading.
- **Dragged tiles:** tiles were dragged in the preview, so plan apply now locks them.
- **Error 601 when replacing a source:** a removed source stays referenced. The fix renames it out of the way before removal.
- **Mail.app errors:** -1700 came from iterating references (fixed by looping by index), and -1743 means Automation permission is missing.
- **Narrow guest picture:** the phone was held upright. The guide now says to hold it sideways.

## Rules the agent follows

- Start or stop a stream, or send email, only when the user asks in that message. Say which YouTube key is active before any stream.
- Default to drafts for invites. Show the recipient, sender and subject, and get an explicit OK before `--send`.
- Run a dry run before `profile apply` or `plan apply`. Before writing to live OBS, check for an open OBS dialog.
- Never print stream keys, room passwords or tokens. Before every push to the public repo, scan for them.
- Guests must be told about AI transcription. The line is in the guide and in the invite email.
- Pod producer cues are playful, private to the host, short, and never pressure guests.
- User preference: no em or en dashes anywhere.

## How to resume

```
cd ~/CODE/obs-agent
obsagent preflight plans/talking-head.yaml   # one report on OBS, scenes, guests, mail, key
podproducer check                            # audio devices, model, whisper, claude
```
