# obsagent spec

Goal: a Claude Code agent (`/obsagent` skill + `obsagent` subagent) that sets up, configures, tests, and troubleshoots OBS Studio on this Mac for YouTube Live, with Zoom webinar (custom RTMP) as a later target. Shows can be pre-planned in a plan doc and applied later.

Machine: MacBook Pro M1 Pro, 16 GB, macOS 14 (Darwin 23.6). OBS 33.0.1 at /Applications/OBS.app (never launched yet, so no config dir). Cameras: Logitech StreamCam, FaceTime HD. obs-websocket plugin is bundled. Tools on PATH: uv, python3 3.14, node, ffmpeg.

## Layout

```
obs-agent/
  obsagent              # executable uv script entry (#!/usr/bin/env -S uv run --script), deps inline
  obsagent_lib/         # python package: doctor.py, ws.py, profiles.py, plans.py, youtube.py, paths.py
  presets/              # YAML encoding presets
  plans/                # show plans (YAML), example.yaml
  knowledge/            # markdown references the agent reads (OBS, YouTube, macOS, troubleshooting, Zoom)
  tests/                # pytest, no OBS required (mock ws)
  README.md
```

`obsagent` must run from anywhere (symlink at ~/.local/bin/obsagent). Every command supports `--json` for machine-readable output that the Claude agent parses. No secrets printed: stream keys and ws passwords are masked (last 4 chars).

## Commands

- `doctor` : read-only probe. OBS installed + version, running?, config dir present, profiles/scene collections, websocket reachable + auth, hardware (chip, RAM, macOS), cameras and audio devices (system_profiler), permissions hints (Screen Recording, Camera, Mic: report what can be detected, else give the System Settings path), network upload estimate (`networkQuality -s -c`, opt-in via `--net` since it takes ~20s), disk free for recordings. Returns a list of checks with status ok/warn/fail and a fix hint.
- `ws status` : streaming/recording state, current profile, scene, video settings, stats (CPU, FPS, render/encode skipped frames, dropped frames, kbps when live).
- `ws monitor --seconds N` : sample GetStats + GetStreamStatus every 2s, print a summary and flag problems (render lag, encode overload, network drops >1%).
- `ws scenes`, `ws inputs`, `ws start-stream`, `ws stop-stream`, `ws start-record`, `ws stop-record`. Start and stop must require `--yes`.
- `preset list` / `preset show NAME`.
- `profile apply PRESET [--profile NAME] [--dry-run]` : create the profile if missing (CreateProfile), switch to it, set ini params with SetProfileParameter (Output Mode=Advanced, AdvOut encoder/track/audio bitrate, Video base/output res + FPS, Audio sample rate 48000 stereo), write streamEncoder.json into the profile dir, then reload the profile (switch away and back) so OBS picks up the encoder JSON. Dry-run prints the diff without changing anything. If OBS isn't running, it can write the profile files directly, but only after checking OBS is not running.
- `stream set-service --target youtube [--rtmps] [--key-from keychain|env|prompt]` : SetStreamServiceSettings. Keys are stored in macOS Keychain (`security add-generic-password -s obsagent -a youtube`), never in repo files.
- `plan new NAME`, `plan show NAME`, `plan validate NAME`, `plan apply NAME [--dry-run]` : a show plan (YAML) contains title, date/time, target (youtube|zoom|custom), preset, scenes wanted, sources (camera, mic, screen, browser/overlay), checklist, YouTube broadcast metadata (title, description, privacy, latency mode). Apply = profile apply + set-service + checks for the scenes and inputs (creates simple ones: video capture device, audio input, macOS screen capture). `plan export NAME --md` produces markdown suitable for a planning doc.
- `youtube auth|broadcast create|stream bind|status` : optional, uses YouTube Data API v3 liveBroadcasts/liveStreams with OAuth installed-app flow; client secret at ~/.config/obsagent/client_secret.json, token in Keychain or ~/.config/obsagent/token.json (chmod 600). If it isn't configured, say how to set it up and exit cleanly.

## websocket

obs-websocket v5 on port 4455. Use the `obsws-python` package. Password from Keychain (`-s obsagent -a obs-websocket`) or env OBS_WS_PASSWORD. `doctor` explains how to turn it on: OBS > Tools > WebSocket Server Settings > Enable, then store the password with `obsagent ws set-password`. OBS 33 keeps the websocket config in the app's user.ini/global.ini, under the `[OBSWebSocket]` section, not in plugin_config. Read it only for diagnostics.

## Presets (YAML, all CBR, keyframe 2s, AAC 48 kHz stereo)

Use Apple VideoToolbox H.264 (`com.apple.videotoolbox.videoencoder.ave.avc`) by default on Apple Silicon. x264 is the fallback. Offer an HEVC preset for YouTube via enhanced RTMP (`com.apple.videotoolbox.videoencoder.ave.hevc`).
- youtube-720p30: 1280x720 30fps, 4000 kbps, audio 128k
- youtube-1080p30: 1920x1080 30fps, 10000 kbps, audio 160k
- youtube-1080p60: 1920x1080 60fps, 12000 kbps, audio 160k
- youtube-1440p30, youtube-1440p60, youtube-4k30 (with M1 Pro warnings)
- zoom-720p30 : custom RTMP, 1280x720 30fps, 3000 kbps (future)
Each preset records the source URL for its numbers. Bitrates must be checked against the current YouTube encoder settings page by the knowledge task. Use the values in knowledge/youtube.md if they differ.

## Quality bar

- pytest suite passes, with ws mocked.
- `./obsagent doctor --json` runs on this machine with OBS not running and returns valid JSON.
- No em-dashes or en-dashes anywhere (use hyphens).
