# obsagent

CLI for setting up, configuring, testing and troubleshooting OBS Studio on this Mac, aimed at YouTube Live (Zoom webinar via custom RTMP later). Built to be driven by a Claude Code agent: every command takes `--json`.

## Install

```
git clone https://github.com/jameschrisa/obsagent.git && cd obsagent
ln -sf "$PWD/obsagent" ~/.local/bin/obsagent

# Claude Code: /obsagent skill and obsagent subagent
ln -sfn "$PWD/claude/skills/obsagent" ~/.claude/skills/obsagent
ln -sf "$PWD/claude/agents/obsagent.md" ~/.claude/agents/obsagent.md
```

Requires macOS, OBS Studio 33+, and [uv](https://docs.astral.sh/uv/).

`obsagent` is a uv script (inline deps: obsws-python, pyyaml, pillow). The symlink is resolved, so it runs from anywhere. `youtube` commands re-run themselves with the Google client libraries.

## Commands

| Command | What it does |
|---|---|
| `doctor [--net] [--json]` | Read-only probe: OBS install/version, running, config dir, websocket config and reachability, hardware, cameras, audio, permission hints, disk, Keychain, upload estimate (`--net`, about 20s). Each check is ok/warn/fail/info with a fix hint. |
| `ws status`, `ws monitor --seconds N`, `ws scenes`, `ws inputs` | Live state and a sampled health summary that flags render lag, encode overload, network drops. |
| `ws start-stream`, `stop-stream`, `start-record`, `stop-record` | Require `--yes`. |
| `ws set-password` | Store the websocket password in Keychain (`obsagent` / `obs-websocket`). Env `OBS_WS_PASSWORD`, `OBS_WS_HOST`, `OBS_WS_PORT` also work. |
| `preset list`, `preset show NAME` | Encoding presets in `presets/*.yaml` (CBR, 2s keyframes, AAC 48 kHz stereo, VideoToolbox by default). |
| `profile apply PRESET [--profile NAME] [--dry-run]` | Creates/switches the OBS profile, sets Advanced output params, writes `streamEncoder.json`, reloads the profile. Uses the websocket when OBS runs, otherwise edits files (only if OBS is not running). Default profile name: `obsagent-PRESET`. |
| `stream set-key --target youtube` | Prompt for a key and store it in Keychain. |
| `stream set-service --target youtube [--rtmps] [--key-from keychain\|env\|prompt] [--server URL]` | Sets a custom RTMP(S) service via the websocket. Keys are masked in output. |
| `plan new\|show\|validate\|apply\|export NAME` | Show plans in `plans/*.yaml`. `apply [--dry-run]` = profile + service + scenes/inputs. `export` prints markdown. |
| `youtube auth`, `youtube broadcast create`, `youtube stream bind`, `youtube status` | Optional YouTube Data API v3. Prints setup steps if `~/.config/obsagent/client_secret.json` is missing. |

### Show lifecycle

| Command | What it does |
|---|---|
| `ws scene NAME` | Switch the program scene and read it back to verify (exit 1 if it did not take). |
| `ws snapshot [SOURCE_OR_SCENE] [--out PATH] [--width 1280]` | Save a PNG (default: current program scene, into `~/Documents/obsagent/snapshots/`) and print the path and visible-pixel ratio. |
| `ws refresh SOURCE`, `guests refresh SHOW [--name N]` | Press the browser source refresh button on a source, or on the show's guest sources. |
| `doctor --plan PLAN` | Adds show checks: plan validates, background exists, roster and emails, room password in Keychain, mail sender in Mail.app, no "Test Guest". `doctor` always warns when `BrowserHWAccel=true`. |
| `preflight PLAN [--record SECONDS] [--json]` | Pass/warn/fail rows for the whole setup (profile, scenes, Background, guests, mics, not streaming, disk, YouTube rtmps key name). `--record` runs a test recording and reports lagged/skipped frames and the file. Never starts a stream. Exit 1 on any fail. |
| `ws monitor --guests PLAN [--log [PATH]]` | Per-guest rows each sample (flags a connected guest that goes blank for 2 samples); `--log` appends JSON lines (default `~/.local/state/obsagent/sessions/<date>.jsonl`). |
| `triage collect [--json]` | One masked evidence report: log matches, stats, status, output settings, BrowserHWAccel, crashes from the last 24h. |
| `show postshow PLAN [--rotate]` | Summarize the latest session log; `--rotate` also issues a new guest room and password. |

Exit codes: 0 ok, 1 failure or validation error, 2 missing `--yes`, 3 websocket unreachable.

## First-time OBS setup

1. Launch OBS once so `~/Library/Application Support/obs-studio` exists.
2. OBS > Tools > WebSocket Server Settings > enable, set a password; then `obsagent ws set-password`.
3. Grant Screen Recording, Camera and Microphone to OBS in System Settings > Privacy & Security.
4. `obsagent doctor`, then `obsagent profile apply youtube-1080p30`.

## Tests

```
uv run --with pytest --with pyyaml --with obsws-python pytest -q
```

The suite mocks the websocket client and uses temp dirs; no OBS needed.

## Notes

- `src_ref/` holds copies of the OBS source files used to confirm key and id names (VideoToolbox encoder properties, AdvancedOutput ini keys). Source paths are cited in `obsagent_lib/profiles.py` and `obsagent_lib/plans.py`.
- OBS 33.0.1 registers the camera source as `macos-avcapture` (not `av_capture_input_v2`); plans pick whichever kind `GetInputKindList` reports.
- Camera, mic and display devices are chosen by hand in OBS the first time (device ids are machine specific).
