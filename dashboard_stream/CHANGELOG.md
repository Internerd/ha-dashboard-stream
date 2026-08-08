# Changelog

All notable changes to the **Dashboard Stream Cam** app are documented in
this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

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
