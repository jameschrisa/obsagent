# OBS settings that matter for streaming (OBS 33, macOS, Apple Silicon)

Config locations on macOS: `~/Library/Application Support/obs-studio/`. Per-profile file `basic/profiles/<ProfileName>/basic.ini`, with `streamEncoder.json` and `recordEncoder.json` in the same profile folder. App-level `global.ini` holds General app settings. Scene collections are `basic/scenes/*.json`. Logs in `logs/`.

Never edit these files while OBS runs; OBS rewrites them on exit. Prefer obs-websocket (`SetProfileParameter`, see websocket.md). `SetProfileParameter` only accepts string values (null removes the key) and calls config_save immediately.

## 1. Output mode

basic.ini `[Output]`:
- `Mode` = `Simple` or `Advanced`. Use Advanced for agent-managed profiles: it exposes rate control, keyframe interval, B-frames, track selection, and reads encoder options from `streamEncoder.json`.

Simple mode keys (`[SimpleOutput]`): `StreamEncoder`, `StreamAudioEncoder`, `VBitrate`, `ABitrate`, `UseAdvanced`, `x264Settings`, `Preset`, `RecQuality`, `RecEncoder`, `RecAudioEncoder`, `RecFormat2`, `RecTracks`, `FilePath`. Simple `StreamEncoder` maps to encoder ids: Apple H.264 = `com.apple.videotoolbox.videoencoder.ave.avc`, Apple HEVC = `com.apple.videotoolbox.videoencoder.ave.hevc`, x264 = `obs_x264`; an unrecognized value falls back to `obs_x264`.

## 2. Advanced output, Streaming tab (`[AdvOut]`)

| Setting | basic.ini key | Notes |
|---|---|---|
| Audio track used for stream | `TrackIndex` (1-6) | Use track 1. |
| Video encoder | `Encoder` | Encoder id string, e.g. `com.apple.videotoolbox.videoencoder.ave.avc`. |
| Audio encoder | `AudioEncoder` | AAC id; YouTube accepts AAC or MP3. |
| Rescale output | `RescaleRes` (e.g. `1920x1080`), `RescaleFilter` | Prefer setting output res in Video instead. |
| Apply service settings | `ApplyServiceSettings` | When on, OBS clamps bitrate and keyint to the service's recommended values. |
| Track bitrates / names | `Track1Bitrate` .. `Track6Bitrate`, `Track1Name` .. `Track6Name` | Audio kbps per track. |
| VOD track | `VodTrackEnabled`, `VodTrackIndex` | Only for services that support a separate VOD track. |

Encoder properties live in `streamEncoder.json` in the profile folder, loaded by `GetDataFromJsonFile` (falls back to empty settings if missing). The frontend reads at least `rate_control`, `bitrate`, `keyint_sec` from it. Format is plain obs_data JSON, flat keys, for example:

```json
{"rate_control": "CBR", "bitrate": 14000, "keyint_sec": 2, "profile": "high", "bframes": true}
```

OBS only reloads the JSON when the profile loads, so after writing it switch profile away and back (as SPEC `profile apply` does).

## 3. Encoders on macOS

VideoToolbox plugin settings (from `plugins/mac-videotoolbox/encoder.c`):

| Key | Type / range | Default | Notes |
|---|---|---|---|
| `rate_control` | `CBR`, `ABR`, `CRF` | ABR (CBR if macOS 13+, hardware encoder, Apple Silicon) | CBR needs macOS 13+ and Apple Silicon. CRF and CBR show only for hardware encoders on Apple Silicon. |
| `bitrate` | 50-10,000,000 kbps, step 50 | 6000 | Used by CBR and ABR. Changing it while live re-applies to the running session. |
| `quality` | 0-100 | 60 | CRF only. |
| `limit_bitrate`, `max_bitrate`, `max_bitrate_window` | bool, kbps, 0.10-10.0 s | false, 6000, 1.5 | Hidden for CBR. |
| `keyint_sec` | 0-20 s | 2 | H.264 and HEVC. YouTube wants 2 s, never over 4 s. |
| `profile` | H.264: baseline, main, high. HEVC: main, main10, main42210 | high / main | Use `high` for H.264 SDR. Use `main` for HEVC SDR, `main10` for HDR. |
| `bframes` | bool | true | Maps to AllowFrameReordering. YouTube lists 2 B-frames; VT exposes only on/off. |
| `spatial_aq_mode` | Auto, Disabled, Enabled | Auto | macOS 15+ only. Not available on macOS 14. |

Pixel formats: I420 and NV12 for H.264; P010 (10-bit) HEVC only. RealTime is set off by the plugin.

Registered encoders: H.264 (hardware and software VT variants, shown as HW/SW), HEVC (only if built with HEVC; HW/AVE/SW), ProRes (recording only; it ignores bitrate and keyint).

Choice guidance:
- Default: VideoToolbox H.264 hardware, CBR, keyint 2 s, profile high. Offloads work from the CPU; the M1 Pro has a dedicated media engine.
- x264 (`obs_x264`) is the fallback: better quality per bit at the same bitrate on slow presets but loads the CPU. Use a fast preset (veryfast) with CBR, keyint 2, B-frames 2 if the VT encoder misbehaves.
- HEVC or AV1 to YouTube uses enhanced RTMP. OBS services.json lists `h264, hevc, av1` for YouTube RTMPS. On M1 Pro only HEVC has a hardware encoder; AV1 would be software. HEVC needs recent OBS (29.1 added it; 30.1 added HDR over e-RTMP).
- Recommended bitrates are in youtube.md.

## 4. Video (`[Video]`)

| Setting | Key | Values |
|---|---|---|
| Base (canvas) resolution | `BaseCX`, `BaseCY` | Match the main display or camera, e.g. 1920 x 1080. |
| Output (scaled) resolution | `OutputCX`, `OutputCY` | Stream resolution. A zero `OutputCY` causes "Invalid parameters" errors at start (forum report; fix the ini). |
| Downscale filter | `ScaleType` | `bilinear`, `area`, `bicubic`, `lanczos`. Use `bicubic` (sharp, moderate cost) or `lanczos` (sharpest, heavier). Only matters when base != output. |
| FPS type | `FPSType` | 0 = Common, 1 = Integer, 2 = Fractional (loader also accepts the strings Common, Integer, Fraction). |
| Common FPS | `FPSCommon` | `24`, `25`, `29.97`, `30`, `48`, `59.94`, `60`. |
| Integer / fractional FPS | `FPSInt`, `FPSNum`, `FPSDen` | |
| Color | `ColorFormat` (`NV12`, `I420`, `I444`, `P010`...), `ColorSpace` (`709`, `601`, `sRGB`, `2100PQ`...), `ColorRange` (`Partial`, `Full`) | YouTube SDR wants Rec. 709, 8-bit. Use NV12, 709, Partial. |
| Renderer | `Renderer` | macOS uses Metal. |
| Auto remux | `AutoRemux` | Remux finished recordings to MP4 when recording to MKV. |

Via websocket: `GetVideoSettings` / `SetVideoSettings` use `baseWidth, baseHeight, outputWidth, outputHeight, fpsNumerator, fpsDenominator` (fields must be set in pairs). Use these for a live change instead of editing the ini.

Rule: output res = what you will ship (1080p). Keep base = output when the source is native; downscaling costs GPU.

## 5. Audio (`[Audio]`)

- `SampleRate` = 44100 or 48000. YouTube accepts 44.1 or 48 kHz (API `audioSampleRate` issue fires for others). Use 48000 (OBS default and matches video work).
- `ChannelSetup` = `Mono`, `Stereo`, `2.1`, `4.0`, `4.1`, `5.1`, `7.1`. YouTube allows mono or stereo only for non-AAC-5.1 ingest; use `Stereo`.
- `LowLatencyAudioBuffering` (bool) reduces audio buffering at the cost of risk of crackle.
- `DisableAudioDucking` (macOS ducks other audio when a mic is active in some setups).
- Audio bitrate per track: `[AdvOut] Track1Bitrate` or `[SimpleOutput] ABitrate`. YouTube recommends 128 kbps stereo AAC (384 for 5.1).
- Tracks: send track 1 to the stream. Record other tracks separately only for post-production.

## 6. Advanced tab

- Process priority: `global.ini [General] ProcessPriority` = `High`, `AboveNormal`, `Normal`, `BelowNormal`, `Idle`. It is stored in the app config, not basic.ini. Leave Normal on macOS; the setting is a weak lever on Apple Silicon.
- Network (basic.ini `[Output]`): `Reconnect` (bool), `RetryDelay` (s), `MaxRetries`, `BindIP` (`default` or an IP), `IPFamily` (`IPv4+IPv6` / `IPv4`), `DynamicBitrate` (bool; drops bitrate instead of frames when congested; OBS KB says use only if the connection cannot be fixed). `NewSocketLoopEnable` and `LowLatencyEnable` ("TCP pacing") are Windows only, ignore on macOS.
- Stream delay: `DelayEnable`, `DelaySec`, `DelayPreserve`.
- OBS KB advice: set video bitrate to about 75% of stable upload speed; keep Bind IP at Default; try IPv4 only if flaky.
- Other: `[Video] DisableOSXVSync`, `ResetOSXVSyncOnExit` (macOS VSync); `[General] BrowserHWAccel` (browser source hardware acceleration); `[General] OpenStatsOnStartup`.
- Enhanced Broadcasting / multitrack video (`[Stream1] EnableMultitrackVideo`) is a Twitch-style feature; leave off for YouTube.
- `[Stream1] IgnoreRecommended` = true disables "Enforce streaming service encoder settings".

## 7. Recording formats

Values for `RecFormat2` (in `[SimpleOutput]` and `[AdvOut]`): `mkv`, `mp4`, `mov`, `hybrid_mp4`, `fragmented_mp4`, `fragmented_mov`.
- MKV: crash-safe; auto-remux to MP4 (`[Video] AutoRemux`) after stop. Safe default for a live show.
- Hybrid MP4 (`hybrid_mp4`): MP4 that is crash-resilient like MKV and needs no remux (OBS 30.2+ feature; listed in source above). Prefer it for editing workflows.
- Plain `mp4` / `mov`: lose the whole file if OBS crashes. Avoid during a show.
- Advanced recording: `RecType` (`Standard` or `FFmpeg`), `RecEncoder` (`none` reuses the stream encoder), `RecTracks` (bitmask), `RecFilePath`, split file keys `RecSplitFile`, `RecSplitFileType`, `RecSplitFileTime`, `RecSplitFileSize`.
- `[Output] FilenameFormatting` (default `%CCYY-%MM-%DD %hh-%mm-%ss`), `OverwriteIfExists`.
- Recording with the same encoder as the stream avoids a second encode session: set `RecEncoder` to `none`.

## 8. Service settings (not in basic.ini)

Stream service lives in `service.json` in the profile folder, with `type` (`rtmp_common` or `rtmp_custom`) and `settings`. Prefer `SetStreamServiceSettings` (websocket.md).

Sources:
- https://github.com/obsproject/obs-studio/blob/master/plugins/mac-videotoolbox/encoder.c
- https://github.com/obsproject/obs-studio/blob/master/frontend/settings/OBSBasicSettings.cpp
- https://github.com/obsproject/obs-studio/blob/master/frontend/utility/AdvancedOutput.cpp
- https://github.com/obsproject/obs-studio/blob/master/frontend/utility/SimpleOutput.cpp
- https://github.com/obsproject/obs-studio/blob/master/plugins/rtmp-services/data/services.json
- https://obsproject.com/kb/stream-connection-troubleshooting
- https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md

UNVERIFIED: the numeric meanings of `FPSType` (0/1/2) and the `ColorSpace`/`ColorFormat` value lists come from general OBS knowledge, not from a source file read here. Valid `RecType` values and the hybrid MP4 introduction version are likewise from memory. The `DisableAudioDucking` description is an interpretation of the key name.
