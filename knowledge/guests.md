# Remote guests with VDO.Ninja

Read this when a show has guests who join from their own computers. Source: https://docs.vdo.ninja (checked 2026-11-01) and obs-browser master on GitHub.

## How it works

VDO.Ninja is a free browser tool. There is no account. A room exists the moment someone opens a link that names it, and a password makes the room name unique to you. Each guest opens a personal link in Chrome, Edge or Firefox, allows the camera and microphone, and appears in the room. OBS shows each guest through a browser source that views only that guest. Your own camera and mic stay in OBS as usual.

Three roles, three link types:

| Link | Who opens it | Format |
|---|---|---|
| Guest | one guest | `https://vdo.ninja/?room=ROOM&password=PASS&push=GUESTID&label=NAME` |
| Director | you, in a normal browser tab | `https://vdo.ninja/?director=ROOM&password=PASS` |
| OBS solo | the OBS browser source | `https://vdo.ninja/?room=ROOM&password=PASS&view=GUESTID&solo` |

The label shows the guest's name in the director view. Keep the director tab open for the whole show: it is where you mute or remove a guest.

## What obsagent stores

- `~/.config/obsagent/shows/<plan>.json` (folder mode 700, file mode 600): room name, guest names, slots and ids.
- macOS Keychain, service `obsagent`, account `vdo:<plan>`: the room password.
- `~/Documents/obsagent/guides/<plan>-<name>.html`: one personal guide per guest, with the link filled in.

Nothing with a link or password goes in the repo or in a plan file. The repo is public.

## Commands

```
obsagent guests add <plan> --name "Ann"      # first add creates the room
obsagent guests list <plan>
obsagent guests links <plan> [NAME] --reveal # without --reveal the password shows as ********
obsagent guests guide <plan> [--name "Ann"]  # writes the HTML guide(s)
obsagent guests remove <plan> "Ann"
obsagent guests rotate <plan>                # new room and password, old links stop working
obsagent plan apply <plan> --dry-run         # shows the guest sources, masked
```

Up to 4 guests (5 people on screen with the host). The plan needs a `guests:` block (see `plans/talking-head.yaml`). Guide fields come from the plan: `title`, `show_time`, `tech_check`, `host_contact`, `youtube.watch_url`. Anything missing stays highlighted yellow in the guide, and the command lists it.

## What plan apply builds

- One browser source per guest, named `Guest N: NAME`, sized 1920x1080, pointing at the OBS solo link. Re-running updates the URL.
- A panel scene with your camera, the guest tiles and your mic. Two people sit side by side, three put two on top and one centered below, four make a 2x2 grid, five put three smaller tiles on top and two centered below. Your camera is always tile 1.
- With `solo_scenes: true`, a scene `Guest: NAME` per guest, that guest full frame plus your mic.
- Guests removed from the roster lose their `Guest N: ...` source on the next apply. Their solo scene stays until you delete it.

## OBS browser source settings

From obs-browser (`obs-browser-plugin.cpp` and `obs-browser-source.cpp`, master): input kind `browser_source`, settings `url`, `width`, `height`, and `reroute_audio`. The checkbox "Control audio via OBS" is `reroute_audio`, off by default. It must be on, or the guest's voice never reaches the OBS mixer and the stream. obsagent turns it on.

## Echo prevention

1. You wear headphones. Always. Without them your speakers play the guest's voice, your mic picks it up and the guest hears themselves.
2. Every guest wears headphones too. Say so in the guide and at tech check.
3. Open the director link with "Director will also be a performer" enabled, or add `&showdirector` to the link, so guests see and hear you. If you skip this, guests hear nothing from you.
4. Do not also listen to the guest tabs in a browser. The sound comes through OBS.
5. In OBS, set the guest sources to Monitor Off in Advanced Audio Properties unless you want to hear them in your headphones. Monitor and Output sends them to your headphones and the stream.

## Emailing invites

`obsagent guests invite` creates each guest's email in Mail.app, so it goes from your own address and Mail.app keeps the credentials. Set up once with `guests senders` and `guests set-sender ADDR --name NAME`; give each guest an address with `guests add --email` or `guests set-email`. The default opens a draft window to review; `--send` sends at once. `--attach-guide` attaches the personal guide. The From address must be an enabled Mail account: to send from james@ausvrn.com, add the ausvrn.com (Fastmail) account in Mail first. If macOS reports error -1743, allow your terminal to control Mail under System Settings > Privacy & Security > Automation. Emails are stored nowhere in the repo, only in `~/.config/obsagent/shows/`.

## Host checklist

- [ ] Headphones on, plugged in before OBS opens.
- [ ] Director tab open, performer option on, camera and mic allowed.
- [ ] `obsagent plan apply <plan> --dry-run`, then apply.
- [ ] Each Guest N source shows the guest in the OBS preview.
- [ ] Each guest source has "Control audio via OBS" ticked and a moving meter when the guest speaks.
- [ ] Panel scene tiles line up and nobody is cropped.
- [ ] Each guest is on headphones and has done a tech check.
- [ ] Links were sent privately (email or direct message), not in a chat room or post.
- [ ] After the show: `obsagent guests rotate <plan>` if the guests will not return.

## Troubleshooting

**Guest tile is black.** The guest has not joined yet, or joined with a different link. Check the director tab for their name. Right-click the OBS source, choose Refresh. If the guest is in the director view but not OBS, confirm the source URL uses `view=<their id>` and the same room and password: run `obsagent plan apply <plan>` again. If you rotated, old guest links point at the old room.

**No audio from the guest.** Open the source properties and confirm "Control audio via OBS" is ticked. Check the guest's meter in the OBS mixer. If it is flat, ask the guest to check the mic permission in the browser and that the right microphone is selected in VDO.Ninja. A muted guest in the director tab also looks like silence.

**Echo.** Someone is on speakers. Find them in the director tab by muting each guest in turn. The echo stops when the guilty mic is muted. Fix: headphones. Also check that no guest tab is open twice.

**Guest cannot hear the host.** The director link alone does not send your audio to the room. Enable "Director will also be a performer", or open `https://vdo.ninja/?director=ROOM&password=PASS&showdirector`. Then check your mic is allowed in that tab.

**Old link stopped working.** The room was rotated. Run `obsagent guests links <plan> NAME --reveal` and send the new link, or re-run `guests guide`.

## Background and locking

A plan can set `background: {image: "assets/backgrounds/slate-soft.png"}` or `background: {color: "#1E2A38"}` (optional `name:` and `scenes:`). `plan apply` creates one shared "Background" input, puts it at the bottom of every scene it manages (including the Panel and guest solo scenes), stretches it to the canvas and locks it. Changing between image and color replaces the input. Items that apply positions (tiles, inset) are locked too so they cannot be dragged by accident; set `lock_items: false` to turn that off.
