# Troubleshooting decision tree

Start with `obsagent ws status --json` and `obsagent ws monitor --seconds 60 --json`, then the log.

## Where logs live and what to grep

- Folder: `~/Library/Application Support/obs-studio/logs/` (files named like `2026-10-31 14-02-11.txt`). Crash reports: `~/Library/Application Support/obs-studio/crashes/`. From OBS: Help > Log Files > Show Log Files / Upload Current Log File / Upload Last Log File (after a crash). Paste the URL in the analyzer at https://obsproject.com/tools/analyzer. Config dir does not exist until OBS has launched once.
- Latest log: `ls -t ~/Library/Application\ Support/obs-studio/logs | head -1`.
- Grep patterns (string literals marked * were confirmed in source; others are the standard OBS log wording, UNVERIFIED in this session):

| Pattern | Meaning |
|---|---|
| `Output '.*': Reconnecting in` * | Stream dropped, OBS retrying (Output Reconnect setting). |
| `Output '.*': stopping` * | An output is stopping. |
| `Max audio buffering reached!` * | Audio thread overloaded or a source stalls; expect desync or audio glitches. |
| `Failed to create output` * | Output could not be created: bad encoder id or settings. |
| `Video stopped, number of skipped frames due to encoding lag` | Final encoder-skip count at stream stop. |
| `Number of lagged frames due to rendering lag` | Render lag count at stop. |
| `Number of dropped frames due to insufficient bandwidth/connection stalls` | Network drop count at stop. |
| `RTMP_Connect`, `Connection to rtmp`, `Failed to connect`, `Disconnected` | Connection stage failures. |
| `Error: Rtmp ... SSL` | RTMPS TLS issue; use port 443. |
| `videotoolbox`, `VideoToolbox`, `Failed to` | Encoder creation problems. |
| `ScreenCaptureKit`, `Screen Capture`, `permission` | Capture source problems. |
| `CoreAudio`, `coreaudio` | Audio device problems. |

Useful one-liners:
```
L=~/Library/Application\ Support/obs-studio/logs; f="$L/$(ls -t "$L" | head -1)"
grep -nEi 'skipped|lagged|dropped|Reconnect|Failed|Error|Max audio buffering' "$f" | tail -50
```

UI strings (confirmed from OBS en-US): "Failed to connect", "Failed to connect to server", "Invalid Path or Connection URL...", "Could not access the specified channel or stream key, please double-check your stream key...", "Disconnected from server.", "Encoding error: An encoder error occurred while streaming.", "Encoding overloaded! Consider turning down video settings or using a faster encoding preset." Stats panel labels: "Skipped frames due to encoding lag", "Frames missed due to rendering lag", "Dropped Frames (Network)".

Three counters that must be read separately (OBS KB and stats):
- Encoding lag (`outputSkippedFrames` in GetStats): the encoder could not finish a frame in its time slot. Cause: encoder/CPU/GPU capacity. "Encoding overloaded" shows at more than 0.1% skipped (forum summary).
- Rendering lag (`renderSkippedFrames`): OBS could not composite frames in time. Cause: GPU load, heavy scene.
- Network drops (GetStreamStatus `outputSkippedFrames`): frames the output could not send. Cause: network. KB: "It is extremely unlikely for OBS Studio to cause dropped frames."

Sources: https://obsproject.com/tools/analyzer ; https://obsproject.com/kb/stream-connection-troubleshooting ; https://obsproject.com/kb/encoding-performance-troubleshooting ; https://github.com/obsproject/obs-studio/blob/master/frontend/data/locale/en-US.ini

## Triage by symptom

### 1. Can't connect / "Failed to connect to server"
| Cause | Check | Fix |
|---|---|---|
| Wrong key or URL | Dialog text "Could not access the specified channel or stream key". `obsagent ws status` service settings. | Re-set the key (`obsagent stream set-service`). In YouTube Studio verify the key and that live streaming is enabled on the channel (can take up to 24 h after first enabling: UNVERIFIED). |
| RTMPS/SSL error | Log `SSL` near connect | Use `rtmps://a.rtmps.youtube.com:443/live2` with port 443; or fall back to `rtmp://a.rtmp.youtube.com/live2`. |
| Firewall, VPN, filtering | Does a browser reach `a.rtmps.youtube.com:443`? `nc -vz a.rtmps.youtube.com 443` | Disable VPN or security software to test (OBS KB). |
| Stale service type | `GetStreamServiceSettings` | Re-apply `rtmp_common` "YouTube - RTMPS". |
| Bound stream already live elsewhere | Studio shows stream in use | Stop the other encoder; 3 active streams per key max. |

### 2. Dropped frames (network)
Check: Stats "Dropped Frames (Network)" or GetStreamStatus delta `outputSkippedFrames / outputTotalFrames`, `outputCongestion`. Flag above 1% per SPEC.
Causes and fixes in order (OBS KB):
1. Use wired Ethernet instead of Wi-Fi.
2. Set bitrate to about 75% of stable upload (run `obsagent doctor --net`).
3. Try the backup ingest (`b.rtmps.youtube.com`).
4. Settings > Advanced > Network: Dynamic Bitrate on (it lowers bitrate instead of dropping frames; masks root cause); Bind to IP Default; IP Family IPv4 only as a test.
5. Disable VPN, test another network, restart modem and router, contact ISP.
Note: on macOS the "Enable network optimizations" and "TCP pacing" options are Windows only.

### 3. Encoding overloaded / skipped frames due to encoding lag
Check: GetStats `outputSkippedFrames / outputTotalFrames`, `cpuUsage`, `averageFrameRenderTime`.
Causes: x264 preset too slow, 4K or 60 fps on M1 Pro, other GPU/CPU heavy apps, thermal throttling, Low Power Mode.
Fixes: switch to VideoToolbox H.264; lower output resolution first, then 60 to 30 fps (OBS KB order); close GPU-heavy apps; plug in power; turn off Low Power Mode; with x264 use a faster preset. Check `powermetrics` thermal pressure.

### 4. Rendering lag
Check: GetStats `renderSkippedFrames`, `averageFrameRenderTime` above frame time (33.3 ms at 30 fps, 16.7 at 60).
Causes: too many or large sources, full-canvas browser sources, filters on scenes, display capture of a 4K or 5K Retina display, downscaling a big base canvas.
Fixes (OBS KB): lower output resolution then fps; simplify scenes (fewer browser sources, lower-resolution media, remove filters, use image sources instead of static browser sources); reduce base canvas as a last resort; close other GPU apps.

### 5. Black screen capture
| Cause | Check | Fix |
|---|---|---|
| Screen Recording permission missing or stale | System Settings > Privacy & Security > Screen & System Audio Recording; OBS toggle | Enable, quit and relaunch OBS; if toggle is on but still black, `tccutil reset ScreenCapture com.obsproject.obs-studio`, relaunch, re-grant. |
| Legacy Display/Window Capture used | Source id `display_capture` / `window_capture` | Replace with macOS Screen Capture (`screen_capture`). |
| Window minimized, on another Space, or fullscreen app | Source Method = Window | Use Display or Application method; enable "Show fullscreen and hidden windows". |
| Protected (DRM) content | Netflix-style players | Cannot be captured. |
| Camera black | Camera permission, another app holding the camera (Zoom, FaceTime) | Enable Camera permission; quit other app; re-select device in `macos-avcapture`. |
| Virtual camera black in Zoom | Camera extension not approved | macos.md section 6. |

### 6. No audio (or YouTube "no audio stream")
- Mixer meters flat: check Settings > Audio devices, Microphone permission, correct device in `coreaudio_input_capture`.
- Desktop audio missing: macOS has no native loopback; add `sck_audio_capture` (macOS 13+, OBS 30+) or Screen Capture audio; see macos.md section 3.
- Meters move but YouTube says "Please provide one audio stream": the audio source is not on the track sent to the stream. Check Advanced > Streaming > Audio Track (`[AdvOut] TrackIndex`) and that sources are enabled for that track in Advanced Audio Properties; mixer not muted; check source is not muted by hotkey.
- Monitoring set to Monitor Off or Monitor and Output wrongly causes echo or silence on monitor only.

### 7. Audio desync
- Cause: USB camera video latency (StreamCam) versus mic, or audio buffering. Log `Max audio buffering reached!` means overload.
- Fix: Advanced Audio Properties > Sync Offset on the mic (positive delay audio) in 20-50 ms steps; add the delay to the audio, not video. Try the `macos-avcapture-fast` path for lower camera latency. Use the same sample rate in OBS (48 kHz) and macOS Audio MIDI Setup for the device. Avoid Bluetooth mics (high latency).
- Confirm with a clap test recording before the show.
- OBS KB "audio video sync issues" page returned 404; the fixes above are standard OBS practice, UNVERIFIED against that page.

### 8. Choppy stream (viewers buffering, stutter)
- If network drops are 0 and encode lag is 0, the viewer side is buffering. Check YouTube health (youtube.md section 7): `bitrateLow`, `videoIngestionStarved`, `gopSizeLong`.
- Keyframe interval must be 2 s; bitrate should be the recommended CBR value; check upload headroom; avoid ultra-low latency if viewers are on poor connections (normal latency buffers better).
- Local rendering stutter on screen capture: lower fps, `DisableOSXVSync` is rarely needed.

### 9. YouTube "not receiving data" / stream status inactive / `noData`
1. Is OBS actually streaming? `ws status` outputActive true, `outputBytes` increasing.
2. Key matches the Studio stream (the stream bound to this broadcast)? Reused or reset key? With API: stream `status.streamStatus` must be `active` before transition.
3. Wait 10-30 seconds after start; the preview/health panel lags (UNVERIFIED typical delay).
4. Wrong ingest: using HLS URL in RTMP field or the reverse; trailing spaces in key.
5. Stream limit: 3 active streams per key; "Daily live stream limit achieved".
6. Transition 403 `errorStreamInactive`: start OBS first and wait for active.

### 10. Bad bitrate / YouTube health warnings
Map the message via youtube.md section 7. Quick rules: `bitrateLow` (raise to recommended CBR), `audioBitrateHigh/Low` (128), `gopSize*` (keyint 2 s), `noAudioStream` (track), `videoIngestionStarved` (network). Actual sent bitrate vs configured: compute from `outputBytes` deltas in `ws monitor`; a value well below target means encoder underfed (static scene with ABR) or network limited; use CBR.

### 11. OBS crash or startup
Logs in `crashes/`; start OBS in Safe Mode; check macOS version compatibility; rename the profile folder to test. Virtual camera extension issues: reinstall OBS.

Sources:
- https://obsproject.com/kb/stream-connection-troubleshooting
- https://obsproject.com/kb/encoding-performance-troubleshooting
- https://obsproject.com/kb/macos-permissions-guide/
- https://obsproject.com/kb/macos-desktop-audio-capture-guide
- https://support.google.com/youtube/answer/3006768
- https://github.com/obsproject/obs-websocket/blob/master/src/requesthandler/RequestHandler_Stream.cpp

## WebSocket profile switch hangs (OBS accepts SetCurrentProfile but never switches)

- Symptom: `obsagent profile apply` or `profile set` aborts with "OBS did not switch to profile ... within 30s". The OBS log shows the client connecting but no video settings reset or profile change.
- Likely cause: the OBS main window is blocked, for example by an open dialog or the Settings window.
- Fix: close any OBS dialog and retry. If none is open, quit OBS (Cmd+Q) and use file mode, which edits basic.ini directly and is safe only while OBS is closed. Back up `~/Library/Application Support/obs-studio/basic/profiles` first. Found 2026-10-31 on OBS 33.0.1 / obs-websocket 5.7.6.

## Screen capture shows nothing ("Invalid target display ID: 0")

- Symptom: the macOS Screen Capture source is transparent or black, its source size is 0x0, and the OBS log shows `[ mac-screencapture ]: init_screen_stream: Invalid target display ID:  0`.
- Cause: the source was created without `display_uuid`. This is not a permission issue. The log's startup lines confirm permissions (`[macOS] Permission for screen capture granted.`).
- Fix: set `display_uuid` to the display's CoreGraphics UUID. `obsagent plan apply` does this automatically for `display: main`, or you can pick the display in the source properties in OBS.
- Do NOT call `GetInputPropertiesListPropertyItems` on a `screen_capture` input. On OBS 33.0.1 / obs-websocket 5.7.6 it crashes OBS (EXC_BAD_ACCESS in strlen inside obs-websocket). Confirmed 2026-10-31 from the crash report.

## OBS crash on quit in obs_transition_clear

- A crash report whose faulting thread is `obs_graphics_thread` > `obs_source_destroy` > `obs_transition_clear` happened while OBS was shutting down. It is an OBS bug in cleanup, not a streaming problem. If it happens mid-session, attach the crash report from `~/Library/Logs/DiagnosticReports/OBS-*.ips` to an OBS GitHub issue.

## Guest tile is blank but the guest's audio meters move

- Symptom: a `Guest N: NAME` browser source shows nothing (fully transparent), yet its audio meter moves. `obsagent ws snapshot "Guest 1: NAME"` reports about 0% visible pixels.
- Cause: Browser Source Hardware Acceleration is on (`BrowserHWAccel=true` under [General] in global.ini). On OBS 33.0.1 on an M1 Pro the page renders transparent until it is off.
- Fix: OBS > Settings > Advanced > turn off Browser Source Hardware Acceleration, then restart OBS. Then `obsagent guests refresh SHOW`.
- Evidence: `obsagent doctor` warns `browser_hwaccel`; `obsagent ws snapshot SOURCE` prints the visible-pixel ratio; `ws monitor --guests PLAN` flags a connected guest that goes blank for 2 samples. Found 2026-11-01.

## Tiles moved or resized

- Symptom: guest or camera tiles sit in the wrong place or have the wrong size.
- Cause: someone dragged them in the preview.
- Fix: `obsagent plan apply PLAN` restores positions and locks the items so they cannot be dragged again.
- Evidence: `obsagent plan apply PLAN --dry-run` lists the transform requests; `preflight PLAN` reports missing scenes or Background.

## CreateInput fails with error 601 (name already exists)

- Symptom: `plan apply` fails creating an input; obs-websocket code 601, the name already exists.
- Cause: OBS keeps a removed source alive while a scene still references it, so the name stays taken.
- Fix: rename the old source, remove it from every scene, then remove it (plan apply does this for the Background input). If you removed a source by hand, run `plan apply` again.
- Evidence: the 601 error text from the request; `obsagent ws inputs` still lists the name.

## Mail.app errors -1743 and -1700 during guests invite

- Symptom: `guests invite` fails with `-1743` or `-1700`.
- Cause: -1743 means macOS blocked this terminal from controlling Mail (Automation permission). -1700 is a coercion error from AppleScript, usually a sender or recipient value Mail cannot use.
- Fix: for -1743, System Settings > Privacy & Security > Automation, enable Mail for your terminal. For -1700, check the sender with `obsagent guests senders` and the guest email with `guests list`.
- Evidence: the osascript error text; `obsagent doctor --plan PLAN` shows `mail_sender`.

## Guest picture is narrow

- Symptom: the guest appears as a thin vertical strip.
- Cause: the guest joined from a phone held in portrait.
- Fix: ask the guest to hold the phone sideways (landscape) and rejoin; then `obsagent guests refresh SHOW`.
- Evidence: `obsagent ws snapshot "Guest 1: NAME"` shows the narrow frame.

## Collect evidence in one step

`obsagent triage collect` gathers the latest log matches (secrets masked), stats, stream and record status, profile, scene, output settings, BrowserHWAccel and crash reports from the last 24 hours.

