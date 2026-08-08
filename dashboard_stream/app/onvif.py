"""A deliberately minimal ONVIF Device/Media service and WS-Discovery
responder.

This is not a general-purpose ONVIF stack. It describes exactly one fixed
video profile (this app's own rendered dashboard) so that NVR software
such as UniFi Protect can discover it, ask for its capabilities, and
retrieve the RTSP stream and JPEG snapshot URLs - all gated behind the
same username/password used for the RTSP stream itself, via WS-Security
UsernameToken (digest or plain).

Relevant specs (referenced for attribution, see NOTICE.md):
 - ONVIF Core Specification / WSDLs, https://www.onvif.org/profiles/specifications/
 - WS-Discovery, https://docs.oasis-open.org/ws-dx/ws-discovery/1.1/
 - WS-Security UsernameToken Profile 1.0, OASIS
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import logging
import socket
import struct
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from config import Settings

logger = logging.getLogger("dashboard_stream.onvif")

SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
WSSE_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
TDS_NS = "http://www.onvif.org/ver10/device/wsdl"
TRT_NS = "http://www.onvif.org/ver10/media/wsdl"
TT_NS = "http://www.onvif.org/ver10/schema"
WSD_NS = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
WSA_NS = "http://schemas.xmlsoap.org/ws/2004/08/addressing"
DN_NS = "http://www.onvif.org/ver10/network/wsdl"

MULTICAST_ADDR = "239.255.255.250"
MULTICAST_PORT = 3702


def get_local_ip() -> str:
    """Best-effort discovery of the host's LAN-facing IP address.

    Opens a UDP socket "connected" to a public address without sending any
    traffic, purely to ask the kernel which local interface/address it
    would route through. Falls back to the loopback address if that fails
    (e.g. no default route), in which case check the app's log / status
    panel and, on multi-homed hosts, verify manually.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def get_or_create_device_uuid(path: str = "/data/device_uuid") -> str:
    file = Path(path)
    if file.exists():
        value = file.read_text().strip()
        if value:
            return value
    value = str(uuid.uuid4())
    file.write_text(value)
    return value


@dataclass
class OnvifContext:
    settings: Settings
    local_ip: str
    device_uuid: str


class OnvifError(Exception):
    def __init__(self, code: str, subcode: str, reason: str, http_status: int = 500):
        super().__init__(reason)
        self.code = code
        self.subcode = subcode
        self.reason = reason
        self.http_status = http_status


class NotAuthorized(OnvifError):
    def __init__(self, reason: str = "The username or password is incorrect."):
        super().__init__("s:Sender", "ter:NotAuthorized", reason, http_status=401)


# ---------------------------------------------------------------------------
# WS-Security
# ---------------------------------------------------------------------------

def verify_security_header(envelope: ET.Element, username: str, password: str) -> bool:
    ns = {"s": SOAP_NS, "wsse": WSSE_NS, "wsu": WSU_NS}
    header = envelope.find("s:Header", ns)
    if header is None:
        return False
    security = header.find("wsse:Security", ns)
    if security is None:
        return False
    token = security.find("wsse:UsernameToken", ns)
    if token is None:
        return False
    user_el = token.find("wsse:Username", ns)
    pass_el = token.find("wsse:Password", ns)
    nonce_el = token.find("wsse:Nonce", ns)
    created_el = token.find("wsu:Created", ns)
    if user_el is None or pass_el is None:
        return False
    if (user_el.text or "") != username:
        return False
    supplied = pass_el.text or ""
    password_type = pass_el.get("Type", "")

    if nonce_el is None or created_el is None or "PasswordText" in password_type:
        # Plain-text password fallback for clients that don't implement digest auth.
        return supplied == password

    try:
        nonce_raw = base64.b64decode(nonce_el.text or "")
    except (ValueError, TypeError):
        return False
    created = created_el.text or ""
    digest_input = nonce_raw + created.encode("utf-8") + password.encode("utf-8")
    expected = base64.b64encode(hashlib.sha1(digest_input).digest()).decode("ascii")  # noqa: S324 - WS-Security mandates SHA1
    if expected != supplied:
        return False

    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            created_dt = datetime.datetime.strptime(created, fmt).replace(tzinfo=datetime.timezone.utc)
            break
        except ValueError:
            created_dt = None
    if created_dt is None:
        return True  # unparsable timestamp: digest already matched, don't hard-fail on clock format
    now = datetime.datetime.now(datetime.timezone.utc)
    return abs((now - created_dt).total_seconds()) <= 300


# ---------------------------------------------------------------------------
# SOAP envelope helpers
# ---------------------------------------------------------------------------

def soap_envelope(body_inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<s:Envelope xmlns:s="{SOAP_NS}" xmlns:tds="{TDS_NS}" xmlns:trt="{TRT_NS}" '
        f'xmlns:tt="{TT_NS}">\n  <s:Body>\n{body_inner}\n  </s:Body>\n</s:Envelope>'
    )


def soap_fault(err: OnvifError) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<s:Envelope xmlns:s="{SOAP_NS}" xmlns:ter="http://www.onvif.org/ver10/error">\n'
        "  <s:Body>\n    <s:Fault>\n"
        f"      <s:Code><s:Value>{escape(err.code)}</s:Value>"
        f"<s:Subcode><s:Value>{escape(err.subcode)}</s:Value></s:Subcode></s:Code>\n"
        f'      <s:Reason><s:Text xml:lang="en">{escape(err.reason)}</s:Text></s:Reason>\n'
        "    </s:Fault>\n  </s:Body>\n</s:Envelope>"
    )


# ---------------------------------------------------------------------------
# Device / Media operation handlers
# ---------------------------------------------------------------------------

def _get_system_date_and_time(_ctx: OnvifContext) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return f"""    <tds:GetSystemDateAndTimeResponse>
      <tds:SystemDateAndTime>
        <tt:DateTimeType>Manual</tt:DateTimeType>
        <tt:DaylightSavings>false</tt:DaylightSavings>
        <tt:UTCDateTime>
          <tt:Time><tt:Hour>{now.hour}</tt:Hour><tt:Minute>{now.minute}</tt:Minute><tt:Second>{now.second}</tt:Second></tt:Time>
          <tt:Date><tt:Year>{now.year}</tt:Year><tt:Month>{now.month}</tt:Month><tt:Day>{now.day}</tt:Day></tt:Date>
        </tt:UTCDateTime>
      </tds:SystemDateAndTime>
    </tds:GetSystemDateAndTimeResponse>"""


def _get_capabilities(ctx: OnvifContext) -> str:
    device_xaddr = f"http://{ctx.local_ip}:{ctx.settings.onvif_port}/onvif/device_service"
    media_xaddr = f"http://{ctx.local_ip}:{ctx.settings.onvif_port}/onvif/media_service"
    return f"""    <tds:GetCapabilitiesResponse>
      <tds:Capabilities>
        <tt:Device><tt:XAddr>{device_xaddr}</tt:XAddr></tt:Device>
        <tt:Media><tt:XAddr>{media_xaddr}</tt:XAddr></tt:Media>
      </tds:Capabilities>
    </tds:GetCapabilitiesResponse>"""


def _get_device_information(ctx: OnvifContext) -> str:
    return f"""    <tds:GetDeviceInformationResponse>
      <tds:Manufacturer>Dashboard Stream Cam</tds:Manufacturer>
      <tds:Model>{escape(ctx.settings.onvif_device_name)}</tds:Model>
      <tds:FirmwareVersion>1.0.0</tds:FirmwareVersion>
      <tds:SerialNumber>{ctx.device_uuid}</tds:SerialNumber>
      <tds:HardwareId>virtual-1</tds:HardwareId>
    </tds:GetDeviceInformationResponse>"""


def _get_scopes(ctx: OnvifContext) -> str:
    scopes = [
        ("Fixed", "onvif://www.onvif.org/type/video_encoder"),
        ("Fixed", "onvif://www.onvif.org/Profile/Streaming"),
        ("Configurable", f"onvif://www.onvif.org/name/{quote(ctx.settings.onvif_device_name)}"),
        ("Configurable", "onvif://www.onvif.org/location/virtual"),
    ]
    items = "\n".join(
        f"      <tds:Scopes><tt:ScopeDef>{d}</tt:ScopeDef><tt:ScopeItem>{s}</tt:ScopeItem></tds:Scopes>"
        for d, s in scopes
    )
    return f"    <tds:GetScopesResponse>\n{items}\n    </tds:GetScopesResponse>"


def _get_services(ctx: OnvifContext) -> str:
    device_xaddr = f"http://{ctx.local_ip}:{ctx.settings.onvif_port}/onvif/device_service"
    media_xaddr = f"http://{ctx.local_ip}:{ctx.settings.onvif_port}/onvif/media_service"
    return f"""    <tds:GetServicesResponse>
      <tds:Service>
        <tds:Namespace>{TDS_NS}</tds:Namespace>
        <tds:XAddr>{device_xaddr}</tds:XAddr>
        <tds:Version><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tds:Version>
      </tds:Service>
      <tds:Service>
        <tds:Namespace>{TRT_NS}</tds:Namespace>
        <tds:XAddr>{media_xaddr}</tds:XAddr>
        <tds:Version><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tds:Version>
      </tds:Service>
    </tds:GetServicesResponse>"""


def _get_video_sources(ctx: OnvifContext) -> str:
    s = ctx.settings
    return f"""    <trt:GetVideoSourcesResponse>
      <trt:VideoSources token="vs_1">
        <tt:Framerate>{s.framerate}</tt:Framerate>
        <tt:Resolution><tt:Width>{s.stream_width}</tt:Width><tt:Height>{s.stream_height}</tt:Height></tt:Resolution>
      </trt:VideoSources>
    </trt:GetVideoSourcesResponse>"""


def _get_profiles(ctx: OnvifContext) -> str:
    s = ctx.settings
    return f"""    <trt:GetProfilesResponse>
      <trt:Profiles token="profile_1" fixed="true">
        <tt:Name>Dashboard</tt:Name>
        <tt:VideoSourceConfiguration token="vsc_1">
          <tt:Name>VideoSourceConfig</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:SourceToken>vs_1</tt:SourceToken>
          <tt:Bounds x="0" y="0" width="{s.stream_width}" height="{s.stream_height}"/>
        </tt:VideoSourceConfiguration>
        <tt:VideoEncoderConfiguration token="vec_1">
          <tt:Name>VideoEncoderConfig</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:Encoding>H264</tt:Encoding>
          <tt:Resolution><tt:Width>{s.stream_width}</tt:Width><tt:Height>{s.stream_height}</tt:Height></tt:Resolution>
          <tt:Quality>5</tt:Quality>
          <tt:RateControl>
            <tt:FrameRateLimit>{s.framerate}</tt:FrameRateLimit>
            <tt:EncodingInterval>1</tt:EncodingInterval>
            <tt:BitrateLimit>2500</tt:BitrateLimit>
          </tt:RateControl>
          <tt:H264><tt:GovLength>{s.framerate * 2}</tt:GovLength><tt:H264Profile>Main</tt:H264Profile></tt:H264>
        </tt:VideoEncoderConfiguration>
      </trt:Profiles>
    </trt:GetProfilesResponse>"""


def _get_stream_uri(ctx: OnvifContext) -> str:
    uri = f"rtsp://{ctx.local_ip}:{ctx.settings.rtsp_port}/stream"
    return f"""    <trt:GetStreamUriResponse>
      <trt:MediaUri>
        <tt:Uri>{uri}</tt:Uri>
        <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
        <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
        <tt:Timeout>PT0S</tt:Timeout>
      </trt:MediaUri>
    </trt:GetStreamUriResponse>"""


def _get_snapshot_uri(ctx: OnvifContext) -> str:
    uri = f"http://{ctx.local_ip}:{ctx.settings.onvif_port}/snapshot.jpg"
    return f"""    <trt:GetSnapshotUriResponse>
      <trt:MediaUri>
        <tt:Uri>{uri}</tt:Uri>
        <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
        <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
        <tt:Timeout>PT0S</tt:Timeout>
      </trt:MediaUri>
    </trt:GetSnapshotUriResponse>"""


# Operations that MUST be reachable without WS-Security, per common ONVIF
# client behaviour (clock sync / capability probing before login).
UNAUTHENTICATED_OPERATIONS = {"GetSystemDateAndTime"}

_HANDLERS = {
    "GetSystemDateAndTime": _get_system_date_and_time,
    "GetCapabilities": _get_capabilities,
    "GetDeviceInformation": _get_device_information,
    "GetScopes": _get_scopes,
    "GetServices": _get_services,
    "GetVideoSources": _get_video_sources,
    "GetProfiles": _get_profiles,
    "GetStreamUri": _get_stream_uri,
    "GetSnapshotUri": _get_snapshot_uri,
}


def handle_soap_request(raw_body: bytes, ctx: OnvifContext) -> str:
    try:
        envelope = ET.fromstring(raw_body)
    except ET.ParseError as exc:
        raise OnvifError("s:Sender", "s:Client", f"Malformed SOAP request: {exc}", http_status=400) from exc

    ns = {"s": SOAP_NS}
    body = envelope.find("s:Body", ns)
    if body is None or len(body) == 0:
        raise OnvifError("s:Sender", "s:Client", "SOAP Body is empty.", http_status=400)

    op_elem = list(body)[0]
    op_name = op_elem.tag.split("}")[-1]

    handler = _HANDLERS.get(op_name)
    if handler is None:
        raise OnvifError("s:Sender", "ter:ActionNotSupported", f"Unsupported operation: {op_name}", http_status=400)

    if op_name not in UNAUTHENTICATED_OPERATIONS:
        if not verify_security_header(envelope, ctx.settings.stream_username, ctx.settings.stream_password):
            raise NotAuthorized()

    return soap_envelope(handler(ctx))


# ---------------------------------------------------------------------------
# WS-Discovery (UDP multicast probe/hello responder)
# ---------------------------------------------------------------------------

def _build_probe_matches(ctx: OnvifContext, relates_to: str) -> str:
    device_xaddr = f"http://{ctx.local_ip}:{ctx.settings.onvif_port}/onvif/device_service"
    msg_id = f"urn:uuid:{uuid.uuid4()}"
    scopes = (
        "onvif://www.onvif.org/type/video_encoder "
        f"onvif://www.onvif.org/name/{quote(ctx.settings.onvif_device_name)} "
        "onvif://www.onvif.org/location/virtual"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<s:Envelope xmlns:s="{SOAP_NS}" xmlns:wsa="{WSA_NS}" xmlns:d="{WSD_NS}" xmlns:dn="{DN_NS}">\n'
        "  <s:Header>\n"
        f"    <wsa:MessageID>{msg_id}</wsa:MessageID>\n"
        f"    <wsa:RelatesTo>{escape(relates_to)}</wsa:RelatesTo>\n"
        "    <wsa:To>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:To>\n"
        "    <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</wsa:Action>\n"
        "  </s:Header>\n"
        "  <s:Body>\n    <d:ProbeMatches>\n      <d:ProbeMatch>\n"
        f"        <wsa:EndpointReference><wsa:Address>urn:uuid:{ctx.device_uuid}</wsa:Address></wsa:EndpointReference>\n"
        "        <d:Types>dn:NetworkVideoTransmitter</d:Types>\n"
        f"        <d:Scopes>{escape(scopes)}</d:Scopes>\n"
        f"        <d:XAddrs>{device_xaddr}</d:XAddrs>\n"
        "        <d:MetadataVersion>1</d:MetadataVersion>\n"
        "      </d:ProbeMatch>\n    </d:ProbeMatches>\n  </s:Body>\n</s:Envelope>"
    )


def _build_hello(ctx: OnvifContext) -> str:
    device_xaddr = f"http://{ctx.local_ip}:{ctx.settings.onvif_port}/onvif/device_service"
    msg_id = f"urn:uuid:{uuid.uuid4()}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<s:Envelope xmlns:s="{SOAP_NS}" xmlns:wsa="{WSA_NS}" xmlns:d="{WSD_NS}" xmlns:dn="{DN_NS}">\n'
        "  <s:Header>\n"
        f"    <wsa:MessageID>{msg_id}</wsa:MessageID>\n"
        "    <wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>\n"
        "    <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Hello</wsa:Action>\n"
        "  </s:Header>\n"
        "  <s:Body>\n    <d:Hello>\n"
        f"      <wsa:EndpointReference><wsa:Address>urn:uuid:{ctx.device_uuid}</wsa:Address></wsa:EndpointReference>\n"
        "      <d:Types>dn:NetworkVideoTransmitter</d:Types>\n"
        f"      <d:XAddrs>{device_xaddr}</d:XAddrs>\n"
        "      <d:MetadataVersion>1</d:MetadataVersion>\n"
        "    </d:Hello>\n  </s:Body>\n</s:Envelope>"
    )


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, ctx: OnvifContext):
        self.ctx = ctx
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        try:
            self._handle(data, addr)
        except Exception:  # noqa: BLE001 - never let a malformed packet kill the responder
            logger.debug("Ignoring malformed WS-Discovery packet from %s", addr, exc_info=True)

    def _handle(self, data: bytes, addr: tuple) -> None:
        root = ET.fromstring(data)
        ns = {"s": SOAP_NS, "wsa": WSA_NS, "d": WSD_NS}
        body = root.find("s:Body", ns)
        if body is None or body.find("d:Probe", ns) is None:
            return
        header = root.find("s:Header", ns)
        msg_id_el = header.find("wsa:MessageID", ns) if header is not None else None
        relates_to = msg_id_el.text if msg_id_el is not None and msg_id_el.text else ""
        response = _build_probe_matches(self.ctx, relates_to)
        assert self.transport is not None
        self.transport.sendto(response.encode("utf-8"), addr)
        logger.info("WS-Discovery: answered Probe from %s:%s", addr[0], addr[1])

    def send_hello(self) -> None:
        if self.transport is None:
            return
        data = _build_hello(self.ctx).encode("utf-8")
        self.transport.sendto(data, (MULTICAST_ADDR, MULTICAST_PORT))


def _make_multicast_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", MULTICAST_PORT))
    mreq = struct.pack("4sl", socket.inet_aton(MULTICAST_ADDR), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setblocking(False)
    return sock


async def run_ws_discovery(ctx: OnvifContext, hello_interval: int = 60) -> None:
    """Runs forever, answering WS-Discovery Probes and periodically
    broadcasting Hello announcements so passive scanners see this device
    too. Requires host networking - see DOCS.md."""
    loop = asyncio.get_running_loop()
    sock = _make_multicast_socket()
    transport, protocol = await loop.create_datagram_endpoint(lambda: DiscoveryProtocol(ctx), sock=sock)
    logger.info(
        "WS-Discovery responder listening on udp/%s as %s (%s)",
        MULTICAST_PORT,
        ctx.settings.onvif_device_name,
        ctx.local_ip,
    )
    try:
        protocol.send_hello()
        while True:
            await asyncio.sleep(hello_interval)
            protocol.send_hello()
    finally:
        transport.close()
