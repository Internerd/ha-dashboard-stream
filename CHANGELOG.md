# Changelog

This file tracks changes to the **repository** itself (structure,
policies, CI). For app behavior changes, see
[`dashboard_stream/CHANGELOG.md`](./dashboard_stream/CHANGELOG.md).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `tools/check_onvif_schema.py`: validates the app's ONVIF responses against
  the official ONVIF schema (mandatory elements and element order,
  recursively). Run it after touching `dashboard_stream/app/onvif.py`. It
  walks every combination of the `audio_track` and `substream` options, so
  responses are checked with one profile and with two.
- `tools/compare_rtsp.py`: the companion to `compare_onvif.py`, one layer
  down. For when an NVR has finished with ONVIF, holds an open RTSP session,
  and still shows no picture: it plays both streams and reports what is
  actually on the wire - SDP verbatim, RTP headers, frame rate from the RTP
  timestamps, NAL units per access unit, slices per frame, whether SPS/PPS
  are repeated in-band, the decoded sequence parameter set, and RTCP sender
  reports. It found both defects fixed in app 1.8.1. Standard library only,
  and it asks a device exactly once more after an authentication failure, so
  it cannot lock a camera account out.
- `tools/compare_onvif.py`: asks two ONVIF/RTSP devices the same questions
  and prints where their answers differ, with addresses, timestamps, serials
  and tokens normalised away. For the case where an NVR accepts one device
  and refuses another: it turns "what does the other one do differently"
  into a list instead of a guess. Standard library only, so it runs from any
  machine that can reach both devices.

- Initial repository scaffold: `repository.yaml`, the `dashboard_stream`
  app, `AI_POLICY.md`, `NOTICE.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, issue/PR templates, and a lint CI workflow.
