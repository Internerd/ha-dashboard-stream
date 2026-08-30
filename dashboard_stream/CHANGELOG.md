# Changelog

All notable changes to the **Dashboard Stream Cam** app are documented in
this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

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
