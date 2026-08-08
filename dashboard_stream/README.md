# Dashboard Stream Cam

_Turn a Home Assistant dashboard into an authenticated ONVIF/RTSP camera stream that UniFi Protect (or any NVR/ONVIF client) can add as a third-party camera._

![Supports amd64 Architecture][amd64-shield]
![Supports aarch64 Architecture][aarch64-shield]

[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg

---

Pick a dashboard from a live dropdown (backed by your actual Home
Assistant dashboards), and this app renders it headlessly and republishes
it as a normal network camera:

- **RTSP stream**, username/password protected.
- **ONVIF device/media service + WS-Discovery**, so it can be added to
  **UniFi Protect** (or any ONVIF NVR) as a third-party camera, gated by
  the same credentials.
- **JPEG snapshot endpoint** for NVR thumbnails.
- **s6 process supervision + an internal hang-detection watchdog + a
  Supervisor-level container watchdog** for unattended, auto-restarting
  operation.
- Full logging via the app's **Log** tab.

See the **Documentation** tab for setup, the UniFi Protect integration
steps, the configuration reference, and troubleshooting.
