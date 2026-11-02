# Knowledge index

Read the file that matches the task. All numbers carry a source URL at the end of their section. Items marked UNVERIFIED could not be confirmed from a primary source on 2026-10-31.

| File | Read when |
|---|---|
| obs-settings.md | Choosing or changing an encoder, rate control, resolution, FPS, audio, process priority, network options, recording format. Has basic.ini section/key names and streamEncoder.json keys. |
| youtube.md | You need bitrates, ingest URLs, the stream key vs API setup flow, latency or DVR choices, API endpoint names and OAuth scopes, or you must decode a Control Room health message. Also lists corrections to SPEC presets. |
| macos.md | Permissions (Screen Recording, Camera, Microphone, Accessibility), tccutil resets, capture sources on macOS 14, app audio, thermal and power tips, Continuity Camera, virtual camera install. |
| troubleshooting.md | A symptom is reported (can't connect, dropped frames, encoding overloaded, rendering lag, black capture, no audio, desync, YouTube "not receiving data"). Has log locations and grep patterns. |
| websocket.md | Writing or debugging obsagent ws commands: enabling the server, auth, GetStats and GetStreamStatus fields, SetProfileParameter, SetStreamServiceSettings keys, CreateInput kinds. |
| zoom.md | Targeting a Zoom meeting or webinar via custom live streaming, or using OBS Virtual Camera as a Zoom camera. |
| guests.md | A show has remote guests: VDO.Ninja links, the guests commands, the browser source settings, echo prevention, a host checklist, and fixes for a black tile, no audio, echo or a guest who can't hear the host. |
| preflight-checklist.md | Preparing any show: T-24h, T-1h, T-10min, live, post-show. |

Machine facts used throughout: MacBook Pro M1 Pro 16 GB, macOS 14 (Darwin 23.6), OBS 33.0.1, bundle id com.obsproject.obs-studio (read from /Applications/OBS.app Info.plist), config dir ~/Library/Application Support/obs-studio (does not exist until first launch).
