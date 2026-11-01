# Zoom as a target

Two routes: (A) Zoom pushes its own meeting or webinar out to a streaming service (Zoom is the source), (B) OBS feeds Zoom as a camera via OBS Virtual Camera (OBS is the source). The agent's "Zoom webinar via custom RTMP" in SPEC means route A with OBS on the receiving end only if you stream from Zoom to YouTube (OBS is not in that chain). To send an OBS-produced show into a Zoom webinar, use route B.

## A. Zoom custom live streaming service (Zoom to an RTMP destination)

Zoom Help "Live streaming a meeting or webinar using a custom service":
- Needs a Pro, Business, Education or Enterprise account; Workplace Meetings license for meetings, Zoom Webinars license for webinars.
- The account admin must enable Custom Live Streaming Service, separately for meetings and webinars. Desktop app for Windows or macOS at Zoom's minimum version. Enable HD Video for better quality.
- Zoom pushes over RTMP. Resolution options in the setup page: Standard HD (720p) or Full HD (1080p).
- Livestream URLs must be public for Zoom to stream to them.
- Regions have limited bandwidth; if a region is out, pick a nearby alternative.

Setup, webinar (web portal): Webinars > schedule > advanced options > Live Streaming tab > Configure custom streaming. Enter:
1. Stream URL (RTMP/RTMPS ingest of the destination, e.g. YouTube `rtmp://a.rtmp.youtube.com/live2` or `rtmps://a.rtmps.youtube.com:443/live2`).
2. Stream Key.
3. Live streaming page URL (where viewers watch, e.g. the YouTube watch URL).
4. Optional: authentication (Zoom prompts for username and password at start), resolution. Save.

Meetings: Meetings > schedule > Save > Live Streaming tab > Configure custom streaming; same fields.

Go live (host): start the meeting or webinar, More > Livestream > Live on Custom Live Streaming Service. If preconfigured, a browser window shows preparation progress. If not preconfigured, enter the three fields in the page that opens.

Pairing with YouTube: create the YouTube broadcast first (youtube.md section 4B), copy `rtmpsIngestionAddress` and `streamName` into Zoom, put the watch URL (`https://www.youtube.com/watch?v=<broadcastId>`) as the page URL. Zoom does not stream the OBS output; OBS is only needed if the OBS show is brought into Zoom as the camera or a shared window.

Limits and cautions (from the Zoom page, which used LinkedIn as its example):
- Do not click Go live on the destination during a test, it broadcasts to the audience.
- Destination preview windows can expire (LinkedIn: 10 minutes).
- Generate keys shortly before going live where the destination limits their lifetime (LinkedIn: key available one hour before, Zoom suggests 10 minutes before). YouTube keys are persistent, so this does not apply.
- Zoom's pages read do not state a bitrate or codec details for the stream. The SPEC zoom-720p30 3000 kbps is not backed by a Zoom source: UNVERIFIED. Use Zoom's 720p or 1080p setting and let Zoom pick the bitrate.

Source: https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0064210 (redirect target of https://support.zoom.us/hc/en-us/articles/115001777826-Live-streaming-a-meeting-or-webinar-using-a-custom-service)

## B. OBS Virtual Camera as the Zoom camera

1. OBS 30+ on macOS 14 (OBS 33.0.1 here). Start the virtual camera once, approve the camera extension (macos.md section 6), restart OBS.
2. In OBS Controls dock click Start Virtual Camera. Gear options: Program (default), Preview (Studio Mode), Scene, Source.
3. In Zoom: Settings > Video > Camera > OBS Virtual Camera. Grant Zoom Camera permission under System Settings > Privacy & Security > Camera if the list is empty.
4. Audio is not carried by the virtual camera. In Zoom Settings > Audio choose the real mic, or route OBS audio via a virtual device (BlackHole or Loopback, third-party) if you want the OBS mix in Zoom. Use headphones to avoid echo.
5. Zoom applies its own video resolution (720p default, 1080p with HD enabled by plan). Output the virtual camera from a 1280x720 or 1920x1080 canvas at 30 fps to avoid rescale blur.
6. Mirror: Zoom mirrors self-view; use Settings > Video > Mirror my video off if text appears reversed to you only (viewers see it unmirrored).

Webinar note: panelists can use the virtual camera; attendees cannot send video. Stop the virtual camera before quitting OBS or Zoom may hold a black frame; if Zoom shows black, stop and restart the virtual camera and re-select the camera.

Preset hint for the agent: for route B, record OBS output to disk with the normal record settings and do not set a stream service; for route A, no OBS profile is needed.

Sources: https://obsproject.com/kb/virtual-camera-guide ; https://obsproject.com/kb/virtual-camera-troubleshooting ; https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0064210

UNVERIFIED: Zoom camera-selection menu wording, webinar attendee video rules, Zoom's actual output bitrate, the 1080p webinar plan requirement, and the "stop virtual camera" black-frame tip. Search did not return Zoom's own page for virtual camera use.
