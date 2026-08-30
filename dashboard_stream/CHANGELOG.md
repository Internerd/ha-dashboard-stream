# Changelog

All notable changes to the **Dashboard Stream Cam** app are documented in
this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

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
