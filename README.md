# Dashboard Stream Cam - Home Assistant App Repository

Turn any Home Assistant dashboard into an authenticated network camera:
an RTSP stream plus an ONVIF device/media service with WS-Discovery, so
NVR software - most notably **UniFi Protect**, which supports adding
third-party cameras via ONVIF/RTSP - can add it exactly like a physical
camera.

![Supports amd64 Architecture][amd64-shield]
![Supports aarch64 Architecture][aarch64-shield]

[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg

## What this is

This repository is a **Home Assistant App repository** (Home Assistant
renamed "Add-ons" to "Apps" in Supervisor 2026.07; both terms refer to the
same mechanism and this repository installs the same way either way). It
contains one app, [`dashboard_stream/`](./dashboard_stream), which:

- renders a dashboard you pick headlessly (Chromium in a virtual
  framebuffer),
- captures and encodes it (ffmpeg) to an RTSP stream (mediamtx),
- exposes a minimal ONVIF device/media service and WS-Discovery responder
  so it is discoverable and addable as a third-party ONVIF camera,
- gates the stream, ONVIF service and snapshot endpoint behind a
  username/password, matching what UniFi Protect expects when adding a
  third-party camera,
- lets you pick which dashboard is streamed from a live dropdown in the
  app's own web panel,
- supervises itself at three levels (per-process s6 supervision, an
  internal render-hang watchdog, and a Supervisor-level container
  watchdog) and logs everything to the app's Log tab.

Full setup instructions, the UniFi Protect integration walkthrough, the
configuration reference and troubleshooting live in
[`dashboard_stream/DOCS.md`](./dashboard_stream/DOCS.md) (also shown in
the app's **Documentation** tab once installed).

## Installation

1. In Home Assistant: **Settings &rarr; Add-ons &rarr; Add-on Store**
   (or **Settings &rarr; Apps &rarr; App Store**) &rarr; the three-dot
   menu &rarr; **Repositories**.
2. Add this repository's URL:
   `https://github.com/internerd/ha-dashboard-stream`
3. Install **Dashboard Stream Cam** from the store. It builds directly
   from this repository's Dockerfile - no external container registry,
   account, or extra setup is required beyond adding the URL above.

## Repository layout

```
repository.yaml          Home Assistant app-repository manifest
dashboard_stream/        the app itself (config.yaml, Dockerfile, app code, docs)
.github/                 issue/PR templates, CI
AI_POLICY.md             how AI assistance was used in this repository, and the
                          contribution policy around it
NOTICE.md                third-party software, specs and trademark attributions
SECURITY.md              supported versions, vulnerability reporting, and the
                          security trade-offs this app makes (read before deploying)
PRIVACY.md               privacy policy / Datenschutzerklärung (EN + DE)
DISCLAIMER.md            liability disclaimer / Haftungsausschluss (EN + DE)
LICENSE                  Apache License 2.0
```

## Legal & attribution

- Licensed under the [Apache License 2.0](./LICENSE).
- Third-party components, specifications and trademarks used by this
  project are listed with attribution in [NOTICE.md](./NOTICE.md).
- This project is **not affiliated with, endorsed by, or sponsored by**
  the Open Home Foundation / Home Assistant, Ubiquiti Inc. (UniFi
  Protect), ONVIF, or Google (Chromium). Product names are used solely to
  describe compatibility. See [DISCLAIMER.md](./DISCLAIMER.md) for the
  full liability disclaimer, including the legal considerations that
  apply if your dashboard embeds real camera feeds that end up recorded
  by your NVR software.
- This is self-hosted software with no telemetry, analytics, or
  phone-home of any kind - see [PRIVACY.md](./PRIVACY.md) (bilingual
  EN/DE) for exactly what data the app touches and who is the GDPR/DSGVO
  data controller for your own instance (you are).
- See [AI_POLICY.md](./AI_POLICY.md) for how generative AI was used to
  produce this repository's code and documentation, and the policy for
  AI-assisted contributions.

### A note on HACS

This repository is **not**, and cannot be, a [HACS](https://hacs.xyz)
repository. HACS only distributes `integration`, `plugin` (Lovelace
frontend), `theme`, `python_script`, `appdaemon` and `template`
repositories - Supervisor Add-ons/Apps like this one are explicitly a
different, separate mechanism with their own store, which is exactly
what `repository.yaml` and `dashboard_stream/config.yaml` in this repo
implement (see **Installation** above). Adding a `hacs.json` here
wouldn't do anything - HACS would refuse to index this repository. If a
future version of this project ships a companion Lovelace card or
integration, *that* piece could reasonably become HACS-installable, and
would get its own `hacs.json` at that point.

## Contributing

Bug reports, feature requests and pull requests are welcome - see
[CONTRIBUTING.md](./CONTRIBUTING.md), [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)
and [AI_POLICY.md](./AI_POLICY.md) first.
