"""Minimal Chrome DevTools Protocol (CDP) client.

Used to auto-sign the kiosk browser into Home Assistant via a Long-Lived
Access Token, to reload the page periodically, and to switch the rendered
dashboard live (from the ingress panel) without restarting the container.

A short-lived websocket connection is opened per call instead of keeping a
long-running one, since calls here are infrequent (startup, periodic
reload, health pings, on-demand dashboard switch).
"""
from __future__ import annotations

import asyncio
import json
import logging

import aiohttp

logger = logging.getLogger("dashboard_stream.browser")


class BrowserError(Exception):
    """Raised when the browser cannot be reached or a CDP call fails."""


async def _get_ws_url(session: aiohttp.ClientSession, cdp_port: int) -> str:
    url = f"http://127.0.0.1:{cdp_port}/json"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        if resp.status != 200:
            raise BrowserError(f"CDP target list returned HTTP {resp.status}")
        targets = await resp.json(content_type=None)
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise BrowserError("no CDP page target found")
    return pages[0]["webSocketDebuggerUrl"]


async def cdp_call(
    session: aiohttp.ClientSession,
    cdp_port: int,
    method: str,
    params: dict | None = None,
    timeout: float = 10,
) -> dict:
    async def _do_call() -> dict:
        ws_url = await _get_ws_url(session, cdp_port)
        async with session.ws_connect(ws_url) as ws:
            await ws.send_json({"id": 1, "method": method, "params": params or {}})
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("id") == 1:
                        if "error" in data:
                            raise BrowserError(str(data["error"]))
                        return data.get("result", {})
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        raise BrowserError("CDP connection closed without a response")

    return await asyncio.wait_for(_do_call(), timeout=timeout)


async def wait_for_cdp_ready(
    session: aiohttp.ClientSession, cdp_port: int, attempts: int = 90, delay: float = 1
) -> bool:
    for _ in range(attempts):
        try:
            await _get_ws_url(session, cdp_port)
            return True
        except Exception:  # noqa: BLE001 - best-effort readiness probe
            await asyncio.sleep(delay)
    return False


def build_login_js(ha_url: str, token: str) -> str:
    """JS that plants a Home Assistant frontend auth token in localStorage.

    This mirrors the format the frontend itself writes to
    localStorage['hassTokens'] after a normal login, letting the kiosk
    browser start authenticated without a human ever typing a password.
    See DOCS.md for why this is used instead of (or alongside) Trusted
    Networks.
    """
    payload = {
        "hassUrl": ha_url,
        "clientId": ha_url + "/",
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 315360000,
        "refresh_token": token,
        "expires": 99999999999999,
    }
    return "localStorage.setItem('hassTokens', JSON.stringify(%s));" % json.dumps(payload)


async def inject_login(session: aiohttp.ClientSession, cdp_port: int, ha_url: str, token: str) -> None:
    await cdp_call(session, cdp_port, "Runtime.evaluate", {"expression": build_login_js(ha_url, token)})


async def navigate(session: aiohttp.ClientSession, cdp_port: int, url: str) -> None:
    await cdp_call(session, cdp_port, "Page.navigate", {"url": url})


async def reload_page(session: aiohttp.ClientSession, cdp_port: int, ignore_cache: bool = True) -> None:
    await cdp_call(session, cdp_port, "Page.reload", {"ignoreCache": ignore_cache})


async def describe_page(session: aiohttp.ClientSession, cdp_port: int, timeout: float = 10) -> dict:
    """What the kiosk is actually showing - the stream is a picture of this.

    A blank page produces a uniform image that looks like a broken stream on
    an NVR, so it is worth stating in the log rather than leaving to guesswork.
    """
    expression = (
        "JSON.stringify({"
        "url: location.href,"
        "title: document.title,"
        "state: document.readyState,"
        "characters: document.body ? document.body.innerText.trim().length : 0,"
        "elements: document.body ? document.body.getElementsByTagName('*').length : 0"
        "})"
    )
    result = await cdp_call(
        session, cdp_port, "Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=timeout
    )
    value = result.get("result", {}).get("value")
    if not value:
        raise BrowserError("page returned no description")
    return json.loads(value)


async def ping(session: aiohttp.ClientSession, cdp_port: int, timeout: float = 5) -> bool:
    try:
        result = await cdp_call(
            session,
            cdp_port,
            "Runtime.evaluate",
            {"expression": "1+1", "returnByValue": True},
            timeout=timeout,
        )
        return result.get("result", {}).get("value") == 2
    except Exception:  # noqa: BLE001 - liveness probe, any failure means "not alive"
        return False
