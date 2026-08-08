ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

ARG TARGETARCH
ARG MEDIAMTX_VERSION=v1.20.0

ENV \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    S6_KEEP_ENV=1

# ---------------------------------------------------------------------------
# System packages
#   chromium/ttf-freefont/mesa-*: headless dashboard rendering
#   xvfb: virtual framebuffer chromium renders into
#   ffmpeg: X11 capture -> H.264 encode, and periodic JPEG snapshots
#   python3/py3-pip: the app itself (aiohttp server, ONVIF, WS-Discovery)
# ---------------------------------------------------------------------------
RUN \
    apk add --no-cache \
        chromium \
        ttf-freefont \
        font-noto-emoji \
        mesa-gl \
        mesa-dri-swrast \
        xvfb \
        dbus \
        ffmpeg \
        python3 \
        py3-pip \
        tzdata \
        jq \
        curl \
        tar

# ---------------------------------------------------------------------------
# mediamtx: lightweight RTSP server the browser capture is published to.
# See NOTICE.md for licensing/attribution.
# ---------------------------------------------------------------------------
RUN \
    set -eux; \
    if [ -z "${TARGETARCH}" ]; then \
        echo "TARGETARCH is not set, please build with Docker BuildKit." && exit 1; \
    fi; \
    case "${TARGETARCH}" in \
        amd64) MEDIAMTX_ARCH="amd64" ;; \
        arm64) MEDIAMTX_ARCH="arm64" ;; \
        *) echo "Unsupported TARGETARCH: ${TARGETARCH}" && exit 1 ;; \
    esac; \
    curl -sSLf -o /tmp/mediamtx.tar.gz \
        "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_${MEDIAMTX_ARCH}.tar.gz"; \
    mkdir -p /tmp/mediamtx; \
    tar -xzf /tmp/mediamtx.tar.gz -C /tmp/mediamtx; \
    install -m 755 /tmp/mediamtx/mediamtx /usr/bin/mediamtx; \
    rm -rf /tmp/mediamtx /tmp/mediamtx.tar.gz

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
COPY app /opt/app
RUN pip install --no-cache-dir --break-system-packages -r /opt/app/requirements.txt

# Root filesystem (s6-overlay services, init scripts)
COPY rootfs /

RUN \
    chmod a+x \
        /etc/cont-init.d/*.sh \
        /etc/services.d/*/run \
        /etc/services.d/*/finish

LABEL \
    io.hass.name="Dashboard Stream Cam" \
    io.hass.description="Home Assistant dashboard rendered as an authenticated ONVIF/RTSP camera stream" \
    io.hass.type="addon" \
    io.hass.arch="amd64|aarch64" \
    org.opencontainers.image.title="Dashboard Stream Cam" \
    org.opencontainers.image.description="Home Assistant dashboard rendered as an authenticated ONVIF/RTSP camera stream" \
    org.opencontainers.image.source="https://github.com/internerd/ha-dashboard-stream" \
    org.opencontainers.image.licenses="Apache-2.0"
