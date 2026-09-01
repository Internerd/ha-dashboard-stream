"""Dashboard Stream Cam - main application process.

Runs two aiohttp web services in one process:

- a *public* service on 0.0.0.0:<onvif_port> exposing the ONVIF
  device/media SOAP endpoints, a JPEG snapshot endpoint and a bare
  liveness endpoint - these are meant to be reached from the LAN (e.g. by
  UniFi Protect) and are gated by the same username/password as the RTSP
  stream;
- an *ingress* service on <ingress_port> serving the dashboard picker
  panel, reachable exclusively through Home Assistant's authenticated
  Supervisor ingress proxy: every request from outside the Supervisor's
  own Docker network is refused, so the panel stays off the LAN even
  though the app runs with host networking - see SECURITY.md.

It also owns three background loops: the browser supervisor (login
bootstrap, periodic reload, hang detection + forced restart), the JPEG
snapshot capture loop, and the WS-Discovery responder.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import os
import secrets
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

# The ingress panel must be reachable by the Supervisor's ingress proxy and by
# nobody else. It cannot simply bind to loopback: this app runs with
# host_network, and for host-network apps the Supervisor connects to the
# gateway of its own Docker network ("hassio", a fixed 172.30.32.0/23) rather
# than to the app's loopback - so a loopback-bound panel is unreachable and
# Home Assistant reports the app as "not ready". It therefore binds to all
# interfaces and refuses every peer outside that network instead.
INGRESS_ALLOWED_NETWORKS = (
    ipaddress.ip_network("172.30.32.0/23"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)
# Home Assistant has no REST endpoint that lists dashboards - its own frontend
# asks over the WebSocket API, so this app does too. The Supervisor proxies
# Core's WebSocket API; "supervisor" resolves for apps on the Supervisor's
# Docker network, but this app uses host networking, where that name may not
# resolve, so the Supervisor's fixed address is tried as well. If neither
# works (e.g. the app runs against a Home Assistant that is not managed by
# this Supervisor), the configured ha_url and long-lived token are used.
SUPERVISOR_WS_URLS = (
    "http://supervisor/core/websocket",
    "http://172.30.32.2/core/websocket",
)

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

            await asyncio.sleep(s.render_wait)
            await self._report_page()
            await self._supervise_until_hung()
            logger.error("Chromium appears hung (no CDP response) - forcing restart")
            kill_chromium()

    async def _report_page(self) -> None:
        """Log what the browser ended up displaying - this is what gets streamed."""
        try:
            page = await browser.describe_page(self.session, self.settings.cdp_port)
        except (browser.BrowserError, asyncio.TimeoutError, ValueError):
            logger.warning("Could not ask the browser what it is displaying", exc_info=True)
            return
        logger.info(
            "Browser is showing %s (title %r, readyState %s, %s characters of text in %s elements "
            "across %s shadow roots)",
            page.get("url"),
            page.get("title"),
            page.get("state"),
            page.get("characters"),
            page.get("elements"),
            page.get("roots"),
        )
        if not page.get("characters") and not page.get("elements"):
            logger.warning(
                "That page has no visible text, so the stream is a blank picture. Check that "
                "ha_url is reachable from this app, that the dashboard path exists, and that "
                "the browser is signed in (ha_long_lived_token or Trusted Networks)."
            )

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
    """Keep a current JPEG of the rendered dashboard on disk for NVR thumbnails.

    The temp file is written with an explicit format: ffmpeg picks the muxer
    from the file extension, and ".jpg.tmp" is not one it recognises - it
    refuses to open the output and no snapshot is ever produced.

    Failures are reported when the state changes rather than on every pass, so
    a broken capture is visible in the log without repeating every interval.
    """
    tmp_path = SNAPSHOT_PATH + ".tmp"
    size = f"{settings.stream_width}x{settings.stream_height}"
    display = os.environ.get("DISPLAY", ":99")
    last_ok: bool | None = None
    while True:
        ok = False
        detail = ""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "x11grab", "-draw_mouse", "0", "-video_size", size, "-i", display,
                "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", tmp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            ok = proc.returncode == 0 and os.path.exists(tmp_path)
            if ok:
                os.replace(tmp_path, SNAPSHOT_PATH)
            else:
                lines = stderr.decode("utf-8", "replace").strip().splitlines()
                detail = lines[-1] if lines else f"ffmpeg exited with {proc.returncode}"
        except OSError as err:  # noqa: PERF203 - the loop must survive a failed spawn
            detail = str(err)

        if ok != last_ok:
            if ok:
                logger.info(
                    "Snapshot capture is working; /snapshot.jpg is being served (%s bytes)",
                    os.path.getsize(SNAPSHOT_PATH),
                )
            else:
                logger.warning(
                    "Snapshot capture is failing, so /snapshot.jpg answers 503 and NVR "
                    "thumbnails stay empty: %s",
                    detail,
                )
            last_ok = ok
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
    if b"PullMessages" in body:
        # A pull point is meant to block until something happens. Nothing ever
        # does here, so hold the request briefly instead of answering instantly
        # and inviting the client into a tight polling loop.
        await asyncio.sleep(2)
    try:
        xml_response = onvif.handle_soap_request(
            body, ctx, peer=request.remote or "?", service=request.path.rsplit("/", 1)[-1]
        )
        return web.Response(text=xml_response, content_type="application/soap+xml")
    except onvif.OnvifError as err:
        return web.Response(text=onvif.soap_fault(err), content_type="application/soap+xml", status=err.http_status)


async def handle_snapshot(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    ctx: onvif.OnvifContext = request.app["onvif_ctx"]
    # Either the stream credentials, or the token this device only ever hands
    # out in an authenticated GetSnapshotUri response - NVRs commonly fetch
    # that URI with a plain GET and never answer the 401.
    token = request.query.get("token", "")
    authorised = check_basic_auth(request, settings.stream_username, settings.stream_password) or (
        bool(ctx.snapshot_token) and secrets.compare_digest(token, ctx.snapshot_token)
    )
    if not authorised:
        logger.warning(
            "Snapshot request from %s refused: no valid credentials and no snapshot token. "
            "An NVR should use the URI from GetSnapshotUri, which carries the token.",
            request.remote,
        )
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


async def ws_list_dashboards(session: aiohttp.ClientSession, url: str, token: str) -> list[dict]:
    """Run the `lovelace/dashboards/list` command against one WebSocket API.

    Both the Supervisor proxy and Home Assistant itself speak the same
    handshake: the server offers `auth_required`, the client answers with an
    `auth` message carrying a token, and the server confirms with `auth_ok`.
    """
    async with session.ws_connect(url, timeout=aiohttp.ClientTimeout(total=10)) as ws:
        hello = await ws.receive_json(timeout=5)
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting {hello.get('type')!r}")

        await ws.send_json({"type": "auth", "access_token": token})
        auth = await ws.receive_json(timeout=5)
        if auth.get("type") != "auth_ok":
            raise RuntimeError(f"authentication rejected ({auth.get('type')}: {auth.get('message')})")

        await ws.send_json({"id": 1, "type": "lovelace/dashboards/list"})
        while True:
            message = await ws.receive_json(timeout=10)
            if message.get("id") != 1 or message.get("type") != "result":
                continue  # unrelated event on the same connection
            if not message.get("success"):
                raise RuntimeError(f"command failed: {message.get('error')}")
            return message.get("result") or []


async def fetch_dashboards(session: aiohttp.ClientSession, settings: Settings) -> list[dict]:
    """Ask Home Assistant for its dashboards, trying each reachable route."""
    routes: list[tuple[str, str]] = []
    if settings.supervisor_token:
        routes += [(url, settings.supervisor_token) for url in SUPERVISOR_WS_URLS]
    if settings.ha_token:
        routes.append((f"{settings.ha_url}/api/websocket", settings.ha_token))

    if not routes:
        logger.warning(
            "No way to list dashboards: neither a Supervisor token nor "
            "ha_long_lived_token is available. Set ha_long_lived_token to get "
            "the full dashboard list in the panel."
        )
        return []

    for url, token in routes:
        try:
            result = await ws_list_dashboards(session, url, token)
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError, TypeError) as err:
            logger.debug("Dashboard list via %s failed: %s", url, err)
            continue
        logger.debug("Listed %s dashboards via %s", len(result), url)
        return result

    logger.warning(
        "Could not list dashboards over any route (%s). The panel falls back to "
        "the default and the configured dashboard; run with log_level debug to "
        "see why each attempt failed.",
        ", ".join(url for url, _ in routes),
    )
    return []


async def handle_dashboards(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    session: aiohttp.ClientSession = request.app["http_session"]
    dashboards = [{"path": "", "title": "Default dashboard (Overview)"}]
    for item in await fetch_dashboards(session, settings):
        url_path = item.get("url_path")
        if url_path:
            dashboards.append({"path": url_path, "title": item.get("title") or url_path})

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


def resolve_advertised_ip(settings: Settings) -> str:
    """Decide which address ONVIF, WS-Discovery and the panel hand out.

    Every service listens on all interfaces regardless; this is only the
    address clients are *told* to connect to. Auto-detection follows the
    host's default route, which is wrong when the NVR sits on an interface
    that route does not lead to - hence the advertise_ip option.
    """
    configured = settings.advertise_ip.strip()
    if configured:
        try:
            ipaddress.ip_address(configured)
        except ValueError:
            logger.warning(
                "advertise_ip %r is not a valid IP address, falling back to auto-detection",
                configured,
            )
        else:
            logger.info("Advertising %s to ONVIF/RTSP clients (advertise_ip)", configured)
            return configured

    detected = onvif.get_local_ip()
    logger.info(
        "Advertising %s to ONVIF/RTSP clients (auto-detected from the host's default "
        "route). If your NVR cannot reach the stream at this address, set advertise_ip "
        "to the address it should use - the stream itself listens on every interface.",
        detected,
    )
    return detected


@web.middleware
async def restrict_to_supervisor(request: web.Request, handler):
    """Refuse ingress requests that do not come from the Supervisor.

    The panel has no authentication of its own - it relies entirely on Home
    Assistant having authenticated the user before proxying the request - so
    this check is what keeps it off the LAN.
    """
    peer = request.remote
    try:
        address = ipaddress.ip_address(peer)
    except (TypeError, ValueError):
        logger.warning("Refusing ingress request from unparseable peer address %r", peer)
        raise web.HTTPForbidden(text="Forbidden")

    # A dual-stack listener reports IPv4 peers as ::ffff:a.b.c.d.
    if getattr(address, "ipv4_mapped", None) is not None:
        address = address.ipv4_mapped

    if not any(address in network for network in INGRESS_ALLOWED_NETWORKS):
        logger.warning(
            "Refusing ingress request from %s: only Home Assistant's Supervisor "
            "(%s) may reach the panel",
            address,
            ", ".join(str(network) for network in INGRESS_ALLOWED_NETWORKS),
        )
        raise web.HTTPForbidden(text="Forbidden")

    return await handler(request)


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    logger.info("Dashboard Stream Cam starting")

    device_uuid = onvif.get_or_create_device_uuid()
    local_ip = resolve_advertised_ip(settings)
    ctx = onvif.OnvifContext(
        settings=settings,
        local_ip=local_ip,
        device_uuid=device_uuid,
        snapshot_token=onvif.get_or_create_snapshot_token(),
        mac_address=onvif.get_mac_address(local_ip, device_uuid),
    )

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
    # The event service and the per-subscription URLs it hands out both land
    # here; the operation inside the SOAP body decides what happens.
    public_app.router.add_post("/onvif/events_service", handle_onvif)
    public_app.router.add_get("/snapshot.jpg", handle_snapshot)
    public_app.router.add_get("/health", handle_health)

    ingress_app = web.Application(middlewares=[restrict_to_supervisor])
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

    # Some NVRs look for ONVIF on the standard HTTP port instead of asking
    # where it is - UniFi Protect's "Advanced Adoption" takes an address, not
    # a port. Serving the same app on a second port lets those find it. A port
    # already used on the host must not take the whole app down with it.
    if settings.onvif_extra_port:
        try:
            await web.TCPSite(public_runner, "0.0.0.0", settings.onvif_extra_port).start()  # noqa: S104 - same service, see SECURITY.md
        except OSError as err:
            logger.warning(
                "Could not also listen on port %s (%s). Something else on this host is "
                "using it; ONVIF stays reachable on port %s.",
                settings.onvif_extra_port,
                err,
                settings.onvif_port,
            )
        else:
            logger.info(
                "Also serving ONVIF/snapshot on 0.0.0.0:%s (onvif_extra_port)",
                settings.onvif_extra_port,
            )

    ingress_runner = web.AppRunner(ingress_app)
    await ingress_runner.setup()
    await web.TCPSite(ingress_runner, "0.0.0.0", settings.ingress_port).start()  # noqa: S104 - guarded by restrict_to_supervisor
    logger.info(
        "Ingress dashboard-picker panel on :%s (Supervisor-proxied only, other peers refused)",
        settings.ingress_port,
    )

    logger.info(
        "Listening: RTSP %s:%s | ONVIF/snapshot %s:%s%s | WS-Discovery udp/3702 | "
        "ingress panel :%s (Supervisor only). Point your NVR at %s.",
        local_ip,
        settings.rtsp_port,
        local_ip,
        settings.onvif_port,
        f" and :{settings.onvif_extra_port}" if settings.onvif_extra_port else "",
        settings.ingress_port,
        f"rtsp://{local_ip}:{settings.rtsp_port}/stream",
    )

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
