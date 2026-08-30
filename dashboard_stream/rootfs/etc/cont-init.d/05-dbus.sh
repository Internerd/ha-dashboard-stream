#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Prepare the container-local D-Bus buses Chromium expects to find.
#
# Both buses are private to this container: they exist purely so Chromium's
# UPower / secret-service / session probes get a clean "no such service"
# answer instead of flooding the app log with connection errors on every
# startup and every retry.
# ---------------------------------------------------------------------------
mkdir -p /run/dbus /var/lib/dbus

# dbus-daemon refuses to start without a machine UUID.
if [[ ! -s /var/lib/dbus/machine-id ]]; then
    if command -v dbus-uuidgen > /dev/null 2>&1; then
        dbus-uuidgen --ensure=/var/lib/dbus/machine-id
    else
        od -An -N16 -tx1 /dev/urandom | tr -d ' \n' > /var/lib/dbus/machine-id
        echo >> /var/lib/dbus/machine-id
    fi
fi
if [[ ! -s /etc/machine-id ]]; then
    cp /var/lib/dbus/machine-id /etc/machine-id
fi

# Stale sockets from a previous container start would make dbus-daemon exit.
rm -f /run/dbus/system_bus_socket /run/dbus/session_bus_socket /run/dbus/pid

bashio::log.info "D-Bus runtime prepared (system + session bus sockets in /run/dbus)."
