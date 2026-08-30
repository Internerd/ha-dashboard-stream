"""Runtime configuration, loaded from the environment.

Values are exported into the process environment by
/etc/cont-init.d/10-config.sh (from /data/dashboard-stream.env), which in
turn reads them from the app's Supervisor options via bashio::config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(value: str, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    ha_url: str
    ha_token: str
    dashboard_url: str
    dashboard_path: str
    stream_width: int
    stream_height: int
    framerate: int
    render_wait: int
    reload_interval: int
    rtsp_port: int
    onvif_port: int
    onvif_enabled: bool
    onvif_device_name: str
    advertise_ip: str
    stream_username: str
    stream_password: str
    watchdog_interval: int
    stall_timeout: int
    log_level: str
    supervisor_token: str
    selection_file: str = "/data/dashboard_selection.json"
    cdp_port: int = 9222
    ingress_port: int = 8099

    @property
    def rtsp_url_public(self) -> str:
        return f"rtsp://{self.stream_username}:{self.stream_password}@[HOST]:{self.rtsp_port}/stream"


def load_settings() -> Settings:
    env = os.environ
    return Settings(
        ha_url=env.get("HA_URL", "http://homeassistant.local:8123").rstrip("/"),
        ha_token=env.get("HA_TOKEN", ""),
        dashboard_url=env.get("DASHBOARD_URL", ""),
        dashboard_path=env.get("DASHBOARD_PATH", "lovelace/default_view"),
        stream_width=_int(env.get("STREAM_WIDTH"), 1920),
        stream_height=_int(env.get("STREAM_HEIGHT"), 1080),
        framerate=_int(env.get("STREAM_FRAMERATE"), 15),
        render_wait=_int(env.get("RENDER_WAIT"), 8),
        reload_interval=_int(env.get("RELOAD_INTERVAL"), 3600),
        rtsp_port=_int(env.get("RTSP_PORT"), 8554),
        onvif_port=_int(env.get("ONVIF_PORT"), 8080),
        onvif_enabled=_bool(env.get("ONVIF_ENABLED"), True),
        onvif_device_name=env.get("ONVIF_DEVICE_NAME", "Dashboard Stream Cam"),
        advertise_ip=env.get("ADVERTISE_IP", "").strip(),
        stream_username=env.get("STREAM_USERNAME", "viewer"),
        stream_password=env.get("STREAM_PASSWORD", ""),
        watchdog_interval=_int(env.get("WATCHDOG_INTERVAL"), 15),
        stall_timeout=_int(env.get("STALL_TIMEOUT"), 45),
        log_level=env.get("LOG_LEVEL", "info"),
        supervisor_token=env.get("SUPERVISOR_TOKEN", ""),
    )
