# Preflight checklist

Use with `obsagent doctor`, `obsagent plan apply NAME --dry-run` and `obsagent preflight NAME`. Items marked (cmd) have a command; (human) needs a person.

## T-24h
- [ ] (cmd) `obsagent doctor --json --net`: OBS 33.x installed, config dir exists (launch OBS once if not), disk free for recording (rule of thumb: 1080p at 14 Mbps is about 6.3 GB per hour; use MKV or hybrid MP4).
- [ ] (human) Permissions granted: Screen Recording, Camera, Microphone, Accessibility (macos.md). Relaunch OBS after granting.
- [ ] (human) Camera extension approved if the virtual camera is used (Zoom route).
- [ ] (cmd) Websocket enabled (Tools > WebSocket Server Settings), password stored via `obsagent ws set-password`; `obsagent ws status` works.
- [ ] (cmd) Profile applied from preset: `obsagent profile apply youtube-1080p30 --dry-run`, then apply. Verify encoder, CBR, keyframe 2 s, bitrate from youtube.md, AAC 48 kHz stereo.
- [ ] (human) YouTube: channel verified and live enabled; broadcast created (Studio or `obsagent youtube broadcast create`) with title, description, privacy, latency, DVR on, made-for-kids answered; stream key persistent (reusable) and stored in Keychain.
- [ ] (cmd) Network: `networkQuality -s -c` upload at least 1.5x target bitrate (14 Mbps video + 0.128 audio means 21+ Mbps up); wired Ethernet planned.
- [ ] (human) Scenes and sources built: camera, mic, screen capture, overlays; hotkeys; copy backup of scene collection.
- [ ] Update plan doc with scene list and run order.
- [ ] (cmd) `obsagent doctor --plan NAME`: plan valid, background file, guest roster and emails, room password in Keychain, mail sender, no "Test Guest", Browser Source Hardware Acceleration off.

## T-1h
- [ ] Power connected; Low Power Mode off; Do Not Disturb/Focus on; notifications off; close unneeded apps.
- [ ] Quit and relaunch OBS (clean memory); no pending updates prompts.
- [ ] (cmd) `obsagent preflight NAME` (add `--record 30` for a test clip): every row pass or an understood warn; it never starts a stream. Then `obsagent ws snapshot SCENE` per scene to eyeball it.
- [ ] (cmd) `obsagent ws status`: profile, scene, video 1920x1080 at 30/60, encoder VT H.264.
- [ ] (cmd) `obsagent stream set-service --target youtube --rtmps --key-from keychain`.
- [ ] Mic check: levels peak around -12 to -6 dBFS in the mixer, never in the red; headphones on; clap test for sync (record a short clip, review).
- [ ] Desktop audio source present and metering (sck_audio_capture).
- [ ] Test recording 30 seconds to confirm disk path and audio tracks.
- [ ] Open YouTube Studio Live Control Room on a second device or browser.

## T-10min
- [ ] (cmd) `obsagent guests refresh NAME` once guests have joined, then `obsagent ws snapshot` the panel scene.
- [ ] (cmd) `obsagent ws monitor --seconds 30`: render lag, encode lag at 0%, CPU headroom.
- [ ] Start streaming only if the Control Room is ready: if using the API flow, bind the broadcast to the stream and make sure it is `ready` (youtube.md section 4B).
- [ ] (cmd) Start OBS stream (`obsagent ws start-stream --yes`) 5-10 minutes early; watch Control Room until stream status shows receiving data and health is good.
- [ ] If the broadcast has a monitor stream/testing, transition to `testing` and confirm preview; keep privacy at Unlisted/Private until live.
- [ ] Resolve any warnings using youtube.md section 7 (bitrate, keyframe, audio).
- [ ] Check preview sound on the YouTube side with a second device on headphones (avoid echo).
- [ ] Put a "starting soon" scene live; chat open.

## Live
- [ ] Click Go live in Studio (or transition to `live`). Confirm public watch page shows video.
- [ ] (cmd) `obsagent ws monitor --guests NAME --log` (flags a guest tile that goes blank; appends to the session log). Check every few minutes: dropped network frames under 1%, no rendering lag, no encoding lag; CPU/temp stable.
- [ ] Watch Control Room health, chat, audio levels; mute mic when off-air.
- [ ] If dropped frames spike: enable Dynamic Bitrate or lower bitrate, switch to backup ingest only as a last resort (troubleshooting.md section 2).
- [ ] If the stream drops: OBS reconnects automatically (`Reconnect` setting); do not stop the broadcast in Studio unless it fails for over a minute (`enableAutoStop` ends it 1 minute after stream stops if on).

## Post-show
- [ ] End the broadcast (Studio End stream or transition `complete`), then stop OBS (`obsagent ws stop-stream --yes`). Stop in that order if autoStop is off to avoid cutting viewers.
- [ ] Stop recording; confirm file exists and plays; remux MKV to MP4 if needed (`[Video] AutoRemux` or File > Remux Recordings).
- [ ] (cmd) `obsagent show postshow NAME [--rotate]`: session summary; `--rotate` kills old guest links.
- [ ] (cmd) Save final `ws status` and log (or `obsagent triage collect` if something went wrong): copy the latest log from `~/Library/Application Support/obs-studio/logs`; optionally analyze at https://obsproject.com/tools/analyzer.
- [ ] YouTube: VOD processing, edit privacy, thumbnail, chapters, review Analytics > Live stream health report (UNVERIFIED path).
- [ ] Back up scene collection and profile; note issues in the plan doc.
- [ ] Reset any one-off change (Low Power Mode, Focus).

Sources: numeric thresholds follow youtube.md and troubleshooting.md. Disk-usage figure is arithmetic: 14 Mbps x 3600 s / 8 = 6.3 GB. Process steps are agent guidance; the YouTube transitions and `enableAutoStop` behavior come from https://developers.google.com/youtube/v3/live/docs/liveBroadcasts and https://developers.google.com/youtube/v3/live/docs/liveBroadcasts/transition
