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
COLOR_SCHEME="$(bashio::config 'color_scheme')"
RENDER_WAIT="$(bashio::config 'render_wait')"
RELOAD_INTERVAL="$(bashio::config 'reload_interval')"
RTSP_PORT="$(bashio::config 'rtsp_port')"
ONVIF_PORT="$(bashio::config 'onvif_port')"
ONVIF_EXTRA_PORT="$(bashio::config 'onvif_extra_port')"
ONVIF_ENABLED="$(bashio::config 'onvif_enabled')"
ONVIF_DEVICE_NAME="$(bashio::config 'onvif_device_name')"
ADVERTISE_IP="$(bashio::config 'advertise_ip')"
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

# mediamtx only accepts these characters in its internal credentials
# (internal/conf/credential.go). Anything else makes it refuse to start, which
# would leave the app without an RTSP server at all - so say so here, with the
# allowed set, instead of failing later and out of sight.
# In a bracket expression "]" has to come first to be literal and "-" last.
MTX_CREDENTIAL_CHARS='^[]a-zA-Z0-9!$()*+.;<=>[^_{}@#&-]+$'
if ! [[ "${STREAM_USERNAME}" =~ ${MTX_CREDENTIAL_CHARS} ]]; then
    bashio::log.fatal "stream_username contains characters the RTSP server cannot use." \
        "Allowed: letters, digits and ! \$ ( ) * + . ; < = > [ ] ^ _ - { } @ # &" \
        "(no spaces, colons, slashes or quotes)."
    bashio::exit.nok
fi
if ! [[ "${STREAM_PASSWORD}" =~ ${MTX_CREDENTIAL_CHARS} ]]; then
    bashio::log.fatal "stream_password contains characters the RTSP server cannot use." \
        "Allowed: letters, digits and ! \$ ( ) * + . ; < = > [ ] ^ _ - { } @ # &" \
        "(no spaces, colons, slashes or quotes). Pick a different password - a" \
        "long alphanumeric one is both safe and accepted by every NVR."
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
# Every value is written with %q so that a password, token or device name
# containing a quote, $, backtick or space cannot break this file or be
# re-expanded when a service sources it.
: > "${ENV_FILE}"
write_env() {
    printf 'export %s=%q\n' "$1" "$2" >> "${ENV_FILE}"
}
write_env HA_URL "${HA_URL}"
write_env HA_TOKEN "${HA_TOKEN}"
write_env DASHBOARD_URL "${DASHBOARD_URL}"
write_env DASHBOARD_PATH "${TARGET_PATH}"
write_env STREAM_WIDTH "${WIDTH}"
write_env STREAM_HEIGHT "${HEIGHT}"
write_env STREAM_FRAMERATE "${FRAMERATE}"
write_env COLOR_SCHEME "${COLOR_SCHEME}"
write_env RENDER_WAIT "${RENDER_WAIT}"
write_env RELOAD_INTERVAL "${RELOAD_INTERVAL}"
write_env RTSP_PORT "${RTSP_PORT}"
write_env ONVIF_PORT "${ONVIF_PORT}"
write_env ONVIF_EXTRA_PORT "${ONVIF_EXTRA_PORT}"
write_env ONVIF_ENABLED "${ONVIF_ENABLED}"
write_env ONVIF_DEVICE_NAME "${ONVIF_DEVICE_NAME}"
write_env ADVERTISE_IP "${ADVERTISE_IP}"
write_env STREAM_USERNAME "${STREAM_USERNAME}"
write_env STREAM_PASSWORD "${STREAM_PASSWORD}"
write_env WATCHDOG_INTERVAL "${WATCHDOG_INTERVAL}"
write_env STALL_TIMEOUT "${STALL_TIMEOUT}"
write_env LOG_LEVEL "${LOG_LEVEL}"
write_env DISPLAY ":99"
write_env DBUS_SYSTEM_BUS_ADDRESS "unix:path=/run/dbus/system_bus_socket"
write_env DBUS_SESSION_BUS_ADDRESS "unix:path=/run/dbus/session_bus_socket"
write_env SUPERVISOR_TOKEN "${SUPERVISOR_TOKEN:-}"
chmod 600 "${ENV_FILE}"

bashio::log.info "Dashboard target: ${DASHBOARD_URL}"

# ---------------------------------------------------------------------------
# Render mediamtx.yml (RTSP server config with per-path Basic Auth).
# ---------------------------------------------------------------------------
# mediamtx's own level: at "info" it logs every connection attempt and every
# failed authentication with the peer address, which is what tells you whether
# an NVR reached the stream at all. That is worth having by default.
case "${LOG_LEVEL}" in
    debug|info) MTX_LOG_LEVEL="info" ;;
    warning)    MTX_LOG_LEVEL="warn" ;;
    *)          MTX_LOG_LEVEL="error" ;;
esac

# Credentials are quoted as JSON (a subset of YAML) so that a password
# containing #, quotes, @ or * cannot be truncated, re-interpreted or reject
# the whole config - all of which look like "invalid credentials" to an NVR.
STREAM_USERNAME_YAML="$(jq -Rn --arg v "${STREAM_USERNAME}" '$v')"
STREAM_PASSWORD_YAML="$(jq -Rn --arg v "${STREAM_PASSWORD}" '$v')"

cat > "${MEDIAMTX_CONF}" <<EOF
# Auto-generated at container start - do not edit, edit the app options instead.
logLevel: ${MTX_LOG_LEVEL}
rtspAddress: :${RTSP_PORT}
# mediamtx offers only Basic by default, but NVRs (UniFi Protect among them)
# commonly authenticate with Digest and simply report "invalid credentials"
# when the server does not offer it. Offer both; over plain RTSP neither is
# more confidential than the other.
rtspAuthMethods: [basic, digest]
rtmp: no
hls: no
webrtc: no
srt: no
# New in mediamtx 1.20 and on by default: it would open further host ports
# (8892/8893) this app has no use for.
moq: no
api: no
metrics: no
pprof: no
authInternalUsers:
  # The real stream credentials (configured in the app options): read-only,
  # reachable from the LAN - this is what UniFi Protect / VLC / etc. use.
  - user: ${STREAM_USERNAME_YAML}
    pass: ${STREAM_PASSWORD_YAML}
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
