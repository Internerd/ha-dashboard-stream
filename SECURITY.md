# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a suspected security
vulnerability. Instead, report it privately:

- Preferred: use GitHub's **"Report a vulnerability"** button under this
  repository's **Security** tab (private Security Advisory).
- Alternative: email **marcel-hoess@live.de** with a description, steps
  to reproduce, and the affected version/commit.

Please include enough detail to reproduce the issue (app version,
Home Assistant/Supervisor version, architecture, relevant configuration
with credentials redacted). We aim to acknowledge reports within a few
days. There is no bug bounty; credit is given in the fix's changelog
entry if you'd like it.

## Supported versions

This repository has a single app. Security fixes are only made against
the latest released `version` in
[`dashboard_stream/config.yaml`](./dashboard_stream/config.yaml)/
[`CHANGELOG.md`](./dashboard_stream/CHANGELOG.md) - please update before
reporting or re-testing an issue.

## Security model & known trade-offs

This app deliberately makes some trade-offs to behave like a real IP
camera on your LAN. Understand these before installing it:

### Host networking

The app runs with `host_network: true`. This is required for:
- WS-Discovery (UDP multicast) to actually reach other devices on your
  LAN - Docker's default bridge networking blocks/NATs multicast;
- the RTSP and ONVIF ports to be reachable at a stable address by NVR
  software, the same way a physical camera is.

**Consequence:** the app's ports bind directly to your Home Assistant
host's network interfaces, bypassing Supervisor's normal per-app Docker
network isolation. Two things are done to limit the blast radius of this:

1. The **dashboard-picker ingress web panel is bound to `127.0.0.1`
   only** and is reachable exclusively through Home Assistant's own
   authenticated Supervisor ingress proxy - it is never exposed on your
   LAN, even though the rest of the app uses host networking.
2. The RTSP, ONVIF and snapshot ports **are** intentionally LAN-reachable
   (that's the app's purpose) and are gated by the username/password you
   configure, matching what UniFi Protect and other NVRs already expect
   from third-party cameras.

### Credentials

- The app **refuses to start** with an empty `stream_password` - there is
  no working default/hardcoded credential.
- `stream_username`/`stream_password` gate the RTSP stream, the ONVIF
  SOAP service (via WS-Security UsernameToken, digest or plain) and the
  JPEG snapshot endpoint (via HTTP Basic auth).
- The RTSP server offers both Basic and Digest authentication. Digest is
  there because NVRs commonly require it; over plain (unencrypted) RTSP
  neither method keeps the password confidential from someone who can
  capture the traffic, so treat the stream credentials as LAN-visible and
  do not reuse a password you use elsewhere.
- ffmpeg's local publish leg to the embedded RTSP server uses a separate,
  fixed, non-secret credential restricted (via mediamtx's `ips` allow
  list) to `127.0.0.1` only - so it never carries your real stream
  password on a process command line (visible to anything else able to
  inspect processes in the container), and cannot be used to publish from
  outside the container's own loopback.
- Both RTSP paths - `/stream` and, when `substream` is enabled,
  `/substream` - carry the same rendering and are gated by the same
  credentials, read-only. The loopback-only publish credential may publish
  to both and read neither. Turning `substream` off removes the path from
  the server entirely rather than leaving it unauthenticated.
- The snapshot URL that ONVIF hands out carries a random 128-bit token
  (`/snapshot.jpg?token=...`, stored in `/data/snapshot_token`). NVRs
  routinely fetch that URL with a plain GET and never answer an HTTP 401,
  so the token stands in for the credentials on that one endpoint. It is
  only ever disclosed inside an authenticated `GetSnapshotUri` response,
  but it is a bearer credential: anyone who obtains the URL can fetch
  snapshots without the stream password. HTTP Basic with the stream
  credentials keeps working, and deleting `/data/snapshot_token` issues a
  new one on the next start.
- The ingress dashboard-picker panel has no credentials of its own: it
  trusts Home Assistant to have authenticated the user before proxying the
  request. Since host networking prevents it from binding to loopback (the
  Supervisor would not be able to reach it), it instead refuses every
  request whose peer address is outside the Supervisor's own Docker
  network `172.30.32.0/23` (plus loopback), and logs the address of
  anything it turns away.
- If you use the optional `ha_long_lived_token` login method, that token
  is only ever sent to (a) your own configured `ha_url` over whatever
  transport that URL uses, and (b) written into the headless browser's
  local storage inside this app's own container. Prefer HTTPS for
  `ha_url` if the app and Home Assistant aren't on a fully trusted
  network segment, and use a token tied to a dedicated, low-privilege HA
  user rather than an administrator account.

### Chromium sandboxing

Chromium runs with `--no-sandbox`. This is standard practice for headless
Chromium inside a container without the extra Linux capabilities
(`SYS_ADMIN`) its own sandbox needs, and relies on the container/Docker
boundary as the isolation layer instead. If you require Chromium's own
sandbox as well, this would need a more privileged container
configuration than this app currently requests - which is a deliberate
trade-off against minimizing the app's privileges.

### What's exposed to whoever has the stream credentials

Anyone with network access to the RTSP/ONVIF/snapshot ports **and** the
configured credentials can view exactly what the chosen dashboard shows.
Don't put anything on that dashboard you wouldn't want visible to
whoever you (deliberately or accidentally) share those credentials with,
and don't reuse a password from elsewhere.

## Reporting non-security bugs

Use the regular [issue tracker](https://github.com/internerd/ha-dashboard-stream/issues)
- see [CONTRIBUTING.md](./CONTRIBUTING.md).
