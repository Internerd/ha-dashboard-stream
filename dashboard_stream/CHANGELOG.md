# Changelog

All notable changes to the **Dashboard Stream Cam** app are documented in
this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [1.5.0] - 2026-09-01

### Added

- `audio_track` option, defaulting to `silent`: the capture now publishes a
  silent AAC track alongside the video. A dashboard has no sound, but a
  video-only stream is unusual for a camera and NVR playback pipelines
  commonly expect an audio track - UniFi Protect adopts the camera, reads the
  RTSP session and then reports "cannot load live feed". Verified end to end:
  a client now sees two tracks (H264 + AAC LC, mono 16 kHz) instead of one.
  `none` restores the previous video-only stream.
- The ONVIF profile describes the audio track when it is enabled
  (`AudioSourceConfiguration` and `AudioEncoderConfiguration`, in ONVIF's
  element order), plus `GetAudioSources`,
  `GetAudioSourceConfiguration(s)`, `GetAudioEncoderConfiguration(s)` and
  `GetAudioEncoderConfigurationOptions`. With `audio_track: none` those
  answer with empty lists, so the description always matches the stream.

## [1.4.1] - 2026-09-01

### Fixed

- The "page has no visible text, so the stream is a blank picture" warning
  added in 1.4.0 fired on perfectly rendered dashboards. It measured
  `document.body.innerText`, which is empty for Home Assistant's frontend
  because the whole UI lives in shadow roots. The check now walks the shadow
  trees (skipping script and style text) and reports how many roots it saw,
  and only warns when the page has neither text nor elements.
- The capture published RTP packets larger than the RTSP server accepts, so
  every frame was repacketised: `RTP packets are too big (1460 > 1440),
  remuxing them into smaller ones`. Reproduced against mediamtx v1.20.0 and
  fixed with `-pkt_size 1400`, which also keeps the packets inside a normal
  1500-byte MTU on the way to an NVR.

## [1.4.0] - 2026-08-30

### Added

- `color_scheme` option: `dark` starts the kiosk browser with
  `--force-dark-mode`, which is what Home Assistant's frontend follows when
  the user's theme is set to Auto, so the streamed dashboard renders dark.
  `light`/`auto` keep the previous behavior.
- `GetNetworkInterfaces`, `GetNetworkProtocols` and `GetHostname`. UniFi
  Protect asks for the first of these during adoption and this device
  answered "operation not supported"; NVRs key a camera by its MAC address,
  which is what that call is for. The address of the interface holding the
  advertised IP is reported when it can be determined, otherwise a stable
  locally-administered address derived from the persistent device UUID.
- The browser supervisor now logs what the kiosk is actually displaying
  after each start - URL, title, readyState and how much text and how many
  elements the page has - and warns when the page is blank, since a blank
  page streams as a blank picture and looks like a broken camera.
- The snapshot log line reports the file's real size. This matters because
  aiohttp's access log shows the header size for file responses, so a
  healthy 12 kB JPEG appears there as `200 240` - documented in DOCS.md so
  the number is not mistaken for a broken image.

## [1.3.1] - 2026-08-30

### Fixed

- The JPEG snapshot was never produced, so `/snapshot.jpg` answered 503 for
  the lifetime of the app and NVR thumbnails stayed empty. The capture wrote
  to `snapshot.jpg.tmp`, and ffmpeg picks its output format from the file
  extension: `.tmp` is not one it knows, so it refused to open the output
  every single time. The temp file is now written with an explicit
  `-f image2 -c:v mjpeg`, verified to produce a valid JPEG of the rendered
  dashboard.
- That failure was invisible: ffmpeg's stderr went to `/dev/null` and a
  non-zero exit was not reported. The loop now logs when capture starts
  failing (with ffmpeg's own message) and when it recovers - once per state
  change, not once per interval.
- The snapshot is taken from the display named by `DISPLAY` rather than a
  hard-coded `:99`, and without the mouse pointer, matching the video capture.

## [1.3.0] - 2026-08-30

### Fixed

- A successfully adopted camera showed no picture in UniFi Protect. Two
  causes, both visible in the log the previous release added:
  - Protect asked for `GetVideoEncoderConfigurationOptions`, which this
    device answered with "operation not supported". That call, and the
    related `GetVideoEncoderConfiguration(s)`,
    `GetVideoSourceConfiguration(s)`, `GetVideoSourceConfigurationOptions`
    and `GetProfile`, are now implemented - all reporting the one fixed
    profile this app streams.
  - Protect fetched the snapshot URL with a plain GET and got HTTP 401 on
    every attempt: it never answers the authentication challenge. The URL
    returned by `GetSnapshotUri` now carries a random per-install token, so
    such a fetch succeeds; HTTP Basic with the stream credentials still
    works. See SECURITY.md for what that token is and how to rotate it.
- The capture now encodes H.264 Main profile explicitly, which is what the
  ONVIF service has always advertised - libx264 would otherwise default to
  High, leaving the stream and the device description disagreeing.

## [1.2.0] - 2026-08-30

### Added

- `onvif_extra_port` option: serve the ONVIF/snapshot service on a second
  port in addition to `onvif_port`. UniFi Protect's "Advanced Adoption"
  takes an IP address and then speaks ONVIF on the standard HTTP port
  rather than asking which port to use, so an app answering only on 8080 is
  never contacted at all - the adoption fails with "invalid credentials"
  while nothing whatsoever appears in this app's log. Setting the option to
  `80` makes that flow work. A port already in use on the host is logged as
  a warning and skipped, never fatal.
- The startup log now summarises what is listening where and the RTSP URL
  to hand an NVR, so the address an NVR should be pointed at no longer has
  to be pieced together.

## [1.1.2] - 2026-08-30

### Fixed

- Stream credentials were pasted unquoted into the generated RTSP server
  config, so YAML - not the user - decided what the password was. Verified
  against mediamtx v1.20.0: `Pass #1` authenticated as `Pass`, `"quoted"`
  lost its quotes, and `@home1`, `*secret` or a purely numeric password made
  mediamtx refuse to start at all. Every one of those looks like "invalid
  credentials" to an NVR. Credentials are now written as JSON (a subset of
  YAML), which round-trips them exactly.
- The app options were also interpolated unquoted into the environment file
  every service sources, so a device name containing an apostrophe truncated
  it and a value containing `$` or backticks was expanded - backticks were
  executed. Values are now written with `printf %q`.
- Credentials are validated at startup against the character set the RTSP
  server actually accepts (letters, digits and `! $ ( ) * + . ; < = > [ ] ^
  _ - { } @ # &`). A password with a space, colon, slash or quote now stops
  the app with a message naming the allowed set, instead of leaving the RTSP
  server dead or authenticating against something else.

### Changed

- The RTSP server's log level follows the app's `log_level`: at `info` it
  records every connection attempt and every failed authentication with the
  peer address, which is what shows whether an NVR reached the stream at all.
- mediamtx 1.20 enables MoQ by default, opening two further host ports this
  app has no use for; it is now switched off along with RTMP, HLS, WebRTC
  and SRT.

## [1.1.1] - 2026-08-30

### Fixed

- NVRs (UniFi Protect among them) failed to add the camera with "invalid
  credentials". mediamtx offers only Basic authentication by default, while
  NVRs commonly authenticate with Digest and report nothing more specific
  than bad credentials when it is not offered. The generated RTSP config now
  sets `rtspAuthMethods: [basic, digest]`; verified against mediamtx v1.20.0,
  whose 401 previously carried a `Basic` challenge alone and now carries both.
- Chromium's `Registration response error message: DEPRECATED_ENDPOINT`
  lines survived the 1.0.2 flags. `--log-level` only takes effect once
  logging has been initialised, so it is now paired with
  `--enable-logging=stderr`, and the GCM/D-Bus noise lines are dropped from
  Chromium's stderr as a backstop. Everything else Chromium logs still
  reaches the app log, and `log_level: debug` keeps it all.

### Added

- ONVIF requests are now logged with the peer address and the outcome:
  which operation was called, how it authenticated, or precisely why it was
  refused (unknown username, wrong password, clock skew beyond 300 s,
  missing WS-Security header) - and a warning naming any operation an NVR
  asks for that this device does not implement. Passwords are never logged.

## [1.1.0] - 2026-08-30

### Added

- `advertise_ip` option: the address handed to ONVIF/RTSP clients in the
  stream URL, the ONVIF service addresses and the WS-Discovery replies.
  Left empty it keeps the previous behavior (the address of the host's
  default route). On a host with several interfaces or VLANs that address
  can be one the NVR cannot reach, which made the camera appear
  unreachable even though the stream was being served on every interface
  all along. An invalid value falls back to auto-detection with a warning.

### Changed

- The startup log now states which address is advertised and where it came
  from (`advertise_ip` or auto-detection), and says what to set if the NVR
  cannot reach it.
- DOCS.md now spells out that Home Assistant does not proxy the stream at
  all: with host networking the RTSP/ONVIF/snapshot ports sit directly on
  the host's interfaces and NVRs connect to them straight.

## [1.0.5] - 2026-08-30

### Fixed

- The panel's dashboard dropdown only ever offered the default dashboard and
  the one configured in the app options, never the instance's actual
  dashboards. It queried `GET /core/api/lovelace/dashboards`, which does not
  exist - Home Assistant has no REST endpoint for this, its own frontend uses
  the WebSocket command `lovelace/dashboards/list`. The app now uses that
  command too, over the Supervisor's WebSocket proxy, falling back to
  `ha_url` with `ha_long_lived_token` if the Supervisor is not reachable.
  Because this app uses host networking (where the `supervisor` hostname may
  not resolve), the Supervisor's fixed address is tried as well as its name.

## [1.0.4] - 2026-08-30

### Fixed

- The ingress panel reported "The app is not ready yet, it may still be
  starting" and never opened. It was bound to `127.0.0.1`, but with
  `host_network` the Supervisor's ingress proxy connects to the gateway of
  its own Docker network (`172.30.32.0/23`), not to the app's loopback, so
  it could never reach the panel. The panel now listens on all interfaces
  and refuses any request whose peer is outside that network (or loopback)
  with `403`, which keeps it off the LAN as before. Refused peers are
  logged.
- The Xvfb log filter added in 1.0.3 used `grep --line-buffered`, which
  BusyBox grep does not support - it printed a usage error on every start
  instead of filtering. It now uses `awk` with `fflush()`, which this image
  does have, and which (unlike BusyBox grep piping into a pipe) does not
  block-buffer a genuine X error out of sight.

## [1.0.3] - 2026-08-30

### Fixed

- Xvfb printed a block of `Could not resolve keysym XF86...` warnings from
  the XKEYBOARD keymap compiler on every start (current Alpine ships
  xkeyboard-config data newer than the keysym table `xkbcomp` was built
  with). Exactly those lines are now filtered out of Xvfb's stderr; real X
  server errors still reach the log.
- ffmpeg warned `Stream #0: not enough frames to estimate rate; consider
  increasing probesize` on every capture start: a single raw 1920x1080
  frame is ~8 MB, above ffmpeg's 5 MB default probe size, so the input
  probe never saw a full frame. Capture now runs with `-probesize 64M`.
- Xvfb ran with `-nolisten unix`, which suppressed the `/tmp/.X11-unix/X99`
  socket entirely, so the Chromium service's readiness check for it could
  never succeed and always burned its full 30 second timeout before
  starting the browser.

### Changed

- Xvfb now uses `-nolisten local` instead of `-nolisten unix`: with
  `host_network` enabled, X's *abstract* socket is scoped to the shared
  network namespace and therefore reachable from the host, while the plain
  socket it now uses lives in this container's own `/tmp`.

## [1.0.2] - 2026-08-30

### Fixed

- Chromium flooded the app log on every start with
  `Failed to connect to the bus: Failed to connect to socket
  /run/dbus/system_bus_socket`,
  `Failed to connect to the bus: Could not parse server address: Unknown
  address type` and
  `Failed to call method: org.freedesktop.DBus.NameHasOwner`. The container
  now runs its own minimal, container-local D-Bus system and session bus
  (`dbus` / `dbus-session` services), so those probes get a clean answer
  instead of failing and retrying. The session bus also removes the
  `autolaunch:` fallback address Chromium's libdbus cannot parse.
- Chromium repeatedly logged
  `Registration response error message: DEPRECATED_ENDPOINT` from Google
  Cloud Messaging. Background networking, sync, component updates,
  domain reliability, crash reporting and the secret-service password
  store are now disabled - none of them are used by a kiosk renderer.

### Changed

- Chromium's own stderr logging is limited to fatal errors
  (`--log-level=3`) unless the app's `log_level` option is set to `debug`,
  so residual browser-internal chatter cannot drown out the app's log.

## [1.0.1] - 2026-08-16

### Fixed

- Docker build failed on Home Assistant Supervisor with
  `ERROR: unable to select packages: mesa-dri-swrast (no such package)`.
  Current Alpine (as used by `ghcr.io/home-assistant/base:3.23`) merged the
  software-rasterizer DRI driver into the `mesa-dri-gallium` package;
  switched to that.
- Replaced `ttf-freefont` (no longer present in current Alpine aports)
  with `font-noto`, which also gives broader Unicode/Latin/Cyrillic/Greek
  glyph coverage for rendered dashboards.

## [1.0.0] - 2026-08-08

### Added

- Initial release.
- Headless Chromium + Xvfb + ffmpeg pipeline rendering a configurable Home
  Assistant dashboard to an authenticated RTSP stream (via mediamtx).
- Minimal ONVIF device/media SOAP service and WS-Discovery responder, with
  WS-Security (digest and plain) authentication, for UniFi Protect and
  other ONVIF NVR integration.
- HTTP Basic-authenticated JPEG snapshot endpoint.
- Ingress web panel with a live dropdown of the instance's real Home
  Assistant dashboards, backed by the Home Assistant API.
- Automatic sign-in for the headless browser via Trusted Networks or an
  injected Long-Lived Access Token.
- s6 process supervision, an internal CDP-based hang-detection watchdog,
  and a Supervisor-level container watchdog.
