# Dashboard Stream Cam

Renders one of your Home Assistant dashboards headlessly (Chromium + a
virtual framebuffer) and republishes it as a normal, authenticated network
camera: an RTSP stream plus an ONVIF device/media service with
WS-Discovery, so NVR software - most notably **UniFi Protect**, which can
add third-party cameras via ONVIF - can add it exactly like a physical
camera.

> Home Assistant renamed "Add-ons" to "Apps" in Supervisor 2026.07. This
> repository is written against the current App manifest, and installs the
> same way regardless of which term your Home Assistant UI uses.

## What you get

- An RTSP stream (`rtsp://<host>:8554/stream`) protected by username/password.
- An ONVIF device + media service (`http://<host>:8080/onvif/...`) that
  reports one fixed video profile, gated by the same credentials via
  WS-Security (digest or plain), plus a WS-Discovery responder so
  ONVIF-aware NVRs can find it automatically.
- A JPEG snapshot endpoint (`http://<host>:8080/snapshot.jpg`, HTTP Basic
  auth) for NVR thumbnails.
- A web panel (open the app and click **Open Web UI** / the sidebar panel)
  with a **live dropdown of your real Home Assistant dashboards** to pick
  which one is streamed - no need to hand-type a URL. The list is read over
  Home Assistant's WebSocket API, the same way its own frontend lists them.
- Three layers of self-healing: s6 process supervision (instant restart of
  any crashed component), an internal watchdog that force-restarts a
  browser that's alive but stuck rendering, and a Supervisor-level
  container watchdog as the outermost safety net.

## Before you start

1. **Pick (or create) a dashboard** you want to expose. A dashboard built
   for a wall panel / kiosk display (few, large cards; no sidebar) works
   best - a normal admin dashboard will render, but wastes stream real
   estate on menus.
2. **Decide how the headless browser will log in.** Home Assistant's
   frontend always requires a session; pick one of:
   - **Trusted Networks (recommended)** - add the app's network as a
     trusted, no-login range in `configuration.yaml`:
     ```yaml
     homeassistant:
       auth_providers:
         - type: trusted_networks
           trusted_networks:
             - 127.0.0.1/32
             - ::1/128
             - 172.30.32.0/23    # Supervisor/app network - adjust to your setup
           trusted_users: {}
           allow_bypass_login: true
         - type: homeassistant
     ```
     Restart Core after editing. This avoids storing any credential in the
     app at all.
   - **Long-Lived Access Token** - if you'd rather not edit YAML, create
     one under your HA user profile (**Profile &rarr; Security &rarr;
     Long-Lived Access Tokens**) and paste it into the app's
     `ha_long_lived_token` option. The app injects it into the browser's
     local storage on startup, the same mechanism the frontend itself uses
     after a normal login - nothing is sent anywhere outside this app's own
     container. Prefer a dedicated, low-privilege HA user for this token
     rather than an admin account.
3. **Choose a username and password for the stream.** This is required -
   the app refuses to start with an empty password. Use a password you
   haven't reused elsewhere; it will also be visible to anyone with access
   to your Home Assistant configuration.

## Installation

1. In Home Assistant: **Settings &rarr; Add-ons &rarr; Add-on Store**
   (or **Settings &rarr; Apps &rarr; App Store**, depending on your
   Supervisor version) &rarr; the three-dot menu &rarr; **Repositories**.
2. Add: `https://github.com/internerd/ha-dashboard-stream`
3. Find **Dashboard Stream Cam** in the store and install it. Supervisor
   builds the container from this repository's `Dockerfile` directly - no
   external registry or account is required.
4. Open the **Configuration** tab and set at least `ha_url`,
   `stream_username`, `stream_password`, and either
   `ha_long_lived_token` or your Trusted Networks setup from above.
5. Start the app, then open its **Web UI** (sidebar panel) to pick the
   dashboard from the live dropdown.

## Configuration reference

| Option | Description |
| --- | --- |
| `ha_url` | Base URL the internal browser uses to reach Home Assistant. |
| `ha_long_lived_token` | Optional token used to auto-sign the browser in (see above). |
| `dashboard_path` | Fallback dashboard path, used until you pick one in the Web UI. |
| `dashboard_custom_url` | Advanced: render a full custom URL instead. |
| `resolution` | Capture/output resolution. |
| `framerate` | Output frame rate. |
| `audio_track` | `silent` (default) publishes a silent AAC track alongside the video, because a video-only stream is unusual for a camera and some NVRs refuse to play one - UniFi Protect reports "cannot load live feed". `none` streams video only. The ONVIF profile describes whichever is configured. |
| `color_scheme` | `dark` renders the dashboard in dark mode, `light` forces light, `auto` leaves it to the browser (light). Works through the browser's `prefers-color-scheme`, so the Home Assistant user whose token this app uses must have its theme set to **Auto** - a theme pinned in that user's profile wins. |
| `render_wait` | Seconds to let the page finish rendering before capture starts. |
| `reload_interval` | Seconds between automatic page reloads (0 disables). Mitigates browser memory growth on long-running kiosks. |
| `rtsp_port` / `onvif_port` | Fixed TCP ports for the stream and the ONVIF/snapshot service. Leave at the defaults (8554/8080) unless they conflict with something else on your host - the Supervisor-level watchdog is wired to the default ONVIF port. |
| `onvif_extra_port` | Serve ONVIF/snapshot on a second port as well (0 = off). Set to `80` for NVRs that expect ONVIF on the standard HTTP port, such as UniFi Protect's Advanced Adoption. A port already in use on the host is logged and skipped, never fatal. |
| `onvif_enabled` | Turns the ONVIF service and WS-Discovery responder on/off. The RTSP stream keeps working either way. |
| `onvif_device_name` | Friendly name reported to ONVIF/NVR clients. |
| `advertise_ip` | IP address handed out to ONVIF/RTSP clients (stream URL, ONVIF service addresses, WS-Discovery). Empty = auto-detect from the host's default route. Set it when Home Assistant has several interfaces/VLANs and your NVR is not on the default-route one. It changes only the *advertised* address - every service always listens on all interfaces. |
| `stream_username` / `stream_password` | Credentials required for RTSP, ONVIF and the snapshot endpoint. Password is mandatory. The embedded RTSP server accepts only letters, digits and `! $ ( ) * + . ; < = > [ ] ^ _ - { } @ # &` in either value - no spaces, colons, slashes or quotes. Anything else is rejected at startup with a message rather than silently breaking authentication. |
| `watchdog_interval` / `stall_timeout` | How often, and after how long without a response, the internal watchdog force-restarts a hung browser. |
| `log_level` | Log verbosity. |

## Adding to UniFi Protect

UniFi Protect's third-party camera support expects an RTSP URL and
credentials, or ONVIF discovery - this app supports both paths:

**Manual RTSP (most reliable):**
1. UniFi Protect &rarr; **Devices** &rarr; **Add devices** &rarr;
   **Third-party camera**.
2. Enter the RTSP URL shown in this app's Web UI status panel, e.g.
   `rtsp://<home-assistant-host-ip>:8554/stream`.
3. Enter the `stream_username` / `stream_password` you configured.

**ONVIF discovery:**
1. Make sure `onvif_enabled` is on and Protect is on the same LAN/VLAN
   as Home Assistant (WS-Discovery is UDP multicast and does not cross
   routed VLANs unless you enable multicast/IGMP forwarding).
2. In Protect's third-party/ONVIF add-camera flow, either let it discover
   the device (it will show up as `onvif_device_name`) or enter the host
   IP and ONVIF port (`8080`) manually.
3. When prompted, enter the same `stream_username` / `stream_password`.

**Protect's "Advanced Adoption" (Labs):**
This dialog has a single **IP address** field plus username and password.
It wants exactly that - an IP address, not an RTSP URL - and Protect then
talks ONVIF to it on the standard HTTP port, not on this app's `8080`. So:

1. Set `onvif_extra_port` to `80` in the app options (leave `onvif_port` at
   `8080`; the Supervisor watchdog is wired to it). The app then answers
   ONVIF on both ports. If something else on the Home Assistant host
   already uses port 80, the app logs a warning and keeps working on 8080 -
   in that case use the manual RTSP method instead.
2. Enter the plain IP address of the Home Assistant host - the one the
   startup log names in its `Listening: …` line - with the
   `stream_username` / `stream_password`.

If discovery doesn't find the camera (common across VLAN boundaries, or
with switches that filter multicast), the manual RTSP method above always
works and is what we'd recommend defaulting to.

Whichever path you use, the app log tells you whether Protect arrived at
all: an RTSP attempt logs `[RTSP] [conn <address>] opened`, an ONVIF
request logs the operation and the peer. If a failed adoption leaves no
trace in the log, Protect never reached this app and the address or the
route between the two is what needs fixing, not the credentials.

## How it works

```
Home Assistant dashboard (HTTP)
        │  Chromium (kiosk, headless, --remote-debugging-port for control)
        ▼
   Xvfb virtual display
        │  ffmpeg (x11grab → H.264)
        ▼
   mediamtx (local RTSP server, per-user auth)
        │
        ├─ RTSP :8554  ───────────────► UniFi Protect / any RTSP client
        └─ (local publish only)

Supporting services:
   • D-Bus system + session bus (container-local, no host access) - exists
     only so Chromium's UPower/secret-service/session probes get answered

Python app (aiohttp, one process):
   • ONVIF device/media SOAP service + WS-Discovery  :8080, udp/3702 (LAN)
   • JPEG snapshot endpoint                          :8080 (LAN)
   • Ingress dashboard-picker web panel               Supervisor peers
                                                        only, via Home
                                                        Assistant's
                                                        authenticated proxy
   • Browser supervisor: login bootstrap, periodic reload, hang detection
   • /health endpoint used by the Supervisor watchdog
```

## Auto-restart, watchdog & logs

- Every process (Xvfb, Chromium, mediamtx, ffmpeg, the Python app) runs as
  an s6-supervised service and is restarted automatically if it exits.
- The Python app additionally pings the browser over its DevTools
  protocol every `watchdog_interval` seconds; if it doesn't respond for
  `stall_timeout` seconds (a "stuck but not crashed" browser), the app
  kills the Chromium process so s6 starts a fresh one.
- The app's manifest also declares a Home Assistant Supervisor
  `watchdog`, which restarts the whole app container if its `/health`
  endpoint stops responding - the outermost safety net, e.g. against a
  wedged Python event loop.
- `boot: auto` starts the app automatically on every Home Assistant
  restart/reboot.
- All logs (bashio-formatted shell logs and Python `logging` output) go to
  stdout and are visible in the app's **Log** tab, at the level set by
  `log_level`.
- Chromium's own stderr is quiet by default: it is started with
  `--log-level=3` (fatal only), because everything below that is chatter
  from browser subsystems this kiosk does not use. Setting `log_level` to
  `debug` switches Chromium to full `--log-level=0` logging as well.

## Troubleshooting

- **Blank/black stream** - increase `render_wait`; some dashboards
  (maps, camera cards) take a few seconds to finish loading.
- **Stuck on the login screen** - re-check the Trusted Networks CIDR (it
  must include the app's actual source address) or regenerate the
  Long-Lived Access Token; tokens can be revoked from the HA user profile.
- **UniFi Protect can't discover the camera** - use the manual RTSP entry
  method instead; WS-Discovery multicast is frequently blocked between
  VLANs.
- **Is the browser actually rendering anything?** (the check walks Home
  Assistant's shadow DOM, so a rendered dashboard reports thousands of
  characters across many shadow roots) Every start logs what the
  kiosk ended up displaying: `Browser is showing <url> (title …, readyState
  complete, N characters of text in M elements)`. `0 characters` means the
  page is blank and the stream is a blank picture - check `ha_url`, the
  dashboard path and the sign-in method. To see exactly what the camera
  sees, open the snapshot URL from the `GetSnapshotUri` response in a
  browser (it carries its own token).
- **Don't read the snapshot's size from the access log.** For file
  responses aiohttp logs the header size, not the image: a perfectly
  healthy 12 kB JPEG appears as `"GET /snapshot.jpg…" 200 240`. The
  `Snapshot capture is working … (N bytes)` line reports the real size.
- **The NVR adopts the camera but the live feed will not load** - one cause
  worth ruling out first is malformed ONVIF: an NVR's ONVIF client is
  usually generated from the WSDL and simply discards a response that
  violates the schema, without saying so. `tools/check_onvif_schema.py`
  validates every response of this app against the official ONVIF schema. - if the log
  shows the RTSP session being read and snapshots answered with 200, the
  device side is doing its job and the client is refusing the stream itself.
  The usual reason is the missing audio track; leave `audio_track` at
  `silent`. VLC (`vlc --rtsp-tcp rtsp://user:pass@host:port/stream`) is the
  quickest way to confirm the stream itself is fine.
- **The NVR adopted the camera but shows no picture** - check the log for
  `GET /snapshot.jpg ... 401` and for an ONVIF operation logged as not
  implemented. Both were what stopped UniFi Protect before 1.3.0: it asked
  for `GetVideoEncoderConfigurationOptions` (now implemented, along with the
  other encoder/source configuration calls) and fetched the snapshot URL
  without credentials (that URL now carries a token). If a different
  operation shows up as not implemented, that log line is exactly what to
  report.
- **The NVR says the credentials are invalid** - first check the app log
  while you retry. An RTSP attempt logs `[RTSP] [conn <address>] opened`
  and, if the password does not match, `failed to authenticate`. An ONVIF attempt now logs exactly what was refused
  (`ONVIF: refused GetStreamUri from … - password digest does not match
  stream_password`, a wrong username, a clock more than 300 s off, or an
  operation this device does not implement). If nothing appears at all, the
  NVR never reached the app: verify the address it is using (see
  `advertise_ip`) and that port 8554/8080 are reachable from it.
- **UniFi Protect finds the camera but cannot pull the stream** - the
  address it was handed is probably on the wrong interface. The app
  advertises the address of the host's default route, which is not
  necessarily the one your NVR can reach on a multi-VLAN host. Check the
  `Advertising … to ONVIF/RTSP clients` line in the log and, if it names
  the wrong address, set `advertise_ip` to the one the NVR should use. The
  stream itself listens on every interface, so nothing else needs
  changing.
- **High CPU** - lower `resolution`/`framerate`; software-rendering a
  browser is inherently more expensive than a real camera's hardware
  encoder, especially on Raspberry Pi-class hardware.
- **`Could not resolve keysym XF86...` in the log** - these came from
  Xvfb's keymap compiler and are filtered out; the virtual display has no
  keyboard attached in the first place, so they never meant anything.
- **`dbus`/`gcm` errors in the log** - Chromium probes a system and a
  session D-Bus and Google's push-messaging endpoint on startup. The
  container runs its own minimal, container-local D-Bus pair and disables
  the browser's background networking so these probes stay quiet; if you
  ever see them again, they are cosmetic and do not affect the stream.
- Check the **Log** tab first - shell and Python components both log
  clearly prefixed, timestamped messages there.

## Legal

Before deploying this on a real network, please read:

- [SECURITY.md](https://github.com/internerd/ha-dashboard-stream/blob/main/SECURITY.md) -
  the security trade-offs this app makes (below).
- [PRIVACY.md](https://github.com/internerd/ha-dashboard-stream/blob/main/PRIVACY.md) -
  what data the app touches and who is responsible for it (bilingual EN/DE).
- [DISCLAIMER.md](https://github.com/internerd/ha-dashboard-stream/blob/main/DISCLAIMER.md) -
  liability disclaimer, including the legal considerations that apply if
  your dashboard embeds real camera feeds your NVR then records
  (bilingual EN/DE).
- [NOTICE.md](https://github.com/internerd/ha-dashboard-stream/blob/main/NOTICE.md) -
  third-party software/spec/trademark attribution.

## Security notes

See [SECURITY.md](https://github.com/internerd/ha-dashboard-stream/blob/main/SECURITY.md)
in the repository for the full picture, in short:

- This app runs with `host_network: true` (required for WS-Discovery
  multicast and for the RTSP/ONVIF ports to be directly reachable on your
  LAN, like a real camera). That means its ports bind directly to your
  Home Assistant host's network interfaces, bypassing Supervisor's usual
  per-app Docker network isolation.
- The dashboard-picker web panel is reachable exclusively through Home
  Assistant's authenticated ingress proxy - it is **not** exposed on your
  LAN even though the app uses host networking. Because host networking
  means the Supervisor reaches the app through the gateway of its own
  Docker network (`172.30.32.0/23`) rather than through loopback, the
  panel listens on all interfaces but refuses every request whose peer is
  outside that network (and loopback); refused requests are logged with
  the address they came from.
- Nothing about the stream is proxied by Home Assistant: with host
  networking the RTSP, ONVIF and snapshot ports sit directly on the host's
  interfaces, and an NVR connects to them straight, not through Home
  Assistant. Only the ingress panel goes through the Supervisor. So the
  stream carries no Home Assistant TLS or authentication - it is protected
  by the stream credentials alone.
- The RTSP/ONVIF/snapshot ports **are** LAN-reachable by design (that's
  the point - they need to work like a normal IP camera), and are gated by
  the username/password you configure. Anyone with those credentials and
  network access can view the dashboard's contents (whatever
  entities/data it shows) - choose what you put on the streamed dashboard
  accordingly, and don't reuse a password you use elsewhere.
- Chromium runs with its own sandbox disabled (`--no-sandbox`), which is
  standard practice for headless Chromium in a container (the container
  itself is the isolation boundary) but worth knowing if you're
  threat-modelling this app.
