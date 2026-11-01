# macOS notes for OBS (M1 Pro, 16 GB, macOS 14)

## 1. Permissions

OBS 28+ shows an in-app guide: OBS Studio menu > Review App Permissions. Permissions OBS requests (OBS KB wording):
- Screen Recording: capture desktop, windows and apps.
- Camera: webcams and capture cards (also needed for the virtual camera test).
- Microphone: mics and audio devices.
- Accessibility: hotkeys when OBS is not the foremost app.

System Settings paths (macOS 13/14 layout; Apple's page gives the Privacy & Security route):
- Screen Recording: System Settings > Privacy & Security > Screen & System Audio Recording (on macOS 14 the pane can be labelled "Screen Recording"; scroll the sidebar if needed). Toggle OBS on, then quit and reopen OBS; the grant does not apply to a running process.
- Camera: System Settings > Privacy & Security > Camera.
- Microphone: System Settings > Privacy & Security > Microphone.
- Accessibility: System Settings > Privacy & Security > Accessibility.
- Camera extension (virtual camera): see section 6.

The OBS KB page has no manual steps or reset commands ("Coming soon"), so the commands below come from Apple's `tccutil` and third-party docs.

Reset commands (OBS bundle id `com.obsproject.obs-studio`, read from /Applications/OBS.app Info.plist). Quit OBS first; OBS re-prompts on next launch:
```
tccutil reset ScreenCapture com.obsproject.obs-studio
tccutil reset Camera        com.obsproject.obs-studio
tccutil reset Microphone    com.obsproject.obs-studio
tccutil reset Accessibility com.obsproject.obs-studio
```
Always include the bundle id; `tccutil reset ScreenCapture` with no bundle id resets every app. Do not run these unless the user asks; they remove a grant. Symptom that justifies a reset: toggle is on but capture is still black, or OBS is missing from the list (see troubleshooting.md).

Detection from the CLI: macOS exposes no supported API to read a TCC grant for another app without Full Disk Access (the TCC.db is protected). `doctor` should not claim a permission is granted; report "cannot detect, check System Settings path". Camera and microphone device lists from `system_profiler SPCameraDataType SPAudioDataType` work without permission.

Sources: https://obsproject.com/kb/macos-permissions-guide/ ; https://support.apple.com/guide/mac-help/control-access-to-screen-and-system-audio-recording-mchld6aa7d23/mac ; https://docs.screenpi.pe/permissions (tccutil, third-party)

## 2. Capture sources on macOS 14

- macOS Screen Capture (`screen_capture`) is the ScreenCaptureKit source. Methods: Display Capture (entire display), Application Capture (all windows of one app), Window Capture (one window). On macOS 13+ it can also capture audio. On 12.3-12.6 it works without audio. Options: Show windows with empty names (off by default), Show fullscreen and hidden windows/applications (off), Show cursor (on).
- Legacy Display Capture (`display_capture`) and Window Capture (`window_capture`) are flagged deprecated on macOS 13+ in source; do not use them.
- Video Capture Device (`macos-avcapture` standard, `macos-avcapture-fast` low latency IOSurface path) handles StreamCam, FaceTime HD and capture cards. Fast path: choose it for lower latency, uses `frame_rate`. Standard path: uses AVCaptureSession presets and has a `buffering` option.
- Browser source for overlays (obs-browser plugin, CEF).
- Window or app capture needs the target to be on screen to avoid empty frames; minimised windows give a frozen or blank image.

Source: https://obsproject.com/kb/macos-screen-capture-source ; https://github.com/obsproject/obs-studio/tree/master/plugins/mac-capture ; https://github.com/obsproject/obs-studio/blob/master/plugins/mac-avcapture/plugin-main.m

## 3. App and desktop audio capture

OBS KB "macOS Desktop Audio Capture Guide":
1. macOS Audio Capture source (`sck_audio_capture`): OBS 30+ and macOS 13+. All desktop audio or one application. First choice for this machine.
2. macOS Screen Capture source audio (OBS 28-30, macOS 13+): appears in Audio Mixer. Switch Method to Application or Window Capture to isolate one app. To capture audio only, put the source behind everything else or shrink it to zero width and height.
3. VB-CABLE (older macOS): create a Multi-Output Device in Audio MIDI Setup, set it as system output, set OBS Desktop Audio to VB-CABLE. No per-app capture. Loopback (Rogue Amoeba) and Sound Siphon are third-party per-app options. The page's captions mention BlackHole 16ch but the written steps cover VB-CABLE only.

Mic: `coreaudio_input_capture`. Set Global Audio Devices in OBS Settings > Audio to Mic/Aux for the mic.
Desktop audio with hardware that has no loopback needs one of the options above.

Monitoring: set Advanced Audio Properties > Audio Monitoring to Monitor Only on desktop audio only if needed; monitoring a mic with speakers causes echo. Use headphones.

Source: https://obsproject.com/kb/macos-desktop-audio-capture-guide

## 4. Power and thermal tips for M1 Pro (obsagent guidance)

These are practical rules, not from a single source:
- Plug in power. On battery, macOS may throttle; Low Power Mode (System Settings > Battery) lowers CPU/GPU clocks, so turn it off for a show.
- Use the VideoToolbox hardware encoder to keep CPU load low. Check headroom in OBS Stats (CPU usage; "Average time to render frame" should be well under the frame time: 33.3 ms at 30 fps, 16.7 ms at 60 fps).
- Close GPU-heavy apps (Chrome with video, games, Zoom if not in use). Check Activity Monitor > Window > GPU History.
- Keep the lid open or use a stand; do not stream with the lid closed on battery. Avoid clamshell without airflow.
- Keep base resolution equal to output resolution; avoid downscaling a 4K display capture to 1080p live. If the display is Retina scaled, capture at native and expect the large base canvas; set base to 1920x1080 and the display to a scaled "looks like 1080p" mode if load is high.
- Reduce load with: 30 fps instead of 60, fewer browser sources, no filters on full-canvas sources, hardware acceleration enabled for browser source (`[General] BrowserHWAccel`).
- Observe: `sudo powermetrics --samplers thermal -n 1` shows thermal pressure (needs sudo, do not run unprompted). Sustained "Heavy" or "Critical" pressure means thermal throttling.
- 16 GB RAM: leave at least 4 GB free. Memory pressure green in Activity Monitor.
- Disable App Nap for OBS if streaming while hidden: OBS opts out itself; verify with `Info` pane in Finder if frames stall when the window is covered. UNVERIFIED.
- macOS 14 notifications: turn on Focus/Do Not Disturb to avoid popups appearing in Screen Capture of the display.

## 5. Continuity Camera (iPhone as webcam)

Apple requirements: iOS 16+, macOS Ventura 13+, iPhone XR or later, same Apple Account with two-factor, Continuity Camera on (Settings > General > AirPlay & Continuity; on by default). Wireless needs Bluetooth and Wi-Fi on and both devices nearby; the Mac cannot be using AirPlay or Sidecar. Wired: USB with trust prompt accepted. The iPhone cannot be sharing cellular and the Mac cannot be sharing its internet connection.

In OBS: it appears as a Video Capture Device (`macos-avcapture`) named after the iPhone. Apple's page does not mention OBS; select the iPhone from the device list. Desk View, Center Stage, Studio Light and Portrait effects exist in macOS video effects (menu bar Video Effects) but their behavior in OBS is UNVERIFIED. If the iPhone does not appear, wake and unlock it, confirm same Apple Account, and relaunch OBS. Wired is more stable for a long stream than wireless.

Source: https://support.apple.com/en-us/102546

## 6. Virtual camera (OBS Virtual Camera)

- OBS 30+ is required on macOS 14; OBS 29.1 and earlier do not work there. OBS 33 qualifies.
- First start triggers a System Extension / Camera Extension approval. Path: macOS 13-14: System Settings > Privacy & Security > Security (click Allow next to OBS); macOS 15+: System Settings > General > Login Items & Extensions > Camera Extensions. Restart OBS afterwards.
- If the camera is missing after an in-place update, reinstall OBS to re-trigger approval (user reports).
- Start from the Controls dock "Start Virtual Camera"; the gear lets you choose Program, Preview (Studio Mode), a Scene, or a Source.
- Camera permission for the host app (Zoom) is under Privacy & Security > Camera.

Sources: https://obsproject.com/kb/virtual-camera-troubleshooting ; https://obsproject.com/kb/virtual-camera-guide

UNVERIFIED: the exact label of the Screen Recording pane on macOS 14.x, the thermal and App Nap items, and whether Continuity Camera effects pass through to OBS.
