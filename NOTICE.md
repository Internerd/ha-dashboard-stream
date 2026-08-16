# Third-party notices & attribution

This project (the code in this repository) is licensed under the
[Apache License 2.0](./LICENSE). It builds on, packages, or is
interoperable with the third-party software, open specifications and
trademarks listed below. This file is provided for transparency and
attribution; it is not legal advice.

## Software

| Component | Role in this project | License |
| --- | --- | --- |
| [Home Assistant App/Add-on base image](https://github.com/home-assistant) (`ghcr.io/home-assistant/base`) | Container base image (Alpine Linux + s6-overlay + bashio) | Apache License 2.0 |
| [s6-overlay](https://github.com/just-containers/s6-overlay) | Process supervision (bundled in the base image) | ISC License |
| [bashio](https://github.com/hassio-addons/bashio) | Shell helper library for reading app options and logging (bundled in the base image) | Apache License 2.0 |
| [Alpine Linux](https://alpinelinux.org/) packages | Base OS and packages (`chromium`, `ffmpeg`, `xvfb`, `python3`, etc.) | Various OSI-approved licenses per package |
| [Chromium](https://www.chromium.org/) | Headless dashboard rendering (installed via the Alpine `chromium` package) | BSD-style (see Chromium's own LICENSE) |
| [FFmpeg](https://ffmpeg.org/) | Screen capture and H.264 encoding (installed via the Alpine `ffmpeg` package, which links `libx264`) | LGPL/GPL depending on build configuration; consumed as a distro package via subprocess, not linked into this project's own code |
| [mediamtx](https://github.com/bluenviron/mediamtx) | Embedded RTSP server the capture is published to | MIT License |
| [Python](https://www.python.org/) | Application runtime | PSF License |
| [aiohttp](https://github.com/aio-libs/aiohttp) | HTTP/WebSocket server and client used by the app (ONVIF service, ingress panel, CDP client) | Apache License 2.0 |

Each component remains under its own upstream license; nothing in this
repository relicenses them. See each project's repository for full
license text.

## Specifications referenced

This project implements a minimal, independent subset of the following
open specifications, for interoperability purposes only:

- **ONVIF** Core Specification and Device/Media WSDLs -
  <https://www.onvif.org/profiles/specifications/>. ONVIF&reg; and the
  ONVIF logo are trademarks of ONVIF Inc. This project is an independent
  implementation, is not certified by ONVIF, and is not affiliated with
  or endorsed by ONVIF Inc.
- **WS-Discovery** (Web Services Dynamic Discovery) -
  OASIS, <https://docs.oasis-open.org/ws-dx/ws-discovery/1.1/>.
- **WS-Security UsernameToken Profile 1.0** - OASIS,
  <https://www.oasis-open.org/committees/wss/>.

## Trademarks

Product and company names below are used solely to describe
compatibility and interoperability. Use of these names does not imply
any affiliation with, sponsorship by, or endorsement from their
respective owners.

- **Home Assistant** and the Home Assistant App/Add-on framework are
  projects of the Open Home Foundation.
- **UniFi**, **UniFi Protect** and **Ubiquiti** are trademarks of
  Ubiquiti Inc.
- **ONVIF** is a trademark of ONVIF Inc.
- **Chromium** and **Google Chrome** are trademarks of Google LLC.
- **Alpine Linux** is a trademark of the Alpine Linux project.

## Documentation licensing

[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) in this repository is adapted
from the [Contributor Covenant](https://www.contributor-covenant.org/),
version 2.1, available under the
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license.

## Related documents

This file covers third-party attribution. For the privacy policy see
[PRIVACY.md](./PRIVACY.md), and for the liability disclaimer (including
trademark disclaimers and the legal considerations around streaming real
camera footage) see [DISCLAIMER.md](./DISCLAIMER.md).

## AI-generated content

See [AI_POLICY.md](./AI_POLICY.md) for a disclosure of how generative AI
was used to produce this repository's code and documentation.

## Corrections

If you believe an attribution here is missing or incorrect, please open
an issue - see [CONTRIBUTING.md](./CONTRIBUTING.md).
