"""A deliberately minimal ONVIF Device/Media service and WS-Discovery
responder.

This is not a general-purpose ONVIF stack. It describes the fixed profiles
this app really publishes - a main stream and, unless it is switched off, a
smaller substream, both of the same rendered dashboard - so that NVR
software such as UniFi Protect can discover the camera, ask for its
capabilities, and retrieve the RTSP stream and JPEG snapshot URLs. All of
it is gated behind the same username/password used for the RTSP stream
itself, via WS-Security UsernameToken (digest or plain).

Relevant specs (referenced for attribution, see NOTICE.md):
 - ONVIF Core Specification / WSDLs, https://www.onvif.org/profiles/specifications/
 - WS-Discovery, https://docs.oasis-open.org/ws-dx/ws-discovery/1.1/
 - WS-Security UsernameToken Profile 1.0, OASIS
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import fcntl
import hashlib
import logging
import socket
import struct
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
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
TEV_NS = "http://www.onvif.org/ver10/events/wsdl"
WSNT_NS = "http://docs.oasis-open.org/wsn/b-2"
WSTOP_NS = "http://docs.oasis-open.org/wsn/t-1"
WSA5_NS = "http://www.w3.org/2005/08/addressing"
TNS1_NS = "http://www.onvif.org/ver10/topics"

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


def get_mac_address(for_ip: str, device_uuid: str) -> str:
    """MAC address to report over ONVIF, in aa:bb:cc:dd:ee:ff form.

    NVRs key a camera by its MAC, so this has to be stable. The real address
    of the interface holding the advertised IP is used when it can be found;
    otherwise a locally-administered address is derived from the persistent
    device UUID, which is stable across restarts as well.
    """
    try:
        for _index, name in socket.if_nameindex():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                packed = fcntl.ioctl(  # SIOCGIFADDR
                    sock.fileno(), 0x8915, struct.pack("256s", name[:15].encode())
                )
                if socket.inet_ntoa(packed[20:24]) != for_ip:
                    continue
            except OSError:
                continue
            finally:
                sock.close()
            try:
                return Path(f"/sys/class/net/{name}/address").read_text().strip()
            except OSError:
                break
    except OSError:
        pass

    # Locally administered address (second-least-significant bit of the first
    # octet set, multicast bit clear) derived from the device UUID.
    raw = bytearray(uuid.UUID(device_uuid).bytes[:6]) if _is_uuid(device_uuid) else bytearray(b"\x00" * 6)
    raw[0] = (raw[0] | 0x02) & 0xFE
    return ":".join(f"{b:02x}" for b in raw)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def get_or_create_snapshot_token(path: str = "/data/snapshot_token") -> str:
    """Unguessable token that makes the snapshot URL work for plain HTTP GETs.

    ONVIF clients are supposed to authenticate to the snapshot URI, but some
    NVRs - UniFi Protect included - simply GET the URI returned by
    GetSnapshotUri with no credentials and give up on the 401. The token is
    handed out only inside authenticated GetSnapshotUri responses and stands
    in for those credentials; HTTP Basic keeps working alongside it.
    """
    file = Path(path)
    if file.exists():
        value = file.read_text().strip()
        if value:
            return value
    value = uuid.uuid4().hex
    file.write_text(value)
    return value


@dataclass
class OnvifContext:
    settings: Settings
    local_ip: str
    device_uuid: str
    snapshot_token: str = ""
    mac_address: str = "00:00:00:00:00:00"
    # Active event subscriptions: id -> (created, pulls_served)
    subscriptions: dict = field(default_factory=dict)


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

def check_security_header(envelope: ET.Element, username: str, password: str) -> tuple[bool, str]:
    """Validate the WS-Security header, returning (ok, reason).

    The reason is what makes a rejected NVR diagnosable: clients report
    nothing more specific than "invalid credentials", so the device has to
    say which part did not match. It never contains the password.
    """
    ns = {"s": SOAP_NS, "wsse": WSSE_NS, "wsu": WSU_NS}
    header = envelope.find("s:Header", ns)
    if header is None:
        return False, "request has no SOAP header"
    security = header.find("wsse:Security", ns)
    if security is None:
        return False, "request has no WS-Security header"
    token = security.find("wsse:UsernameToken", ns)
    if token is None:
        return False, "WS-Security header has no UsernameToken"
    user_el = token.find("wsse:Username", ns)
    pass_el = token.find("wsse:Password", ns)
    nonce_el = token.find("wsse:Nonce", ns)
    created_el = token.find("wsu:Created", ns)
    if user_el is None or pass_el is None:
        return False, "UsernameToken is missing Username or Password"
    supplied_user = user_el.text or ""
    if supplied_user != username:
        return False, f"username {supplied_user!r} does not match the configured stream_username"
    supplied = pass_el.text or ""
    password_type = pass_el.get("Type", "")

    if nonce_el is None or created_el is None or "PasswordText" in password_type:
        # Plain-text password fallback for clients that don't implement digest auth.
        if supplied == password:
            return True, "plain-text password"
        return False, "plain-text password does not match stream_password"

    try:
        nonce_raw = base64.b64decode(nonce_el.text or "")
    except (ValueError, TypeError):
        return False, "Nonce is not valid base64"
    created = created_el.text or ""
    digest_input = nonce_raw + created.encode("utf-8") + password.encode("utf-8")
    expected = base64.b64encode(hashlib.sha1(digest_input).digest()).decode("ascii")  # noqa: S324 - WS-Security mandates SHA1
    if expected != supplied:
        return False, "password digest does not match stream_password"

    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            created_dt = datetime.datetime.strptime(created, fmt).replace(tzinfo=datetime.timezone.utc)
            break
        except ValueError:
            created_dt = None
    if created_dt is None:
        # Unparsable timestamp: the digest already matched, so don't hard-fail
        # on a clock format this parser does not know.
        return True, "password digest (timestamp format not recognised)"
    skew = abs((datetime.datetime.now(datetime.timezone.utc) - created_dt).total_seconds())
    if skew > 300:
        return False, f"timestamp is {int(skew)}s off this device's clock (limit 300s)"
    return True, "password digest"


def verify_security_header(envelope: ET.Element, username: str, password: str) -> bool:
    """Boolean form of :func:`check_security_header`."""
    ok, _reason = check_security_header(envelope, username, password)
    return ok


# ---------------------------------------------------------------------------
# SOAP envelope helpers
# ---------------------------------------------------------------------------

def soap_envelope(body_inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<s:Envelope xmlns:s="{SOAP_NS}" xmlns:tds="{TDS_NS}" xmlns:trt="{TRT_NS}" '
        f'xmlns:tt="{TT_NS}" xmlns:tev="{TEV_NS}" xmlns:wsnt="{WSNT_NS}" '
        f'xmlns:wstop="{WSTOP_NS}" xmlns:wsa="{WSA5_NS}" xmlns:tns1="{TNS1_NS}">'
        f'\n  <s:Body>\n{body_inner}\n  </s:Body>\n</s:Envelope>'
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

def _get_system_date_and_time(_ctx: OnvifContext, _request: ET.Element) -> str:
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


def _service_xaddr(ctx: OnvifContext, service: str) -> str:
    return f"http://{ctx.local_ip}:{ctx.settings.onvif_port}/onvif/{service}"


def _get_capabilities(ctx: OnvifContext, _request: ET.Element) -> str:
    """Describe the device the way a real camera does.

    StreamingCapabilities is the part that matters most: it is where a client
    learns that this camera can deliver RTP over RTSP over TCP. Announcing only
    an XAddr, as this did before, leaves an NVR to guess the transport.
    """
    return f"""    <tds:GetCapabilitiesResponse>
      <tds:Capabilities>
        <tt:Device>
          <tt:XAddr>{_service_xaddr(ctx, "device_service")}</tt:XAddr>
          <tt:Network>
            <tt:IPFilter>false</tt:IPFilter>
            <tt:ZeroConfiguration>false</tt:ZeroConfiguration>
            <tt:IPVersion6>false</tt:IPVersion6>
            <tt:DynDNS>false</tt:DynDNS>
          </tt:Network>
          <tt:System>
            <tt:DiscoveryResolve>false</tt:DiscoveryResolve>
            <tt:DiscoveryBye>false</tt:DiscoveryBye>
            <tt:RemoteDiscovery>false</tt:RemoteDiscovery>
            <tt:SystemBackup>false</tt:SystemBackup>
            <tt:SystemLogging>false</tt:SystemLogging>
            <tt:FirmwareUpgrade>false</tt:FirmwareUpgrade>
            <tt:SupportedVersions><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tt:SupportedVersions>
          </tt:System>
          <tt:IO>
            <tt:InputConnectors>0</tt:InputConnectors>
            <tt:RelayOutputs>0</tt:RelayOutputs>
          </tt:IO>
          <tt:Security>
            <tt:TLS1.1>false</tt:TLS1.1>
            <tt:TLS1.2>false</tt:TLS1.2>
            <tt:OnboardKeyGeneration>false</tt:OnboardKeyGeneration>
            <tt:AccessPolicyConfig>false</tt:AccessPolicyConfig>
            <tt:X.509Token>false</tt:X.509Token>
            <tt:SAMLToken>false</tt:SAMLToken>
            <tt:KerberosToken>false</tt:KerberosToken>
            <tt:RELToken>false</tt:RELToken>
          </tt:Security>
        </tt:Device>
        <tt:Events>
          <tt:XAddr>{_service_xaddr(ctx, "events_service")}</tt:XAddr>
          <tt:WSSubscriptionPolicySupport>false</tt:WSSubscriptionPolicySupport>
          <tt:WSPullPointSupport>true</tt:WSPullPointSupport>
          <tt:WSPausableSubscriptionManagerInterfaceSupport>false</tt:WSPausableSubscriptionManagerInterfaceSupport>
        </tt:Events>
        <tt:Media>
          <tt:XAddr>{_service_xaddr(ctx, "media_service")}</tt:XAddr>
          <tt:StreamingCapabilities>
            <tt:RTPMulticast>false</tt:RTPMulticast>
            <tt:RTP_TCP>true</tt:RTP_TCP>
            <tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>
          </tt:StreamingCapabilities>
          <tt:Extension>
            <tt:ProfileCapabilities>
              <tt:MaximumNumberOfProfiles>{len(media_profiles(ctx))}</tt:MaximumNumberOfProfiles>
            </tt:ProfileCapabilities>
          </tt:Extension>
        </tt:Media>
      </tds:Capabilities>
    </tds:GetCapabilitiesResponse>"""


def _get_device_information(ctx: OnvifContext, _request: ET.Element) -> str:
    return f"""    <tds:GetDeviceInformationResponse>
      <tds:Manufacturer>Dashboard Stream Cam</tds:Manufacturer>
      <tds:Model>{escape(ctx.settings.onvif_device_name)}</tds:Model>
      <tds:FirmwareVersion>1.0.0</tds:FirmwareVersion>
      <tds:SerialNumber>{ctx.device_uuid}</tds:SerialNumber>
      <tds:HardwareId>virtual-1</tds:HardwareId>
    </tds:GetDeviceInformationResponse>"""


def _get_scopes(ctx: OnvifContext, _request: ET.Element) -> str:
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


def _get_services(ctx: OnvifContext, _request: ET.Element) -> str:
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
      <tds:Service>
        <tds:Namespace>{TEV_NS}</tds:Namespace>
        <tds:XAddr>{_service_xaddr(ctx, "events_service")}</tds:XAddr>
        <tds:Version><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tds:Version>
      </tds:Service>
    </tds:GetServicesResponse>"""


def _get_video_sources(ctx: OnvifContext, _request: ET.Element) -> str:
    s = ctx.settings
    return f"""    <trt:GetVideoSourcesResponse>
      <trt:VideoSources token="vs_1">
        <tt:Framerate>{s.framerate}</tt:Framerate>
        <tt:Resolution><tt:Width>{s.stream_width}</tt:Width><tt:Height>{s.stream_height}</tt:Height></tt:Resolution>
      </trt:VideoSources>
    </trt:GetVideoSourcesResponse>"""


# ---------------------------------------------------------------------------
# Event service (WS-BaseNotification pull point)
#
# This device has nothing to report - a dashboard does not move - but an NVR
# expects a camera to offer the event service and subscribes to it while
# setting the camera up. Both reference implementations for getting an RTSP
# source into UniFi Protect (Happytime's server and rtsp-to-onvif) implement
# it, so this one does too: a subscription that is accepted, renewed and
# unsubscribed properly, and that reports the motion alarm as permanently
# false rather than pretending motion it cannot detect.
# ---------------------------------------------------------------------------

EVENT_TOPIC = "tns1:VideoSource/MotionAlarm"


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _xs_datetime(value: datetime.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _subscription_address(ctx: OnvifContext, subscription_id: str) -> str:
    return f"{_service_xaddr(ctx, 'events_service')}?sub={subscription_id}"


def _create_pull_point_subscription(ctx: OnvifContext, _request: ET.Element) -> str:
    subscription_id = uuid.uuid4().hex[:16]
    now = _utc_now()
    ctx.subscriptions[subscription_id] = {"created": now, "pulls": 0}
    logger.debug("ONVIF: created event subscription %s", subscription_id)
    return f"""    <tev:CreatePullPointSubscriptionResponse>
      <tev:SubscriptionReference>
        <wsa:Address>{escape(_subscription_address(ctx, subscription_id))}</wsa:Address>
      </tev:SubscriptionReference>
      <wsnt:CurrentTime>{_xs_datetime(now)}</wsnt:CurrentTime>
      <wsnt:TerminationTime>{_xs_datetime(now + datetime.timedelta(minutes=10))}</wsnt:TerminationTime>
    </tev:CreatePullPointSubscriptionResponse>"""


def _pull_messages(ctx: OnvifContext, _request: ET.Element) -> str:
    """Answer a pull with the current (always false) motion state.

    A property event carries its initial state on the first pull, which is what
    tells a client the topic exists at all; later pulls simply report the same
    unchanged state.
    """
    now = _utc_now()
    subscription = next(iter(ctx.subscriptions.values()), None)
    first_pull = True
    if subscription is not None:
        first_pull = subscription["pulls"] == 0
        subscription["pulls"] += 1
    message = f"""
      <wsnt:NotificationMessage>
        <wsnt:Topic Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet">{EVENT_TOPIC}</wsnt:Topic>
        <wsnt:Message>
          <tt:Message UtcTime="{_xs_datetime(now)}" PropertyOperation="{'Initialized' if first_pull else 'Changed'}">
            <tt:Source>
              <tt:SimpleItem Name="VideoSourceConfigurationToken" Value="vsc_1"/>
            </tt:Source>
            <tt:Data>
              <tt:SimpleItem Name="State" Value="false"/>
            </tt:Data>
          </tt:Message>
        </wsnt:Message>
      </wsnt:NotificationMessage>"""
    return f"""    <tev:PullMessagesResponse>
      <tev:CurrentTime>{_xs_datetime(now)}</tev:CurrentTime>
      <tev:TerminationTime>{_xs_datetime(now + datetime.timedelta(minutes=10))}</tev:TerminationTime>{message if first_pull else ""}
    </tev:PullMessagesResponse>"""


def _renew(ctx: OnvifContext, _request: ET.Element) -> str:
    now = _utc_now()
    return f"""    <wsnt:RenewResponse>
      <wsnt:CurrentTime>{_xs_datetime(now)}</wsnt:CurrentTime>
      <wsnt:TerminationTime>{_xs_datetime(now + datetime.timedelta(minutes=10))}</wsnt:TerminationTime>
    </wsnt:RenewResponse>"""


def _unsubscribe(ctx: OnvifContext, _request: ET.Element) -> str:
    ctx.subscriptions.clear()
    logger.debug("ONVIF: event subscriptions released")
    return "    <wsnt:UnsubscribeResponse/>"


def _get_event_properties(_ctx: OnvifContext, _request: ET.Element) -> str:
    return f"""    <tev:GetEventPropertiesResponse>
      <tev:TopicNamespaceLocation>http://www.onvif.org/onvif/ver10/topics/topicns.xml</tev:TopicNamespaceLocation>
      <wsnt:FixedTopicSet>true</wsnt:FixedTopicSet>
      <wstop:TopicSet>
        <tns1:VideoSource>
          <MotionAlarm wstop:topic="true">
            <tt:MessageDescription IsProperty="true">
              <tt:Source>
                <tt:SimpleItemDescription Name="VideoSourceConfigurationToken" Type="tt:ReferenceToken"/>
              </tt:Source>
              <tt:Data>
                <tt:SimpleItemDescription Name="State" Type="xs:boolean"/>
              </tt:Data>
            </tt:MessageDescription>
          </MotionAlarm>
        </tns1:VideoSource>
      </wstop:TopicSet>
      <wsnt:TopicExpressionDialect>http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet</wsnt:TopicExpressionDialect>
      <tev:MessageContentFilterDialect>http://www.onvif.org/ver10/tev/messageContentFilter/ItemFilter</tev:MessageContentFilterDialect>
      <tev:MessageContentSchemaLocation>http://www.onvif.org/onvif/ver10/schema/onvif.xsd</tev:MessageContentSchemaLocation>
    </tev:GetEventPropertiesResponse>"""


def _get_device_service_capabilities(_ctx: OnvifContext, _request: ET.Element) -> str:
    return """    <tds:GetServiceCapabilitiesResponse>
      <tds:Capabilities>
        <tds:Network IPFilter="false" ZeroConfiguration="false" IPVersion6="false" DynDNS="false"/>
        <tds:Security TLS1.0="false" TLS1.1="false" TLS1.2="false" OnboardKeyGeneration="false"
          AccessPolicyConfig="false" DefaultAccessPolicy="false" Dot1X="false" RemoteUserHandling="false"
          X.509Token="false" SAMLToken="false" KerberosToken="false" UsernameToken="true" HttpDigest="false"
          RELToken="false"/>
        <tds:System DiscoveryResolve="false" DiscoveryBye="false" RemoteDiscovery="false"
          SystemBackup="false" SystemLogging="false" FirmwareUpgrade="false" HttpFirmwareUpgrade="false"
          HttpSystemBackup="false" HttpSystemLogging="false" HttpSupportInformation="false"/>
      </tds:Capabilities>
    </tds:GetServiceCapabilitiesResponse>"""


def _get_media_service_capabilities(ctx: OnvifContext, _request: ET.Element) -> str:
    return f"""    <trt:GetServiceCapabilitiesResponse>
      <trt:Capabilities SnapshotUri="true" Rotation="false" VideoSourceMode="false" OSD="false">
        <trt:ProfileCapabilities MaximumNumberOfProfiles="{len(media_profiles(ctx))}"/>
        <trt:StreamingCapabilities RTPMulticast="false" RTP_TCP="true" RTP_RTSP_TCP="true"
          NonAggregateControl="false" NoRTSPStreaming="false"/>
      </trt:Capabilities>
    </trt:GetServiceCapabilitiesResponse>"""


def _get_event_service_capabilities(_ctx: OnvifContext, _request: ET.Element) -> str:
    return """    <tev:GetServiceCapabilitiesResponse>
      <tev:Capabilities WSSubscriptionPolicySupport="false" WSPullPointSupport="true"
        WSPausableSubscriptionManagerInterfaceSupport="false" MaxNotificationProducers="1"
        MaxPullPoints="4" PersistentNotificationStorage="false"/>
    </tev:GetServiceCapabilitiesResponse>"""


def _get_network_interfaces(ctx: OnvifContext, _request: ET.Element) -> str:
    """Report one interface with the advertised address and a MAC.

    NVRs key a camera by its MAC address; UniFi Protect asks for this during
    adoption and cannot finish setting the camera up without it.
    """
    return f"""    <tds:GetNetworkInterfacesResponse>
      <tds:NetworkInterfaces token="eth0">
        <tt:Enabled>true</tt:Enabled>
        <tt:Info>
          <tt:Name>eth0</tt:Name>
          <tt:HwAddress>{escape(ctx.mac_address)}</tt:HwAddress>
          <tt:MTU>1500</tt:MTU>
        </tt:Info>
        <tt:IPv4>
          <tt:Enabled>true</tt:Enabled>
          <tt:Config>
            <tt:Manual>
              <tt:Address>{escape(ctx.local_ip)}</tt:Address>
              <tt:PrefixLength>24</tt:PrefixLength>
            </tt:Manual>
            <tt:DHCP>false</tt:DHCP>
          </tt:Config>
        </tt:IPv4>
      </tds:NetworkInterfaces>
    </tds:GetNetworkInterfacesResponse>"""


def _get_hostname(ctx: OnvifContext, _request: ET.Element) -> str:
    return f"""    <tds:GetHostnameResponse>
      <tds:HostnameInformation>
        <tt:FromDHCP>false</tt:FromDHCP>
        <tt:Name>{escape(ctx.settings.onvif_device_name)}</tt:Name>
      </tds:HostnameInformation>
    </tds:GetHostnameResponse>"""


def _get_network_protocols(ctx: OnvifContext, _request: ET.Element) -> str:
    s = ctx.settings
    return f"""    <tds:GetNetworkProtocolsResponse>
      <tds:NetworkProtocols>
        <tt:Name>HTTP</tt:Name><tt:Enabled>true</tt:Enabled><tt:Port>{s.onvif_port}</tt:Port>
      </tds:NetworkProtocols>
      <tds:NetworkProtocols>
        <tt:Name>RTSP</tt:Name><tt:Enabled>true</tt:Enabled><tt:Port>{s.rtsp_port}</tt:Port>
      </tds:NetworkProtocols>
    </tds:GetNetworkProtocolsResponse>"""


# The ONVIF schema makes Multicast mandatory in every encoder configuration,
# even for a device that does not do multicast at all. Leaving it out produces
# XML that a strict client generated from the WSDL cannot deserialise - the
# camera then has no usable stream configuration, which is what an NVR reports
# as "cannot load live feed". Disabled multicast is expressed as address
# 0.0.0.0, port 0, AutoStart false.
_MULTICAST_OFF = """      <tt:Multicast>
        <tt:Address>
          <tt:Type>IPv4</tt:Type>
          <tt:IPv4Address>0.0.0.0</tt:IPv4Address>
        </tt:Address>
        <tt:Port>0</tt:Port>
        <tt:TTL>1</tt:TTL>
        <tt:AutoStart>false</tt:AutoStart>
      </tt:Multicast>"""


@dataclass(frozen=True)
class MediaProfile:
    """One media profile, backed by an RTSP path this app really publishes.

    Every camera this app has been compared against - and that UniFi Protect
    accepts - offers a main stream plus a smaller substream, and an NVR picks
    between them per view. Nothing here is advertised that the capture does
    not actually encode: each profile maps to one ffmpeg output and one
    mediamtx path.
    """

    token: str
    name: str
    path: str
    encoder_token: str
    width: int
    height: int
    framerate: int
    bitrate: int  # kbit/s, kept in step with /etc/services.d/ffmpeg/run


def media_profiles(ctx: OnvifContext) -> list[MediaProfile]:
    s = ctx.settings
    profiles = [
        MediaProfile(
            "profile_1", "MainStream", "stream", "vec_1",
            s.stream_width, s.stream_height, s.framerate, 2500,
        )
    ]
    if s.substream:
        profiles.append(
            MediaProfile(
                "profile_2", "SubStream", "substream", "vec_2",
                s.sub_width, s.sub_height, s.sub_framerate, 500,
            )
        )
    return profiles


def _request_child(request: ET.Element, name: str) -> str:
    """Text of the first child element with this local name, any namespace."""
    for child in request:
        if child.tag.split("}")[-1] == name:
            return (child.text or "").strip()
    return ""


def _no_profile(token: str) -> OnvifError:
    return OnvifError("s:Sender", "ter:NoProfile", f"No profile with token {token!r}.", http_status=400)


def _no_config(token: str) -> OnvifError:
    return OnvifError(
        "s:Sender", "ter:NoConfig", f"No video encoder configuration with token {token!r}.", http_status=400
    )


def _profile_for_request(ctx: OnvifContext, request: ET.Element) -> MediaProfile:
    """The profile a request names, or the main stream when it names none."""
    profiles = media_profiles(ctx)
    token = _request_child(request, "ProfileToken")
    if not token:
        return profiles[0]
    for profile in profiles:
        if profile.token == token:
            return profile
    raise _no_profile(token)


def _encoder_for_request(ctx: OnvifContext, request: ET.Element) -> MediaProfile:
    profiles = media_profiles(ctx)
    token = _request_child(request, "ConfigurationToken")
    if not token:
        return profiles[0]
    for profile in profiles:
        if profile.encoder_token == token:
            return profile
    raise _no_config(token)


def _encoders_for_options_request(ctx: OnvifContext, request: ET.Element) -> list[MediaProfile]:
    """Which encoders a GetVideoEncoderConfigurationOptions call asks about.

    Both tokens are optional in that operation; with neither, the answer has
    to cover every configuration the device has.
    """
    profiles = media_profiles(ctx)
    config_token = _request_child(request, "ConfigurationToken")
    if config_token:
        selected = [p for p in profiles if p.encoder_token == config_token]
        if not selected:
            raise _no_config(config_token)
        return selected
    profile_token = _request_child(request, "ProfileToken")
    if profile_token:
        selected = [p for p in profiles if p.token == profile_token]
        if not selected:
            raise _no_profile(profile_token)
        return selected
    return profiles


def _video_source_config_body(ctx: OnvifContext) -> str:
    s = ctx.settings
    # One video source shared by every profile - the substream is the same
    # rendering, scaled down by its own encoder, exactly as a camera scales
    # one sensor into two streams.
    return f"""      <tt:Name>VideoSourceConfig</tt:Name>
      <tt:UseCount>{len(media_profiles(ctx))}</tt:UseCount>
      <tt:SourceToken>vs_1</tt:SourceToken>
      <tt:Bounds x="0" y="0" width="{s.stream_width}" height="{s.stream_height}"/>"""


def _h264_profile(ctx: OnvifContext) -> str:
    """The ONVIF spelling of the profile the encoder is configured for."""
    return "Baseline" if ctx.settings.h264_profile == "baseline" else "Main"


def _video_encoder_config_body(ctx: OnvifContext, profile: MediaProfile) -> str:
    return f"""      <tt:Name>VideoEncoder_{profile.name}</tt:Name>
      <tt:UseCount>1</tt:UseCount>
      <tt:Encoding>H264</tt:Encoding>
      <tt:Resolution><tt:Width>{profile.width}</tt:Width><tt:Height>{profile.height}</tt:Height></tt:Resolution>
      <tt:Quality>5</tt:Quality>
      <tt:RateControl>
        <tt:FrameRateLimit>{profile.framerate}</tt:FrameRateLimit>
        <tt:EncodingInterval>1</tt:EncodingInterval>
        <tt:BitrateLimit>{profile.bitrate}</tt:BitrateLimit>
      </tt:RateControl>
      <tt:H264><tt:GovLength>{profile.framerate}</tt:GovLength><tt:H264Profile>{_h264_profile(ctx)}</tt:H264Profile></tt:H264>
{_MULTICAST_OFF}
      <tt:SessionTimeout>PT60S</tt:SessionTimeout>"""


def _has_audio(ctx: OnvifContext) -> bool:
    return ctx.settings.audio_track == "silent"


def _audio_source_config_body(ctx: OnvifContext) -> str:
    return f"""      <tt:Name>AudioSourceConfig</tt:Name>
      <tt:UseCount>{len(media_profiles(ctx))}</tt:UseCount>
      <tt:SourceToken>as_1</tt:SourceToken>"""


def _audio_encoder_config_body(ctx: OnvifContext) -> str:
    """Matches the silent G.711 track the capture publishes on every path."""
    return f"""      <tt:Name>AudioEncoderConfig</tt:Name>
      <tt:UseCount>{len(media_profiles(ctx))}</tt:UseCount>
      <tt:Encoding>G711</tt:Encoding>
      <tt:Bitrate>64</tt:Bitrate>
      <tt:SampleRate>8</tt:SampleRate>
{_MULTICAST_OFF}
      <tt:SessionTimeout>PT60S</tt:SessionTimeout>"""


def _profile_body(ctx: OnvifContext, profile: MediaProfile) -> str:
    # Element order follows the ONVIF schema sequence: video source, audio
    # source, video encoder, audio encoder.
    audio_source = ""
    audio_encoder = ""
    if _has_audio(ctx):
        audio_source = f"""
      <tt:AudioSourceConfiguration token="asc_1">
{_audio_source_config_body(ctx)}
      </tt:AudioSourceConfiguration>"""
        audio_encoder = f"""
      <tt:AudioEncoderConfiguration token="aec_1">
{_audio_encoder_config_body(ctx)}
      </tt:AudioEncoderConfiguration>"""
    return f"""      <tt:Name>{profile.name}</tt:Name>
      <tt:VideoSourceConfiguration token="vsc_1">
{_video_source_config_body(ctx)}
      </tt:VideoSourceConfiguration>{audio_source}
      <tt:VideoEncoderConfiguration token="{profile.encoder_token}">
{_video_encoder_config_body(ctx, profile)}
      </tt:VideoEncoderConfiguration>{audio_encoder}"""


def _get_audio_sources(ctx: OnvifContext, _request: ET.Element) -> str:
    if not _has_audio(ctx):
        return "    <trt:GetAudioSourcesResponse/>"
    return """    <trt:GetAudioSourcesResponse>
      <trt:AudioSources token="as_1">
        <tt:Channels>1</tt:Channels>
      </trt:AudioSources>
    </trt:GetAudioSourcesResponse>"""


def _get_audio_source_configurations(ctx: OnvifContext, _request: ET.Element) -> str:
    if not _has_audio(ctx):
        return "    <trt:GetAudioSourceConfigurationsResponse/>"
    return f"""    <trt:GetAudioSourceConfigurationsResponse>
      <trt:Configurations token="asc_1">
{_audio_source_config_body(ctx)}
      </trt:Configurations>
    </trt:GetAudioSourceConfigurationsResponse>"""


def _get_audio_source_configuration(ctx: OnvifContext, _request: ET.Element) -> str:
    if not _has_audio(ctx):
        raise OnvifError("s:Sender", "ter:NoConfig", "This device has no audio configuration.", http_status=400)
    return f"""    <trt:GetAudioSourceConfigurationResponse>
      <trt:Configuration token="asc_1">
{_audio_source_config_body(ctx)}
      </trt:Configuration>
    </trt:GetAudioSourceConfigurationResponse>"""


def _get_audio_encoder_configurations(ctx: OnvifContext, _request: ET.Element) -> str:
    if not _has_audio(ctx):
        return "    <trt:GetAudioEncoderConfigurationsResponse/>"
    return f"""    <trt:GetAudioEncoderConfigurationsResponse>
      <trt:Configurations token="aec_1">
{_audio_encoder_config_body(ctx)}
      </trt:Configurations>
    </trt:GetAudioEncoderConfigurationsResponse>"""


def _get_audio_encoder_configuration(ctx: OnvifContext, _request: ET.Element) -> str:
    if not _has_audio(ctx):
        raise OnvifError("s:Sender", "ter:NoConfig", "This device has no audio configuration.", http_status=400)
    return f"""    <trt:GetAudioEncoderConfigurationResponse>
      <trt:Configuration token="aec_1">
{_audio_encoder_config_body(ctx)}
      </trt:Configuration>
    </trt:GetAudioEncoderConfigurationResponse>"""


def _get_audio_encoder_configuration_options(ctx: OnvifContext, _request: ET.Element) -> str:
    if not _has_audio(ctx):
        return "    <trt:GetAudioEncoderConfigurationOptionsResponse/>"
    return """    <trt:GetAudioEncoderConfigurationOptionsResponse>
      <trt:Options>
        <tt:Options>
          <tt:Encoding>G711</tt:Encoding>
          <tt:BitrateList><tt:Items>64</tt:Items></tt:BitrateList>
          <tt:SampleRateList><tt:Items>8</tt:Items></tt:SampleRateList>
        </tt:Options>
      </trt:Options>
    </trt:GetAudioEncoderConfigurationOptionsResponse>"""


def _get_video_encoder_configurations(ctx: OnvifContext, _request: ET.Element) -> str:
    entries = "\n".join(
        f"""      <trt:Configurations token="{profile.encoder_token}">
{_video_encoder_config_body(ctx, profile)}
      </trt:Configurations>"""
        for profile in media_profiles(ctx)
    )
    return f"""    <trt:GetVideoEncoderConfigurationsResponse>
{entries}
    </trt:GetVideoEncoderConfigurationsResponse>"""


def _get_video_encoder_configuration(ctx: OnvifContext, request: ET.Element) -> str:
    profile = _encoder_for_request(ctx, request)
    return f"""    <trt:GetVideoEncoderConfigurationResponse>
      <trt:Configuration token="{profile.encoder_token}">
{_video_encoder_config_body(ctx, profile)}
      </trt:Configuration>
    </trt:GetVideoEncoderConfigurationResponse>"""


def _get_video_encoder_configuration_options(ctx: OnvifContext, request: ET.Element) -> str:
    """What the encoders *could* do - here: exactly what they do do.

    The stream is a fixed rendering pipeline, so every range is reported as a
    single value, and the resolutions offered are the ones actually encoded.
    NVRs ask for this before they will show a stream; refusing it is what
    stopped UniFi Protect.
    """
    profiles = _encoders_for_options_request(ctx, request)
    resolutions = "\n".join(
        f"""          <tt:ResolutionsAvailable>
            <tt:Width>{profile.width}</tt:Width><tt:Height>{profile.height}</tt:Height>
          </tt:ResolutionsAvailable>"""
        for profile in profiles
    )
    rates = [profile.framerate for profile in profiles]
    govs = [profile.framerate for profile in profiles]
    return f"""    <trt:GetVideoEncoderConfigurationOptionsResponse>
      <trt:Options>
        <tt:QualityRange><tt:Min>1</tt:Min><tt:Max>10</tt:Max></tt:QualityRange>
        <tt:H264>
{resolutions}
          <tt:GovLengthRange><tt:Min>{min(govs)}</tt:Min><tt:Max>{max(govs)}</tt:Max></tt:GovLengthRange>
          <tt:FrameRateRange><tt:Min>{min(rates)}</tt:Min><tt:Max>{max(rates)}</tt:Max></tt:FrameRateRange>
          <tt:EncodingIntervalRange><tt:Min>1</tt:Min><tt:Max>1</tt:Max></tt:EncodingIntervalRange>
          <tt:H264ProfilesSupported>{_h264_profile(ctx)}</tt:H264ProfilesSupported>
        </tt:H264>
      </trt:Options>
    </trt:GetVideoEncoderConfigurationOptionsResponse>"""


def _get_video_source_configurations(ctx: OnvifContext, _request: ET.Element) -> str:
    return f"""    <trt:GetVideoSourceConfigurationsResponse>
      <trt:Configurations token="vsc_1">
{_video_source_config_body(ctx)}
      </trt:Configurations>
    </trt:GetVideoSourceConfigurationsResponse>"""


def _get_video_source_configuration(ctx: OnvifContext, _request: ET.Element) -> str:
    return f"""    <trt:GetVideoSourceConfigurationResponse>
      <trt:Configuration token="vsc_1">
{_video_source_config_body(ctx)}
      </trt:Configuration>
    </trt:GetVideoSourceConfigurationResponse>"""


def _get_video_source_configuration_options(ctx: OnvifContext, _request: ET.Element) -> str:
    s = ctx.settings
    return f"""    <trt:GetVideoSourceConfigurationOptionsResponse>
      <trt:Options>
        <tt:BoundsRange>
          <tt:XRange><tt:Min>0</tt:Min><tt:Max>0</tt:Max></tt:XRange>
          <tt:YRange><tt:Min>0</tt:Min><tt:Max>0</tt:Max></tt:YRange>
          <tt:WidthRange><tt:Min>{s.stream_width}</tt:Min><tt:Max>{s.stream_width}</tt:Max></tt:WidthRange>
          <tt:HeightRange><tt:Min>{s.stream_height}</tt:Min><tt:Max>{s.stream_height}</tt:Max></tt:HeightRange>
        </tt:BoundsRange>
        <tt:VideoSourceTokensAvailable>vs_1</tt:VideoSourceTokensAvailable>
      </trt:Options>
    </trt:GetVideoSourceConfigurationOptionsResponse>"""


def _get_profile(ctx: OnvifContext, request: ET.Element) -> str:
    profile = _profile_for_request(ctx, request)
    return f"""    <trt:GetProfileResponse>
      <trt:Profile token="{profile.token}" fixed="true">
{_profile_body(ctx, profile)}
      </trt:Profile>
    </trt:GetProfileResponse>"""


def _get_profiles(ctx: OnvifContext, _request: ET.Element) -> str:
    entries = "\n".join(
        f"""      <trt:Profiles token="{profile.token}" fixed="true">
{_profile_body(ctx, profile)}
      </trt:Profiles>"""
        for profile in media_profiles(ctx)
    )
    return f"""    <trt:GetProfilesResponse>
{entries}
    </trt:GetProfilesResponse>"""


def _get_stream_uri(ctx: OnvifContext, request: ET.Element) -> str:
    profile = _profile_for_request(ctx, request)
    uri = f"rtsp://{ctx.local_ip}:{ctx.settings.rtsp_port}/{profile.path}"
    return f"""    <trt:GetStreamUriResponse>
      <trt:MediaUri>
        <tt:Uri>{uri}</tt:Uri>
        <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
        <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
        <tt:Timeout>PT0S</tt:Timeout>
      </trt:MediaUri>
    </trt:GetStreamUriResponse>"""


def _get_snapshot_uri(ctx: OnvifContext, request: ET.Element) -> str:
    # Both profiles show the same rendering, so there is one snapshot; the
    # profile token is still validated so an unknown one faults here as it
    # does everywhere else.
    _profile_for_request(ctx, request)
    # The token rides along so that a client which just GETs this URI without
    # credentials still gets a picture; see get_or_create_snapshot_token.
    uri = f"http://{ctx.local_ip}:{ctx.settings.onvif_port}/snapshot.jpg"
    if ctx.snapshot_token:
        uri = f"{uri}?token={ctx.snapshot_token}"
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
    "GetProfile": _get_profile,
    "GetStreamUri": _get_stream_uri,
    "GetSnapshotUri": _get_snapshot_uri,
    "GetVideoEncoderConfigurations": _get_video_encoder_configurations,
    "GetVideoEncoderConfiguration": _get_video_encoder_configuration,
    "GetVideoEncoderConfigurationOptions": _get_video_encoder_configuration_options,
    "GetVideoSourceConfigurations": _get_video_source_configurations,
    "GetVideoSourceConfiguration": _get_video_source_configuration,
    "GetVideoSourceConfigurationOptions": _get_video_source_configuration_options,
    "GetAudioSources": _get_audio_sources,
    "GetAudioSourceConfigurations": _get_audio_source_configurations,
    "GetAudioSourceConfiguration": _get_audio_source_configuration,
    "GetAudioEncoderConfigurations": _get_audio_encoder_configurations,
    "GetAudioEncoderConfiguration": _get_audio_encoder_configuration,
    "GetAudioEncoderConfigurationOptions": _get_audio_encoder_configuration_options,
    "GetNetworkInterfaces": _get_network_interfaces,
    "GetNetworkProtocols": _get_network_protocols,
    "GetHostname": _get_hostname,
    "CreatePullPointSubscription": _create_pull_point_subscription,
    "PullMessages": _pull_messages,
    "Renew": _renew,
    "Unsubscribe": _unsubscribe,
    "GetEventProperties": _get_event_properties,
}

# GetServiceCapabilities exists in every ONVIF service and must answer for the
# endpoint it was called on - the media service replying with the event
# service's capabilities would be nonsense to a client.
_SERVICE_CAPABILITIES = {
    "device_service": _get_device_service_capabilities,
    "media_service": _get_media_service_capabilities,
    "events_service": _get_event_service_capabilities,
}


def handle_soap_request(raw_body: bytes, ctx: OnvifContext, peer: str = "?", service: str = "device_service") -> str:
    """Dispatch one ONVIF SOAP request.

    Every outcome is logged with the peer address: an NVR that refuses to
    pair says no more than "invalid credentials", so the device side has to
    be the one that records what was actually asked for and what failed.
    """
    try:
        envelope = ET.fromstring(raw_body)
    except ET.ParseError as exc:
        logger.warning("ONVIF request from %s is not valid XML: %s", peer, exc)
        raise OnvifError("s:Sender", "s:Client", f"Malformed SOAP request: {exc}", http_status=400) from exc

    ns = {"s": SOAP_NS}
    body = envelope.find("s:Body", ns)
    if body is None or len(body) == 0:
        logger.warning("ONVIF request from %s has an empty SOAP body", peer)
        raise OnvifError("s:Sender", "s:Client", "SOAP Body is empty.", http_status=400)

    op_elem = list(body)[0]
    op_name = op_elem.tag.split("}")[-1]

    handler = _HANDLERS.get(op_name)
    if op_name == "GetServiceCapabilities":
        handler = _SERVICE_CAPABILITIES.get(service, _get_device_service_capabilities)
    if handler is None:
        logger.warning(
            "ONVIF: %s asked for %r, which this device does not implement. If your NVR "
            "needs it, please report it with this log line.",
            peer,
            op_name,
        )
        raise OnvifError("s:Sender", "ter:ActionNotSupported", f"Unsupported operation: {op_name}", http_status=400)

    if op_name in UNAUTHENTICATED_OPERATIONS:
        logger.debug("ONVIF: %s called %s (no authentication required)", peer, op_name)
    else:
        ok, reason = check_security_header(envelope, ctx.settings.stream_username, ctx.settings.stream_password)
        if not ok:
            logger.warning("ONVIF: refused %s from %s - %s", op_name, peer, reason)
            raise NotAuthorized()
        logger.debug("ONVIF: %s called %s, authenticated by %s", peer, op_name, reason)

    return soap_envelope(handler(ctx, op_elem))


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
