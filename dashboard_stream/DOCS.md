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
| `render_wait` | Seconds to let the page finish rendering before capture starts. |
| `reload_interval` | Seconds between automatic page reloads (0 disables). Mitigates browser memory growth on long-running kiosks. |
| `rtsp_port` / `onvif_port` | Fixed TCP ports for the stream and the ONVIF/snapshot service. Leave at the defaults (8554/8080) unless they conflict with something else on your host - the Supervisor-level watchdog is wired to the default ONVIF port. |
| `onvif_enabled` | Turns the ONVIF service and WS-Discovery responder on/off. The RTSP stream keeps working either way. |
| `onvif_device_name` | Friendly name reported to ONVIF/NVR clients. |
| `stream_username` / `stream_password` | Credentials required for RTSP, ONVIF and the snapshot endpoint. Password is mandatory. |
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

If discovery doesn't find the camera (common across VLAN boundaries, or
with switches that filter multicast), the manual RTSP method above always
works and is what we'd recommend defaulting to.

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
