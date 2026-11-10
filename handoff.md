# obsagent handoff

Last session: 2026-11-02, morning PT. Read this first, then SPEC.md if you need the design.

## Where we are

OBS is set up for a YouTube talking-head show with up to 4 remote guests. Guest video through VDO.Ninja works, invite emails send from the host's own address, and the wallpaper is live. A second agent, **pod producer**, gives the host private, playful live cues. BlackHole is installed and routed, and the producer has run live once (host audio only).

**Next steps:**
1. **Holistiplan test stream (no longer on hold).** OBS is pointed at the Holistiplan channel: key name `youtube`, label "Holistiplan", ends `awft`, RTMPS. Preflight was 16 pass, 2 warn, 0 fail. The user was about to set an Unlisted stream in YouTube Studio (Create > Go live > Stream, key ending awft) and say **go**. Before starting, restate the key (last 4) and wait for the go in that message. Monitor with `obsagent ws monitor --guests talking-head --log --seconds 120 --json`.
2. **Reset the Holistiplan key afterwards.** The full key was pasted into chat on 2026-11-02. Reset it in YouTube Studio, then the user stores the new one in a regular Terminal: `obsagent stream set-key --target youtube --label "Holistiplan"`.
3. **Pod producer with a real guest.** The guest track (BlackHole) has never carried audio yet, because no guest joined during the live run. Do one run with the phone as guest, headphones on, dock open.
4. **Pod producer needs an auto-stop.** See open items. Never leave `podproducer run` going unattended.

## What exists

- **Repo** `~/CODE/obs-agent`, public at https://github.com/jameschrisa/obsagent (branch `main`, last code commit `2042be5`).
- **CLI `obsagent`** (`obsagent_lib/`, symlinked at `~/.local/bin/obsagent`). `--json` works on every command.
  - `doctor [--net] [--plan P]`, `profile`, `preset`, `stream` (named YouTube keys), `plan validate|apply`.
  - `ws status|monitor|scene|snapshot|refresh|scenes|inputs|start/stop-stream|start/stop-record`. `monitor` takes `--guests PLAN` and `--log`.
  - `guests add|remove|list|links|rotate|guide|refresh|invite|set-email|set-sender|senders`. `remove` takes the name as a positional: `obsagent guests remove talking-head "Test Guest"`.
  - `preflight PLAN [--record N]` (plan name, e.g. `talking-head`). It never streams.
  - `triage collect`, `show postshow PLAN [--rotate]`.
- **CLI `podproducer`** (`podproducer_lib/`, symlinked at `~/.local/bin/podproducer`): `prep SHOW`, `check`, `run SHOW`, `recap`, `sessions`. Design in `docs/pod-producer.md`.
- **Claude Code entry points**, symlinked into `~/.claude`: `/obsagent` skill and `obsagent` subagent; `/pod-producer` skill and `pod-producer` subagent.
- **Knowledge** `knowledge/`: OBS settings, YouTube, macOS, troubleshooting, websocket, Zoom, guests, preflight checklist.
- **Docs** `docs/`, also copied to `~/Downloads` as HTML and DOCX (DOCX made with macOS `textutil`; pandoc is not installed):
  - `guest-guide.html` (Downloads: `Guest-User-Guide.*`). Its `<mark>[UPPER CASE]</mark>` placeholders must match `PLACEHOLDERS` in `obsagent_lib/guests.py`, or personal guides stop filling in.
  - `host-live-guide.html` (Downloads: `Host-Live-Show-Guide.*`): running a live show with obsagent and the pod producer.
  - `pod-producer.md`.
- **Tests:** 161 pass. Run them with `uv run --with pytest --with pyyaml --with obsws-python pytest -q`.

## Live state

- **OBS 33.0.1**, open at handoff on the `Panel` scene, not streaming. obs-websocket on port 4455 with auth; password in the Keychain (service `obsagent`, account `obs-websocket`).
- **Profile `obsagent-youtube-1080p60`** (active). Its folder is `basic/profiles/obsagentyoutube1080p60` (no hyphens; `user.ini` `[Basic] ProfileDir`). Advanced output, Apple VideoToolbox H.264, 17000 kbps CBR, keyframe 2 s, 1080p60, CoreAudio AAC 128k at 48 kHz. Monitoring device: BlackHole 2ch (set in the OBS UI). `Untitled` is the restored original.
- **Stream service:** YouTube RTMPS, key `youtube` (label Holistiplan, ends `awft`). `youtube:alt` (label "second YouTube account", ends `8zpy`) is stored but not active; it was also pasted in chat, so a reset is recommended.
- **Scenes** (built by `obsagent plan apply talking-head`, positioned items locked):
  - `Camera`: StreamCam full frame.
  - `Screen + camera`: main display with a 480x270 StreamCam inset, bottom right.
  - `Panel`: host plus guests. 2 people side by side, 3 two on top and one below, 4 a 2x2 grid, 5 three 640x360 tiles on top and two centered below.
  - `Guest: <name>`: a close-up per guest.
  - Shared `Background` image at the bottom of each scene. `Mic/Aux` muted.
- **Guests:** up to 4 per show (`MAX_GUESTS = 4` in `guests.py` and `plans.py`). Show `talking-head`, room password in the Keychain (`vdo:talking-head`), state in `~/.config/obsagent/shows/`. Roster: only "Test Guest" (jchrisa@me.com). Remove before a real show. Guest sources are set to Monitor and Output by plan apply; the host mic is not monitored.
- **Background:** `~/Documents/obsagent/backgrounds/blue-peach-diagonal.jpg` (Pexels, outside the repo). Fallback in repo: `assets/backgrounds/slate-soft.png`.
- **Email:** Mail.app, default sender `James <james@ausvrn.com>` (Fastmail), `james@signetscience.com` with `--from`. Both tested end to end.
- **Audio:** BlackHole 2ch 0.7.1 installed from the signed pkg (brew's sudo step fails without a terminal) and loaded after `sudo killall coreaudiod`. ffmpeg device indexes at handoff: 0 BlackHole 2ch, 3 Logitech StreamCam.
- **Pod producer:** whisper model `~/.local/share/podproducer/models/ggml-small.en.bin`. Live run 2026-11-02 from 09:06 until stopped at handoff: session `~/.local/state/podproducer/sessions/20261102-090652/` with 786 transcript lines (all Host) and 376 cues. Capture, transcription, cue generation and the dock (HTTP 200 on port 8765) all worked. Cues were on topic and playful. That session holds private room conversation that was not a show; ask the user before keeping or deleting it.
- **Machine:** M1 Pro, 16 GB, macOS 14.6.1 (Homebrew Tier 3), about 393 Mbps upload. The StreamCam dropped off USB once on 2026-11-02; replugging fixed it.

## Chrome panel (added 2026-11-09)

- Helper `obsagent serve` (obsagent_lib/bridge.py) on 127.0.0.1:8767 (8766 is taken by another app). Installed at login: `~/Library/LaunchAgents/com.obsagent.bridge.plist`, log `~/Library/Logs/obsagent-bridge.log`. Token in `~/.config/obsagent/bridge-token` (0600); `obsagent serve token` prints it in Terminal.
- Extension in `chrome-extension/` (MV3 side panel), loaded unpacked. Guide: `docs/chrome-extension.md`.
- Verified: health, status, 401 without token, a real chat job through `claude -p` (read-only question answered, session id returned), and a chat request to start a stream was refused. Not verified: the panel UI loaded in Chrome, Go Live through the panel, producer start/stop through the panel with real ffmpeg.
- Pod producer now stops on `--max-minutes` (default 120) and `--idle-minutes` (default 15).

## Open items, in order

1. **Holistiplan unlisted test stream** (Next steps 1), then the key reset.
2. **Producer auto-stop.** The live run kept transcribing for hours after the test. Add an idle timeout (stop after N minutes with no guest audio, or a `--max-minutes` limit) and have `run` print how to stop it. Decide with the user what to do with the 2026-11-02 session.
3. **Producer guest track** with a real guest on the phone; confirm guest lines show up as a separate speaker.
4. **Unverified live behaviour:** preflight host-mic meter (reads silent if nobody talks during the 3 s sample), `render_ratio` cannot tell an absent guest from a blank tile, producer recap on a real session, a producer brief for the real show (`/pod-producer prep talking-head`).
5. **User review** of both guides in Downloads; fill the guest guide placeholders via the plan (`show_time`, `tech_check`, `host_contact`, `youtube.watch_url`).
6. **On hold or untested:** YouTube API commands (need `client_secret.json`), Zoom, screen privacy (whole display versus window capture), Claude Docs sync ("Claude Duet" was never identified).

## Known problems already fixed (do not regress)

- **Slow profile switches:** apply waits for each switch, verifies it and reads every value back.
- **Screen capture crash:** never call `GetInputPropertiesListPropertyItems` on `screen_capture` (crashes OBS 33). The display UUID comes from CoreGraphics.
- **Blank screen source:** a screen source without `display_uuid` captures nothing.
- **Audio encoder:** CoreAudio_AAC, not ffmpeg_aac. **FPSType:** OBS rewrites it to 2; treat as equal.
- **Blank guest tiles:** Browser Source Hardware Acceleration must be off (confirmed off 2026-11-02).
- **Dragged tiles:** plan apply locks them. **Error 601 replacing a source:** rename it out of the way before removal.
- **Mail.app errors:** -1700 (loop by index), -1743 (Automation permission).
- **Narrow guest picture:** phone held upright; the guide says sideways.
- **Producer check read the wrong profile:** it took the first profile folder (Untitled). It now reads `ProfileDir` from `user.ini`.
- **Guide placeholders:** the regenerated guide used different placeholder text and broke personal guides. The filler now also replaces `<mark>` placeholders; keep the guide's tokens in sync with `guests.PLACEHOLDERS`.
- **StreamCam missing:** plan apply fails on the mic match when the camera is unplugged. Replug, then re-apply.
- **OBS settings written while OBS is closed can be lost** if the folder is wrong. Set the monitoring device in the OBS UI.

## Rules the agent follows

- Start or stop a stream, or send email, only when the user asks in that message. Say which YouTube key is active (last 4) before any stream.
- Default to drafts for invites. Show recipient, sender and subject, and get an explicit OK before `--send`.
- Dry run before `profile apply` or `plan apply`. Check for an open OBS dialog before writing to live OBS.
- Never print stream keys, room passwords or tokens. Scan for them before every push to the public repo.
- Guests are told about AI transcription (guide and invite email).
- Pod producer cues are playful, private, short, and never pressure guests. Stop the producer as soon as a test ends.
- User preference: no em or en dashes anywhere.

## How to resume

```
cd ~/CODE/obs-agent
obsagent ws status                  # is OBS open and on the right profile and scene
obsagent stream list-keys           # confirm Holistiplan (awft) is the key in use
obsagent preflight talking-head     # one report on OBS, scenes, guests, mail, key
podproducer check                   # audio devices, model, whisper, claude, monitoring
```
