#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

ENV_FILE="/data/dashboard-stream.env"
SELECTION_FILE="/data/dashboard_selection.json"
MEDIAMTX_CONF="/etc/mediamtx.yml"

# ---------------------------------------------------------------------------
# Read options
# ---------------------------------------------------------------------------
HA_URL="$(bashio::config 'ha_url')"
HA_TOKEN="$(bashio::config 'ha_long_lived_token')"
DASHBOARD_PATH="$(bashio::config 'dashboard_path')"
DASHBOARD_CUSTOM_URL="$(bashio::config 'dashboard_custom_url')"
RESOLUTION="$(bashio::config 'resolution')"
FRAMERATE="$(bashio::config 'framerate')"
RENDER_WAIT="$(bashio::config 'render_wait')"
RELOAD_INTERVAL="$(bashio::config 'reload_interval')"
RTSP_PORT="$(bashio::config 'rtsp_port')"
ONVIF_PORT="$(bashio::config 'onvif_port')"
ONVIF_ENABLED="$(bashio::config 'onvif_enabled')"
ONVIF_DEVICE_NAME="$(bashio::config 'onvif_device_name')"
STREAM_USERNAME="$(bashio::config 'stream_username')"
STREAM_PASSWORD="$(bashio::config 'stream_password')"
WATCHDOG_INTERVAL="$(bashio::config 'watchdog_interval')"
STALL_TIMEOUT="$(bashio::config 'stall_timeout')"
LOG_LEVEL="$(bashio::config 'log_level')"

# ---------------------------------------------------------------------------
# Fail fast on unsafe configuration rather than starting an unauthenticated
# stream. This mirrors what UniFi Protect itself requires for third-party
# ONVIF/RTSP cameras: a username and a non-empty password.
# ---------------------------------------------------------------------------
if bashio::var.is_empty "${STREAM_PASSWORD}"; then
    bashio::log.fatal "stream_password is empty. Refusing to start an unauthenticated" \
        "camera stream. Set 'stream_username' and 'stream_password' in the app" \
        "configuration and restart the app."
    bashio::exit.nok
fi

if bashio::var.is_empty "${STREAM_USERNAME}"; then
    bashio::log.fatal "stream_username is empty. Set a username in the app configuration."
    bashio::exit.nok
fi

if ! [[ "${RESOLUTION}" =~ ^[0-9]+x[0-9]+$ ]]; then
    bashio::log.fatal "resolution '${RESOLUTION}' is not in WIDTHxHEIGHT form."
    bashio::exit.nok
fi
WIDTH="${RESOLUTION%x*}"
HEIGHT="${RESOLUTION#*x}"

# ---------------------------------------------------------------------------
# Resolve the dashboard URL to render.
# Priority: dashboard_custom_url > dashboard picked via the web panel
# (/data/dashboard_selection.json) > dashboard_path option.
# ---------------------------------------------------------------------------
TARGET_PATH="${DASHBOARD_PATH}"
if [[ -f "${SELECTION_FILE}" ]]; then
    SELECTED="$(jq -r '.path // empty' "${SELECTION_FILE}" 2>/dev/null || true)"
    if [[ -n "${SELECTED}" ]]; then
        TARGET_PATH="${SELECTED}"
        bashio::log.info "Using dashboard selected via the web panel: ${TARGET_PATH}"
    fi
fi

if bashio::var.is_empty "${DASHBOARD_CUSTOM_URL}"; then
    DASHBOARD_URL="${HA_URL%/}/${TARGET_PATH#/}?kiosk"
else
    DASHBOARD_URL="${DASHBOARD_CUSTOM_URL}"
    bashio::log.info "dashboard_custom_url set, overriding dashboard_path."
fi

# ---------------------------------------------------------------------------
# Persist resolved settings for the s6 services and the Python app.
# ---------------------------------------------------------------------------
umask 077
cat > "${ENV_FILE}" <<EOF
export HA_URL="${HA_URL}"
export HA_TOKEN="${HA_TOKEN}"
export DASHBOARD_URL="${DASHBOARD_URL}"
export DASHBOARD_PATH="${TARGET_PATH}"
export STREAM_WIDTH="${WIDTH}"
export STREAM_HEIGHT="${HEIGHT}"
export STREAM_FRAMERATE="${FRAMERATE}"
export RENDER_WAIT="${RENDER_WAIT}"
export RELOAD_INTERVAL="${RELOAD_INTERVAL}"
export RTSP_PORT="${RTSP_PORT}"
export ONVIF_PORT="${ONVIF_PORT}"
export ONVIF_ENABLED="${ONVIF_ENABLED}"
export ONVIF_DEVICE_NAME="${ONVIF_DEVICE_NAME}"
export STREAM_USERNAME="${STREAM_USERNAME}"
export STREAM_PASSWORD="${STREAM_PASSWORD}"
export WATCHDOG_INTERVAL="${WATCHDOG_INTERVAL}"
export STALL_TIMEOUT="${STALL_TIMEOUT}"
export LOG_LEVEL="${LOG_LEVEL}"
export DISPLAY=":99"
export DBUS_SYSTEM_BUS_ADDRESS="unix:path=/run/dbus/system_bus_socket"
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/dbus/session_bus_socket"
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-}"
EOF
chmod 600 "${ENV_FILE}"

bashio::log.info "Dashboard target: ${DASHBOARD_URL}"

# ---------------------------------------------------------------------------
# Render mediamtx.yml (RTSP server config with per-path Basic Auth).
# ---------------------------------------------------------------------------
cat > "${MEDIAMTX_CONF}" <<EOF
# Auto-generated at container start - do not edit, edit the app options instead.
logLevel: warn
rtspAddress: :${RTSP_PORT}
rtmp: no
hls: no
webrtc: no
srt: no
api: no
metrics: no
pprof: no
authInternalUsers:
  # The real stream credentials (configured in the app options): read-only,
  # reachable from the LAN - this is what UniFi Protect / VLC / etc. use.
  - user: ${STREAM_USERNAME}
    pass: ${STREAM_PASSWORD}
    ips: []
    permissions:
      - action: read
        path: stream
  # A fixed, non-secret credential used only by this app's own ffmpeg
  # process to publish the captured video. Restricted to loopback so it
  # is meaningless if ever observed (e.g. via "ps") from outside the
  # container's own network namespace.
  - user: publisher
    pass: internal-publish-only
    ips: ["127.0.0.1"]
    permissions:
      - action: publish
        path: stream
paths:
  stream:
    source: publisher
EOF

bashio::log.info "Configuration ready (log_level=${LOG_LEVEL}, onvif_enabled=${ONVIF_ENABLED})."
