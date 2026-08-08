"""Dashboard Stream Cam - main application process.

Runs two aiohttp web services in one process:

- a *public* service on 0.0.0.0:<onvif_port> exposing the ONVIF
  device/media SOAP endpoints, a JPEG snapshot endpoint and a bare
  liveness endpoint - these are meant to be reached from the LAN (e.g. by
  UniFi Protect) and are gated by the same username/password as the RTSP
  stream;
- an *ingress* service bound only to 127.0.0.1:<ingress_port>, reachable
  exclusively through Home Assistant's authenticated Supervisor ingress
  proxy, serving the dashboard picker panel. It is intentionally not
  exposed on the LAN even though the app runs with host networking - see
  SECURITY.md.

It also owns three background loops: the browser supervisor (login
bootstrap, periodic reload, hang detection + forced restart), the JPEG
snapshot capture loop, and the WS-Discovery responder.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import aiohttp
from aiohttp import web

import browser
import onvif
from config import Settings, load_settings

logger = logging.getLogger("dashboard_stream.server")

SELECTION_FILE = "/data/dashboard_selection.json"
SNAPSHOT_PATH = "/data/snapshot.jpg"
INDEX_HTML = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def build_dashboard_url(settings: Settings, path: str) -> str:
    path = (path or "").strip()
    base = settings.ha_url if not path else f"{settings.ha_url}/{path.lstrip('/')}"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}kiosk"


def load_selection() -> str | None:
    try:
        with open(SELECTION_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("path")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def save_selection(path: str) -> None:
    tmp = SELECTION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"path": path}, fh)
    os.replace(tmp, SELECTION_FILE)


def kill_chromium() -> None:
    subprocess.run(["pkill", "-f", "remote-debugging-port=9222"], check=False)  # noqa: S603,S607


class BrowserSupervisor:
    """Owns the lifecycle of the (externally s6-supervised) Chromium
    process from the application side: signs it in, points it at the
    right dashboard, reloads it periodically, and force-kills it if it
    stops responding to CDP so that s6 brings up a fresh one."""

    def __init__(self, session: aiohttp.ClientSession, settings: Settings, initial_url: str):
        self.session = session
        self.settings = settings
        self.current_url = initial_url
        self.last_ok = False

    async def set_dashboard(self, url: str) -> None:
        self.current_url = url
        await browser.navigate(self.session, self.settings.cdp_port, url)

    async def run(self) -> None:
        s = self.settings
        while True:
            ready = await browser.wait_for_cdp_ready(self.session, s.cdp_port)
            if not ready:
                await asyncio.sleep(2)
                continue

            logger.info("Chromium is up, bootstrapping session")
            if s.ha_token:
                try:
                    await browser.inject_login(self.session, s.cdp_port, s.ha_url, s.ha_token)
                    logger.info("Injected Home Assistant long-lived access token into browser session")
                except browser.BrowserError:
                    logger.warning("Failed to inject login token", exc_info=True)
            try:
                await browser.navigate(self.session, s.cdp_port, self.current_url)
            except browser.BrowserError:
                logger.warning("Failed to navigate to %s", self.current_url, exc_info=True)

            await self._supervise_until_hung()
            logger.error("Chromium appears hung (no CDP response) - forcing restart")
            kill_chromium()

    async def _supervise_until_hung(self) -> None:
        s = self.settings
        fail_count = 0
        max_fail = max(1, s.stall_timeout // max(1, s.watchdog_interval))
        last_reload = time.monotonic()
        while True:
            await asyncio.sleep(s.watchdog_interval)
            ok = await browser.ping(self.session, s.cdp_port)
            self.last_ok = ok
            if not ok:
                fail_count += 1
                logger.warning("Chromium CDP unresponsive (%s/%s)", fail_count, max_fail)
                if fail_count >= max_fail:
                    return
                continue
            fail_count = 0
            if s.reload_interval and (time.monotonic() - last_reload) >= s.reload_interval:
                try:
                    await browser.reload_page(self.session, s.cdp_port)
                    last_reload = time.monotonic()
                    logger.info("Periodic dashboard reload")
                except browser.BrowserError:
                    logger.warning("Periodic reload failed", exc_info=True)


async def snapshot_loop(settings: Settings, interval: int = 10) -> None:
    tmp_path = SNAPSHOT_PATH + ".tmp"
    size = f"{settings.stream_width}x{settings.stream_height}"
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-f", "x11grab", "-video_size", size, "-i", ":99",
                "-vframes", "1", "-q:v", "5", tmp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode == 0 and os.path.exists(tmp_path):
                os.replace(tmp_path, SNAPSHOT_PATH)
        except OSError:
            logger.debug("snapshot capture failed", exc_info=True)
        await asyncio.sleep(interval)


def check_basic_auth(request: web.Request, username: str, password: str) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user, _, pwd = decoded.partition(":")
    return user == username and pwd == password


# ---------------------------------------------------------------------------
# Public (LAN-reachable) handlers: ONVIF, snapshot, health
# ---------------------------------------------------------------------------

async def handle_onvif(request: web.Request) -> web.Response:
    ctx: onvif.OnvifContext = request.app["onvif_ctx"]
    if not ctx.settings.onvif_enabled:
        return web.Response(status=404, text="ONVIF is disabled in the app configuration.")
    body = await request.read()
    try:
        xml_response = onvif.handle_soap_request(body, ctx)
        return web.Response(text=xml_response, content_type="application/soap+xml")
    except onvif.OnvifError as err:
        return web.Response(text=onvif.soap_fault(err), content_type="application/soap+xml", status=err.http_status)


async def handle_snapshot(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    if not check_basic_auth(request, settings.stream_username, settings.stream_password):
        return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="dashboard-stream"'})
    if not os.path.exists(SNAPSHOT_PATH):
        return web.Response(status=503, text="Snapshot not ready yet, try again shortly.")
    return web.FileResponse(SNAPSHOT_PATH)


async def handle_health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


# ---------------------------------------------------------------------------
# Ingress (loopback-only) handlers: dashboard picker panel
# ---------------------------------------------------------------------------

async def handle_index(_request: web.Request) -> web.Response:
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def handle_dashboards(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    session: aiohttp.ClientSession = request.app["http_session"]
    dashboards = [{"path": "", "title": "Default dashboard (Overview)"}]
    if settings.supervisor_token:
        try:
            async with session.get(
                "http://supervisor/core/api/lovelace/dashboards",
                headers={"Authorization": f"Bearer {settings.supervisor_token}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    for item in await resp.json():
                        url_path = item.get("url_path")
                        if url_path:
                            dashboards.append({"path": url_path, "title": item.get("title") or url_path})
                else:
                    logger.warning("Home Assistant API returned HTTP %s for dashboard list", resp.status)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("Could not reach Home Assistant to list dashboards", exc_info=True)

    configured = settings.dashboard_path.strip()
    if configured and not any(d["path"] == configured for d in dashboards):
        dashboards.append({"path": configured, "title": f"Configured default ({configured})"})
    current = load_selection()
    if current is None:
        current = settings.dashboard_path
    return web.json_response({"dashboards": dashboards, "current": current})


async def handle_select(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    path = str(data.get("path", "")).strip()
    if len(path) > 200 or "\n" in path or "\r" in path:
        return web.json_response({"ok": False, "error": "invalid dashboard path"}, status=400)

    save_selection(path)
    url = build_dashboard_url(settings, path)
    supervisor: BrowserSupervisor = request.app["browser_supervisor"]
    try:
        await supervisor.set_dashboard(url)
    except browser.BrowserError:
        logger.warning("Could not switch the live browser to %s, it will apply on next restart", url, exc_info=True)
    return web.json_response({"ok": True, "url": url})


async def handle_status(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    ctx: onvif.OnvifContext = request.app["onvif_ctx"]
    supervisor: BrowserSupervisor = request.app["browser_supervisor"]
    return web.json_response(
        {
            "dashboard_url": supervisor.current_url,
            "browser_ok": supervisor.last_ok,
            "onvif_enabled": settings.onvif_enabled,
            "local_ip": ctx.local_ip,
            "onvif_device_name": settings.onvif_device_name,
            "rtsp_url": f"rtsp://{ctx.local_ip}:{settings.rtsp_port}/stream",
            "onvif_device_service": f"http://{ctx.local_ip}:{settings.onvif_port}/onvif/device_service",
            "snapshot_url": f"http://{ctx.local_ip}:{settings.onvif_port}/snapshot.jpg",
            "stream_username": settings.stream_username,
        }
    )


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    logger.info("Dashboard Stream Cam starting")

    device_uuid = onvif.get_or_create_device_uuid()
    local_ip = onvif.get_local_ip()
    ctx = onvif.OnvifContext(settings=settings, local_ip=local_ip, device_uuid=device_uuid)
    logger.info("Detected LAN address: %s (verify this in a multi-NIC host)", local_ip)

    http_session = aiohttp.ClientSession()

    initial_path = load_selection()
    if initial_path is None:
        initial_path = settings.dashboard_path
    initial_url = settings.dashboard_url or build_dashboard_url(settings, initial_path)
    supervisor = BrowserSupervisor(http_session, settings, initial_url)

    public_app = web.Application()
    public_app["settings"] = settings
    public_app["onvif_ctx"] = ctx
    public_app.router.add_post("/onvif/device_service", handle_onvif)
    public_app.router.add_post("/onvif/media_service", handle_onvif)
    public_app.router.add_get("/snapshot.jpg", handle_snapshot)
    public_app.router.add_get("/health", handle_health)

    ingress_app = web.Application()
    ingress_app["settings"] = settings
    ingress_app["onvif_ctx"] = ctx
    ingress_app["http_session"] = http_session
    ingress_app["browser_supervisor"] = supervisor
    ingress_app.router.add_get("/", handle_index)
    ingress_app.router.add_get("/api/dashboards", handle_dashboards)
    ingress_app.router.add_post("/api/select", handle_select)
    ingress_app.router.add_get("/api/status", handle_status)
    ingress_app.router.add_get("/health", handle_health)

    public_runner = web.AppRunner(public_app)
    await public_runner.setup()
    await web.TCPSite(public_runner, "0.0.0.0", settings.onvif_port).start()  # noqa: S104 - intentionally LAN-reachable, see SECURITY.md
    logger.info("Public ONVIF/snapshot service on 0.0.0.0:%s", settings.onvif_port)

    ingress_runner = web.AppRunner(ingress_app)
    await ingress_runner.setup()
    await web.TCPSite(ingress_runner, "127.0.0.1", settings.ingress_port).start()
    logger.info("Ingress dashboard-picker panel on 127.0.0.1:%s (Supervisor-proxied only)", settings.ingress_port)

    tasks = [
        asyncio.create_task(supervisor.run()),
        asyncio.create_task(snapshot_loop(settings)),
    ]
    if settings.onvif_enabled:
        tasks.append(asyncio.create_task(onvif.run_ws_discovery(ctx)))
    else:
        logger.info("ONVIF/WS-Discovery disabled by configuration; RTSP stream is still active.")

    try:
        await asyncio.gather(*tasks)
    finally:
        await http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
