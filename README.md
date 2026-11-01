# obsagent

CLI for setting up, configuring, testing and troubleshooting OBS Studio on this Mac, aimed at YouTube Live (Zoom webinar via custom RTMP later). Built to be driven by a Claude Code agent: every command takes `--json`.

## Install

```
ln -sf /Users/jchrisa/CODE/obs-agent/obsagent ~/.local/bin/obsagent
```

`obsagent` is a uv script (inline deps: obsws-python, pyyaml). The symlink is resolved, so it runs from anywhere. `youtube` commands re-run themselves with the Google client libraries.

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
