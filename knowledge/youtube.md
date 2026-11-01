# YouTube Live for obsagent

Checked 2026-10-31 against the YouTube Help encoder settings page and the Live Streaming API reference.

## 1. Encoder settings and recommended bitrates

Table from YouTube's "Choose live encoder settings, bitrates, and resolutions" page. Values are Mbps on the page; kbps column is what to put in OBS (Mbps x 1000).

| Resolution / fps | H.264 min | H.264 recommended | HEVC and AV1 min | HEVC and AV1 recommended |
|---|---|---|---|---|
| 2160p (4K) 60 | 14 Mbps (14000) | 50 Mbps (50000) | 10 (10000) | 35 (35000) |
| 2160p (4K) 30 | 11 (11000) | 42 (42000) | 8 (8000) | 30 (30000) |
| 1440p 60 | 8 (8000) | 34 (34000) | 6 (6000) | 24 (24000) |
| 1440p 30 | 7 (7000) | 21 (21000) | 5 (5000) | 15 (15000) |
| 1080p 60 | 6 (6000) | 17 (17000) | 4 (4000) | 12 (12000) |
| 1080p 30 | 5 (5000) | 14 (14000) | 4 (4000) | 10 (10000) |
| 720p 60 | 3 (3000) | 8 (8000) | 2 (2000) | 6 (6000) |
| 720p 30 | 3 (3000) | 8 (8000) | 2 (2000) | 6 (6000) |
| 480p 30 | 0.4 (400) | 4 (4000) | 0.3 (300) | 3 (3000) |
| 360p 30 | 0.4 (400) | 4 (4000) | 0.3 (300) | 3 (3000) |

Other specs on the same page:
- Protocols: RTMP and RTMPS (RTMPS recommended); HLS if the encoder cannot do HDR over RTMP(S).
- Video codecs: H.264, H.265 (HEVC), AV1. HDR is H.265 only; AV1 is not supported for HDR.
- Max 60 fps. Progressive scan, square pixels, CABAC, closed GOP.
- Keyframe interval: 2 s recommended, do not exceed 4 s.
- Rate control CBR. B-frames 2. Reference frames 1.
- Color: Rec. 709, 8-bit for SDR; 10-bit for HDR. AV1 at 3840x2160 and above needs at least 2 tile columns.
- Audio: AAC or MP3. Stereo 128 kbps at 44.1 kHz; 5.1 at 384 kbps and 48 kHz (5.1 only with AAC over RTMP/RTMPS). The API accepts 44.1 or 48 kHz stereo and flags other rates.
- Resolution is auto-detected unless you use a custom stream key with manual settings enabled.
- Low latency is unavailable at 4K; 4K runs at normal latency.
- Not specified on the page: H.264 profile and level, HDR bitrate table.
- OBS's own services.json for YouTube caps at max video bitrate 51000 kbps, max audio bitrate 160 kbps, keyint 2, and lists codecs h264, hevc, av1. With "Enforce streaming service encoder settings" on, OBS clamps to those, so 160 kbps audio would pass but YouTube's audio recommendation is 128.

M1 Pro limits (obsagent judgment, not YouTube): 4K60 H.264 at 50 Mbps stresses the VT encoder and uplink; warn on every 1440p and 4K preset and require an upload speed of at least 1.5x the bitrate (OBS KB suggests bitrate at about 75% of stable upload).

Source: https://support.google.com/youtube/answer/2853702 ; https://github.com/obsproject/obs-studio/blob/master/plugins/rtmp-services/data/services.json ; https://obsproject.com/kb/stream-connection-troubleshooting

## 2. Corrections to SPEC presets

SPEC.md presets are below YouTube's current recommended H.264 values. Use the youtube.md numbers (SPEC itself says to).

| Preset | SPEC says | YouTube H.264 recommended | YouTube H.264 minimum | Verdict |
|---|---|---|---|---|
| youtube-720p30 | 4000 kbps | 8000 | 3000 | SPEC is above the minimum but half the recommendation. Use 8000. |
| youtube-1080p30 | 10000 | 14000 | 5000 | SPEC 10000 equals the HEVC/AV1 recommendation, not H.264. Use 14000 for H.264. |
| youtube-1080p60 | 12000 | 17000 | 6000 | SPEC 12000 equals the HEVC/AV1 recommendation. Use 17000 for H.264. |
| youtube-1440p30 | not given | 21000 | 7000 | |
| youtube-1440p60 | not given | 34000 | 8000 | |
| youtube-4k30 | not given | 42000 | 11000 | |
| youtube-4k60 | not given | 50000 | 14000 | |
| HEVC 1080p30 / 1080p60 | not given | 10000 / 12000 | 4000 / 4000 | The SPEC H.264 numbers happen to match these. |
| audio 160k | 160 | 128 | n/a | YouTube recommends 128 kbps AAC stereo; 160 is not flagged as an error by the numbers on the pages read, but audioBitrateHigh exists in the API, so use 128. |
| audio 48 kHz | 48000 | 44.1 (help text) | accepts 44.1 or 48 (API) | 48 kHz is acceptable. The legacy error text says "correct the sample rate ... to 44.1KHz" but only fires for rates outside 44.1/48. |
| zoom-720p30 3000 kbps | 3000 | n/a | n/a | Not a YouTube number. Zoom's pages read do not state a bitrate (see zoom.md). |
| Keyframe 2 s, CBR | yes | yes | | Correct. |

Lower bitrates are not errors. The API flags `bitrateLow` as a "Video output low" issue when below the recommended value, which can appear as a warning in the Control Room health panel. Falling to the minimum is valid when upload is limited. With bandwidth short, lower the resolution rather than starving the bitrate.

SPEC also says the OBS-websocket config sits in `[OBSWebSocket]` in global.ini. Current obs-websocket source migrates and removes those global.ini values and stores config in `config.json` (see websocket.md). Source: https://github.com/obsproject/obs-websocket/blob/master/src/Config.cpp

## 3. Ingest URLs

Official YouTube pages read do not print the URLs; they say to copy them from Live Control Room (Stream settings; click the lock icon to switch to the RTMPS URL). OBS's rtmp-services list (maintained with YouTube) shows:

| Role | URL |
|---|---|
| RTMPS primary | `rtmps://a.rtmps.youtube.com:443/live2` |
| RTMPS backup | `rtmps://b.rtmps.youtube.com:443/live2?backup=1` |
| RTMP primary (legacy) | `rtmp://a.rtmp.youtube.com/live2` |
| RTMP backup | `rtmp://b.rtmp.youtube.com/live2?backup=1` |
| HLS primary | `https://a.upload.youtube.com/http_upload_hls?cid={stream_key}&copy=0&file=out.m3u8` |
| HLS backup | `https://b.upload.youtube.com/http_upload_hls?cid={stream_key}&copy=1&file=out.m3u8` |

- Port 443 for RTMPS; add it to the URL if you see an SSL error. If a timeout persists the encoder may lack RTMPS support.
- API: `cdn.ingestionInfo` has `streamName` (the key), `ingestionAddress` (RTMP/HLS/DASH primary), `backupIngestionAddress`, `rtmpsIngestionAddress`, `rtmpsBackupIngestionAddress`. Some tools take URL and key separately, others as `STREAM_URL/STREAM_NAME`.
- Protocol comparison: RTMP and RTMPS carry H.264 and support normal, low and ultra-low latency. HLS carries H.264 and H.265 and is "not suitable for ultra-low latency". DASH carries H.264 and VP9. HLS and DASH are segment-based so latency is higher than RTMP.
- Enhanced RTMP (HEVC and AV1 over RTMP) is supported by YouTube since April 2023 per OBS and Veovera coverage; the protocol comparison page lists only H.264 for RTMP, so the comparison table is out of date relative to the encoder settings page. OBS 29.1 added AV1/HEVC to YouTube, 30.1 added HDR HEVC over e-RTMP.

Sources: https://support.google.com/youtube/answer/10364924 ; https://developers.google.com/youtube/v3/live/guides/ingestion-protocol-comparison ; https://developers.google.com/youtube/v3/live/docs/liveStreams ; https://github.com/obsproject/obs-studio/blob/master/plugins/rtmp-services/data/services.json ; https://www.phoronix.com/news/OBS-Studio-AV1-HEVC-RTMP

## 4. Setup flows

### A. Stream key (no API)
1. YouTube Studio > Create > Go live > Stream tab. Pick or create a stream key.
2. Default key is tied to the channel's default stream. To reuse the same key across shows, "Create new stream key" (a custom, reusable key). "Reuse settings" on a past stream copies metadata, settings and key.
3. Put URL (or choose service YouTube - RTMPS in OBS) and key into OBS: `rtmp_common` with service "YouTube - RTMPS", or `rtmp_custom` with the URL (see websocket.md).
4. Start streaming in OBS; the Control Room preview and health panel go active.
5. Click Go live (unless auto-start is on). Reset a key: Studio > Create > Go live > Stream tab > Reset, requires channel owner or manager.
- Limits: 10 active streams per channel, 3 active streams per stream key.
- Daily live stream limit message exists ("Daily live stream limit achieved") if too many streams are created in 24 h.

### B. API-created stream (persistent key, scheduled broadcasts)
1. Insert a `liveStream` with `isReusable: true` for a persistent key. A non-reusable stream binds to one broadcast and may be auto-deleted afterwards; `liveStreams.list?mine=true` omits non-reusable streams.
2. Insert a `liveBroadcast`.
3. Bind broadcast to stream.
4. Start OBS with the stream's ingest URL and `streamName`.
5. Poll the stream until `status.streamStatus == active`.
6. If `monitorStream.enableMonitorStream` is true, transition to `testing`, then `live`. Otherwise transition `live` (or set `enableAutoStart`).
7. After the show, transition to `complete` (or rely on `enableAutoStop`, which ends about one minute after streaming stops).

## 5. API reference

Base: `https://www.googleapis.com/youtube/v3`. All write calls need user authorization from the account that owns the broadcasting channel.

OAuth scopes (insert, bind, transition each accept either): `https://www.googleapis.com/auth/youtube` or `https://www.googleapis.com/auth/youtube.force-ssl`. Request force-ssl for obsagent (it covers the same calls).

| Operation | Method and path | Notes |
|---|---|---|
| Create stream | `POST /liveStreams?part=id,snippet,cdn,contentDetails,status` | Required: `snippet.title` (1-128 chars), `cdn.frameRate` (`30fps`, `60fps`, `variable`), `cdn.ingestionType` (`rtmp` includes RTMPS, `hls`, `dash`), `cdn.resolution` (`240p`..`2160p`, `variable`; variable only with frameRate variable). Optional `contentDetails.isReusable`. |
| Create broadcast | `POST /liveBroadcasts?part=id,snippet,contentDetails,status` | Required: `snippet.title`, `snippet.scheduledStartTime`, `status.privacyStatus` (`public`, `unlisted`, `private`). Settable `contentDetails`: `enableAutoStart`, `enableAutoStop`, `enableClosedCaptions`, `enableDvr`, `enableEmbed`, `recordFromStart`, `monitorStream.enableMonitorStream`, `monitorStream.broadcastStreamDelayMs`. Also `status.selfDeclaredMadeForKids`. |
| Bind | `POST /liveBroadcasts/bind?id={broadcastId}&part=id,snippet,contentDetails,status&streamId={streamId}` | No body. Omit `streamId` to unbind. One broadcast to one stream. |
| Transition | `POST /liveBroadcasts/transition?broadcastStatus={testing\|live\|complete}&id={id}&part=id,status` | No body. Stream must be `active` first. `testing` needs `enableMonitorStream: true`. |
| List | `GET /liveBroadcasts`, `GET /liveStreams` | Needs read access; the exact readonly scope on list was not confirmed here. |
| Update | `PUT /liveBroadcasts`, `PUT /liveStreams` | |
| Delete | `DELETE /liveBroadcasts?id=` | |

Statuses:
- `lifeCycleStatus`: `created`, `ready`, `testStarting`, `testing`, `liveStarting`, `live`, `complete`, `revoked`. If stuck in `testStarting` or `liveStarting`, delete the broadcast, create a new one, bind and continue.
- `streamStatus`: `created` (no valid CDN settings), `ready`, `active` (receiving data), `inactive`, `error`.
- `healthStatus.status`: `good` (no issues at warning or worse), `ok` (no error-severity issues), `bad` (has error-severity issues), `noData` (YouTube has no health info). Issue severity: `info` (no effect), `warning` (viewers can watch, quality not optimal), `error` (viewers cannot watch). Key code on the issue `type`, not the message text.

Transition error reasons: `errorStreamInactive` (bound stream inactive, start OBS first), `invalidTransition`, `redundantTransition`, `insufficientLivePermissions`, `livePermissionBlocked`, `liveStreamingNotEnabled` (channel not enabled; verify the channel for live), `concurrentBroadcastsExceedLimit`, `sharedIngestionBroadcastsExceedLimit`, `userRequestsExceedRateLimit`.

Set DVR with `contentDetails.enableDvr` (default true; cannot change once testing or live). `recordFromStart` default true; false requires channel permission.

Quota costs: UNVERIFIED (not stated on the method pages).

Sources: https://developers.google.com/youtube/v3/live/docs/liveBroadcasts/insert ; .../liveBroadcasts/bind ; .../liveBroadcasts/transition ; .../liveStreams/insert ; https://developers.google.com/youtube/v3/live/docs/liveStreams ; https://developers.google.com/youtube/v3/live/docs/liveBroadcasts ; https://developers.google.com/youtube/v3/live/getting-started ; https://support.google.com/youtube/answer/9854503

## 6. Latency modes and DVR

- API `contentDetails.latencyPreference`: `normal`, `low`, `ultraLow`. `ultraLow` does not support closed captions or resolutions above 1080p. The older boolean `enableLowLatency` cannot select `ultraLow`.
- Latency is the delay from capture to viewer display; lower latency can increase buffering. If you do not interact with the audience live, latency matters less. 4K cannot use low latency.
- `latencyPreference` was not in the settable-field list on the broadcasts.insert page (it only appears in its error table, `invalidLatencyPreferenceOptions`), so confirm it can be set at insert or use update after insert. UNVERIFIED.
- Exact seconds of delay per mode: UNVERIFIED (not on the pages read).
- Rule: interactive Q&A or chat-driven shows use low; everything else normal; use ultraLow only for 1080p or lower with no captions.
- DVR (`enableDvr`) lets viewers pause and rewind live. Keep on. For playback right after the show ends it must be true together with archive.
- Closed captions: `closedCaptionsType` = `closedCaptionsDisabled`, `closedCaptionsHttpPost`, or `closedCaptionsEmbedded` (EIA-608 or CEA-708 in the stream). `enableClosedCaptions` is deprecated.

Source: https://developers.google.com/youtube/v3/live/docs/liveBroadcasts ; https://support.google.com/youtube/answer/2853702

## 7. Health warnings mapped to fixes

Control Room messages (official help text) and the API `configurationIssues` type that backs each. Errors in red are critical, yellow are moderate (YouTube help).

| Control Room message | API type | Cause | Fix in OBS |
|---|---|---|---|
| "We recommend you use a stream bitrate value of X" | `bitrateLow` / `bitrateHigh` | Video bitrate off from recommended for the detected resolution | Set CBR bitrate per section 1; if upload is short, lower resolution. |
| "We recommend you use an audio stream bitrate value of 128Kbps" | `audioBitrateLow` / `audioBitrateHigh` | Audio bitrate differs | Set track bitrate to 128. |
| "Please correct the sample rate for audio in the stream to 44.1KHz" | `audioSampleRate` | Rate not 44.1 or 48 kHz | Audio > Sample Rate 48 kHz (or 44.1). |
| "Please set the audio codec ... (AAC, MP3)" | `audioCodec` | Wrong audio codec | Use AAC. |
| "Please correct the number of audio channels" | `audioTooManyChannels` | More than 2 channels | Audio channels Stereo. |
| "Please provide one audio stream" | `noAudioStream` | No audio sent | Add an audio source, check track 1 is sent. |
| "Please provide only one audio stream" | `multipleAudioStreams` | Multiple tracks in the stream | Select one track in Advanced > Streaming. |
| "Please provide one video stream" | `noVideoStream` | No video | Check scene is not empty; encoder running. |
| "Please set the video codec for the stream to H.264" | `videoCodec` | Unsupported codec on that ingest | Use H.264, or HEVC/AV1 only via e-RTMP or HLS. |
| "Please correct the video codec profile" | n/a | Bad H.264 profile | Profile `high` (or `main`). |
| "The current video is interlaced..." | n/a | Interlaced | Progressive only. |
| "The current frame rate is too high" | `frameRateHigh` | FPS above limit | Use 30 or 60 fps. |
| "Please use a keyframe frequency of four seconds or less" | `gopSizeLong` / `gopSizeOver` | Keyframe interval too long | `keyint_sec` = 2 (60 frames at 30 fps). |
| "The GOP (group of pictures) size is small, which can reduce image quality" | `gopSizeShort` | Keyframes too frequent | `keyint_sec` = 2, not 0 or 1. |
| Closed GOP warning | `openGop` | Open GOP | Use a closed GOP (VT H.264 default is fine; x264 default is fine). |
| "Please check the video resolution" | `videoResolutionSuboptimal` | Resolution works but not ideal | Match a listed resolution (e.g. 1920x1080). |
| "You need to change the video resolution" | `videoResolutionUnsupported` | Unsupported | Change to resolution in the message. |
| "Please change the container format" | `badContainer` | Wrong container | Use RTMP(S) FLV, not a custom ffmpeg muxer. |
| "Video output low" / not enough video | `videoIngestionStarved` | YouTube not receiving enough data; viewers may buffer | Check OBS dropped frames, uplink, lower bitrate or enable Dynamic Bitrate. |
| "Daily live stream limit achieved" | n/a | Too many streams created in 24 h | Wait or request a higher limit. |
| Primary vs backup mismatch ("... same resolution", codec, bitrate, keyframe, etc.) | `*Mismatch` types | Backup ingest sends different settings | Only relevant when sending to both URLs; make them identical or send primary only. |

Status words: `noData` / stream status "inactive" = YouTube hears nothing (see troubleshooting.md "YouTube not receiving data"). The Control Room's "excellent/good/poor" labels could not be confirmed from a primary source; use the API `healthStatus.status` values (`good`, `ok`, `bad`, `noData`).

Sources: https://support.google.com/youtube/answer/3006768 ; https://developers.google.com/youtube/v3/live/docs/liveStreams/health_status_messages ; https://developers.google.com/youtube/v3/live/docs/liveStreams
