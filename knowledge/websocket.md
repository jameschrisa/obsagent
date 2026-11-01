# obs-websocket v5 essentials

obs-websocket is bundled with OBS 28+ (OBS 33 ships it at /Applications/OBS.app/Contents/PlugIns/obs-websocket.plugin).

## Enabling and config

- Default port 4455. Change or enable in OBS > Tools > WebSocket Server Settings. Authentication is strongly recommended; a password is generated automatically on first load.
- Command-line overrides: `--websocket_port`, `--websocket_password`, `--websocket_debug`, `--websocket_ipv4_only`.
- Safe Mode does not start the server (OBS 33 note).
- Config storage (CORRECTS SPEC): current source writes `config.json` in the module config directory, which on macOS is `~/Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json`. Keys: `first_load`, `server_enabled`, `server_port`, `alerts_enabled`, `auth_required`, `server_password`. The legacy `[OBSWebSocket]` section in global.ini (keys FirstLoad, ServerEnabled, ServerPort, AlertsEnabled, AuthRequired, ServerPassword) is migrated into config.json and removed on load. SPEC says global.ini; read config.json first and fall back to global.ini only for old installs. The exact macOS path is derived from the module config path convention, UNVERIFIED on disk because OBS has never been launched here.
- Password is stored in plain text in config.json, so treat the file as secret and mask output (last 4 chars).

## Protocol

- WebSocket subprotocol header `obswebsocket.json` (text frames, default) or `obswebsocket.msgpack`.
- Server sends Hello (op 0) with `obsStudioVersion`, `obsWebSocketVersion`, `rpcVersion`, optional `authentication` {`challenge`, `salt`}.
- Client sends Identify (op 1) with `rpcVersion` and, if auth is on, `authentication`. Server replies Identified (op 2) with `negotiatedRpcVersion`.
- Auth string: `base64( sha256( base64( sha256(password + salt) ) + challenge ) )`.
- Use `obsws-python` (non-asyncio, Python 3.10+) as SPEC says; it does this handshake.

## Requests this agent uses

| Request | Request fields | Response fields |
|---|---|---|
| GetVersion | none | OBS and plugin versions (fields not confirmed here) |
| GetStats | none | `cpuUsage`, `memoryUsage`, `availableDiskSpace`, `activeFps`, `averageFrameRenderTime`, `renderSkippedFrames`, `renderTotalFrames`, `outputSkippedFrames`, `outputTotalFrames`, `webSocketSessionIncomingMessages`, `webSocketSessionOutgoingMessages` |
| GetStreamStatus | none | `outputActive`, `outputReconnecting`, `outputTimecode`, `outputDuration`, `outputCongestion`, `outputBytes`, `outputSkippedFrames`, `outputTotalFrames` |
| StartStream / StopStream | none | none |
| StartRecord / StopRecord | none | StopRecord returns `outputPath` |
| GetProfileParameter | `parameterCategory`, `parameterName` | `parameterValue`, `defaultParameterValue` (strings) |
| SetProfileParameter | `parameterCategory`, `parameterName`, `parameterValue` (string; null removes the key) | none |
| CreateProfile / SetCurrentProfile | `profileName` | none |
| GetStreamServiceSettings | none | `streamServiceType`, `streamServiceSettings` |
| SetStreamServiceSettings | `streamServiceType`, `streamServiceSettings` | none |
| GetVideoSettings / SetVideoSettings | `baseWidth`, `baseHeight`, `outputWidth`, `outputHeight`, `fpsNumerator`, `fpsDenominator` (set in pairs) | same fields on Get |
| CreateScene | `sceneName` (required), `canvasUuid` | `sceneUuid` |
| CreateInput | `inputName`, `inputKind` (required); `sceneName` or `sceneUuid`, `inputSettings` (object), `sceneItemEnabled` (default true) | `inputUuid`, `sceneItemId` |
| GetInputKindList | `unversioned` (bool, default false) | `inputKinds` (string array) |
| GetInputList | `inputKind` (optional) | `inputs` |
| SetInputSettings | `inputName` or `inputUuid`, `inputSettings`, `overlay` (default true) | none |
| ToggleRecord | none | `outputActive` |

Field semantics (from source, not just names):
- GetStats `renderSkippedFrames` = frames skipped in the render thread (OBS UI: "Frames missed due to rendering lag"). `outputSkippedFrames` = frames skipped in the output/encode thread (UI: "Skipped frames due to encoding lag"). These are cumulative counters; compute percent as skipped / total, and compute deltas between samples.
- GetStreamStatus `outputSkippedFrames` is `obs_output_get_frames_dropped`, i.e. network dropped frames (UI: "Dropped Frames (Network)"), despite the name. `outputTotalFrames` is frames delivered by the stream output. `outputCongestion` is a float (NaN is returned as 0.0); higher means a congested connection. `outputBytes` is cumulative; kbps = delta bytes x 8 / delta seconds / 1000.
- OBS warns "Encoding overloaded!" when encoder skipped frames exceed 0.1% (OBS forum summary; threshold UNVERIFIED in source).

Behaviors confirmed in source:
- SetProfileParameter writes with config_set_string and immediately config_save on the current profile. Video and encoder values may not take effect until the profile reloads; use SetVideoSettings for live video changes.
- SetStreamServiceSettings errors with `OutputRunning` if streaming. If the type equals the current type, the new settings are merged over the old ones; otherwise a new service is created (invalid type gives `ResourceCreationFailed`).
- Request-level errors come back as `requestStatus` {`result`, `code`, `comment`}; check `result` before reading data.

## SetStreamServiceSettings payloads

rtmp_common (OBS knows the service list; server and key only):
```json
{"streamServiceType": "rtmp_common",
 "streamServiceSettings": {"service": "YouTube - RTMPS",
                           "server": "rtmps://a.rtmps.youtube.com:443/live2",
                           "key": "<stream key>"}}
```
Keys read by the service: `service`, `protocol` (RTMP or RTMPS, set from the service entry), `server`, `key`. The service name must match services.json ("YouTube - RTMPS", with alternates "YouTube / YouTube Gaming", "YouTube - RTMP", "YouTube - RTMPS (Beta)" accepted; HLS is "YouTube - HLS").

rtmp_custom (any RTMP server, used for Zoom):
```json
{"streamServiceType": "rtmp_custom",
 "streamServiceSettings": {"server": "rtmps://...", "key": "<key>",
                           "use_auth": false, "username": "", "password": ""}}
```
Exact keys from `rtmp-custom.c`: `server`, `key`, `use_auth` (bool), `username`, `password`.

Never log the key; mask the last 4 chars only.

## Events

- `StreamStateChanged` {`outputActive`, `outputState`}. outputState values: `OBS_WEBSOCKET_OUTPUT_STARTING`, `STARTED`, `STOPPING`, `STOPPED`, `RECONNECTING`, `RECONNECTED`, `PAUSED`, `RESUMED`.
- `RecordStateChanged` {`outputActive`, `outputState`, `outputPath` (file name when stopped, else null)}.
- Events need subscribing via the Identify `eventSubscriptions` bitmask; sampling GetStats every 2 s (as `ws monitor` does) needs no subscription.

## CreateInput kinds on macOS

Confirmed from OBS 33 plugin sources:

| Kind id | What it is | Notes |
|---|---|---|
| `macos-avcapture` | Video Capture Device (standard path) | Settings: `device` (string uid), `use_preset` (bool), `preset` (default `AVCaptureSessionPresetHigh`), `supported_format`, `enable_audio` (default true), `buffering`. |
| `macos-avcapture-fast` | Video Capture Device (fast path, IOSurface) | Settings: `device`, `supported_format`, `enable_audio`, `frame_rate`. |
| `screen_capture` | macOS Screen Capture (ScreenCaptureKit) | Method display, application or window. Needs macOS 12.3+; audio needs 13+. Exact settings keys not confirmed here (UI "Method" has Display, Application, Window). |
| `sck_audio_capture` | macOS Audio Capture (ScreenCaptureKit) | macOS 13+. All desktop audio or one app. |
| `coreaudio_input_capture` | Audio Input Capture (mic) | Settings key `device_id`. |
| `coreaudio_output_capture` | Audio Output Capture | Deprecated on macOS 13+ when SCK is available. |
| `display_capture`, `window_capture` | Legacy Display / Window Capture | Deprecated on macOS 13+. Do not use. |
| Others in the app bundle | `mac-syphon`, `mac-virtualcam`, `image-source`, `text-freetype2`, `browser_source`, `ffmpeg_source`, `vlc_source` | Typical ids; confirm with `GetInputKindList`. |

Always call `GetInputKindList` first and branch on what is returned rather than assuming ids. The `device_id` key for `coreaudio_input_capture` and the `device` value format (a uid string from the device list) are UNVERIFIED in the files read; fetch available device values through `GetInputPropertiesListPropertyItems` (inputName, propertyName `device`) after creating the input.

OBS 33.0.0 release notes (as summarized by the fetcher, treat as indicative): macOS Screen Capture transparency handling improved; Safe Mode no longer starts WebSocket; a check for missing encoders on profile load was added; CEF updated for browser sources.

Sources:
- https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md
- https://github.com/obsproject/obs-websocket/blob/master/README.md
- https://github.com/obsproject/obs-websocket/blob/master/src/Config.cpp
- https://github.com/obsproject/obs-websocket/blob/master/src/requesthandler/RequestHandler_Stream.cpp
- https://github.com/obsproject/obs-websocket/blob/master/src/requesthandler/RequestHandler_Config.cpp
- https://github.com/obsproject/obs-studio/blob/master/plugins/rtmp-services/rtmp-custom.c
- https://github.com/obsproject/obs-studio/blob/master/plugins/rtmp-services/rtmp-common.c
- https://github.com/obsproject/obs-studio/blob/master/plugins/mac-capture/ (ids in mac-sck-video-capture.m, mac-sck-audio-capture.m, mac-audio.c)
- https://github.com/obsproject/obs-studio/blob/master/plugins/mac-avcapture/plugin-main.m
- https://github.com/obsproject/obs-studio/releases/tag/33.0.0
