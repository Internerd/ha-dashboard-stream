# Changelog

This file tracks changes to the **repository** itself (structure,
policies, CI). For app behavior changes, see
[`dashboard_stream/CHANGELOG.md`](./dashboard_stream/CHANGELOG.md).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `tools/check_onvif_schema.py`: validates the app's ONVIF responses against
  the official ONVIF schema (mandatory elements and element order,
  recursively). Run it after touching `dashboard_stream/app/onvif.py`.
- `tools/compare_onvif.py`: asks two ONVIF/RTSP devices the same questions
  and prints where their answers differ, with addresses, timestamps, serials
  and tokens normalised away. For the case where an NVR accepts one device
  and refuses another: it turns "what does the other one do differently"
  into a list instead of a guess. Standard library only, so it runs from any
  machine that can reach both devices.

- Initial repository scaffold: `repository.yaml`, the `dashboard_stream`
  app, `AI_POLICY.md`, `NOTICE.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, issue/PR templates, and a lint CI workflow.
