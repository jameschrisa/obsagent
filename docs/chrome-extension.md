# Run the show from Chrome

A Chrome side panel and a small helper on your Mac. The panel has buttons for the routine steps and a box to ask Claude. Everything it does is also a Terminal command.

## How it fits together

- **The helper** is `obsagent serve`. It listens on `127.0.0.1:8767` only, and runs a fixed list of actions. It never runs a command the panel makes up.
- **The panel** is the `chrome-extension/` folder, loaded as an unpacked extension. It only talks to `http://127.0.0.1:8767`.
- **The pairing token** is a random value made the first time the helper runs. It lives in `~/.config/obsagent/bridge-token` (readable by you only). Every request must carry it.

## Install

1. Start the helper at login: `obsagent serve install`. It writes `~/Library/LaunchAgents/com.obsagent.bridge.plist`, logs to `~/Library/Logs/obsagent-bridge.log`, and loads it. To run it once in a window instead: `obsagent serve`.
2. Check it: `obsagent serve status`.
3. In Chrome open `chrome://extensions`, turn on Developer mode, choose Load unpacked, and pick `~/CODE/obs-agent/chrome-extension`.
4. Click the extension icon in the toolbar. The side panel opens.
5. In Terminal run `obsagent serve token` and copy the token. Paste it into the panel and press Connect. The token command refuses to print unless it is running in a terminal.

To remove everything: `obsagent serve uninstall`, then remove the extension in Chrome.

## What each part does

| Panel | What it does | Terminal equivalent |
|---|---|---|
| Status | OBS running or not, WebSocket, LIVE badge with timer, scene, profile, stream key name and last 4 characters, producer state. Updates every 3 seconds while the panel is visible. | `obsagent ws status` |
| Launch OBS | Opens OBS and waits up to 30 seconds for the WebSocket. | `open -a OBS` |
| Dry run | Shows what the plan would change. Changes nothing. | `obsagent plan apply PLAN --dry-run` |
| Apply | Applies the plan to the open OBS. Available after a clean dry run of the same plan, then asks to confirm. | `obsagent plan apply PLAN` |
| Preflight | Pass, warn and fail count for the plan. Never streams. | `obsagent preflight PLAN` |
| Scenes | Switches the program scene and checks it took. | `obsagent ws scene "NAME"` |
| Start producer | Runs the pod producer for the chosen plan. Stops after the minutes you set (default 90) or 15 minutes with no speech. | `podproducer run PLAN --max-minutes 90` |
| Stop producer | Stops the producer and its audio capture. | Ctrl+C in its terminal |
| Open cue dock | Opens `http://127.0.0.1:8765`. | same URL |
| Go Live | Shows the key name and last 4 characters and reminds you to check YouTube Studio. Starts only after you confirm. | `obsagent ws start-stream --yes` |
| Stop Stream | Asks first, then stops. | `obsagent ws stop-stream --yes` |
| Ask the agent | Sends your request to Claude with the obsagent or pod-producer skill. Follow-ups continue the same conversation until you press New conversation. | `claude`, then `/obsagent ...` |

## Safety notes

- The helper only answers requests that carry the token, that come from a `chrome-extension://` origin or from a tool with no origin, and whose Host header is `127.0.0.1:8767` or `localhost:8767`. That blocks web pages and DNS tricks.
- Go Live is enforced by the helper, not just the panel. The request must carry the last 4 characters of the key OBS holds now, or it is refused.
- Plan names must match a file in `plans/`. Scene names must exist in OBS.
- The panel and the helper show the key name and last 4 characters only. Job output is scrubbed for keys, passwords and the token before it is returned.
- Ask the agent runs `claude -p` in the repo folder with `--permission-mode dontAsk` and only these tools: Read, Grep, Glob, `obsagent` and `podproducer` commands. Starting or stopping a stream, sending invites and storing keys are denied, and the request tells Claude the panel buttons handle those.
- Anyone who can read your token file can control the helper. Keep `~/.config/obsagent/` private. To rotate the token, delete `bridge-token`, restart the helper (`obsagent serve uninstall`, then `install`), and pair again.

## Troubleshooting

- **Helper not running:** `obsagent serve install`, then `obsagent serve status`. Read `~/Library/Logs/obsagent-bridge.log`.
- **Token not accepted:** run `obsagent serve token` again and paste it. Use Forget token at the bottom of the panel first if needed.
- **Port busy:** something else holds 8767. `obsagent serve --port N` changes the helper's port, but the panel is fixed to 8767.
- **Scenes are grey:** OBS is closed or its WebSocket server is off. Use Launch OBS, or check Tools > WebSocket Server Settings.
