#!/usr/bin/env python3
"""Check this app's ONVIF responses against the official ONVIF schema.

The responses are written by hand, so nothing stops a mandatory element from
going missing - and a strict client (an NVR's ONVIF stack is usually generated
from the WSDL) then fails to deserialise the answer and reports something
unhelpful like "cannot load live feed", while a plain RTSP player is perfectly
happy. This walks every response, checks the mandatory child elements and the
element order of every ONVIF type it recognises, and exits non-zero on a
finding.

    python3 tools/check_onvif_schema.py [path/to/onvif.xsd]

Without an argument the schema is downloaded from the ONVIF specs repository.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "dashboard_stream" / "app"
sys.path.insert(0, str(APP))

import onvif  # noqa: E402
from config import Settings  # noqa: E402

SCHEMA_URL = "https://raw.githubusercontent.com/onvif/specs/master/wsdl/ver10/schema/onvif.xsd"
XS = "{http://www.w3.org/2001/XMLSchema}"
TT = "{http://www.onvif.org/ver10/schema}"
TRT = "{http://www.onvif.org/ver10/media/wsdl}"
TDS = "{http://www.onvif.org/ver10/device/wsdl}"

# response operation, namespace, element to inspect, ONVIF type it must match
CHECKS = [
    ("GetProfiles", onvif.TRT_NS, TRT + "Profiles", "Profile"),
    ("GetProfile", onvif.TRT_NS, TRT + "Profile", "Profile"),
    ("GetVideoSources", onvif.TRT_NS, TRT + "VideoSources", "VideoSource"),
    ("GetVideoEncoderConfigurations", onvif.TRT_NS, TRT + "Configurations", "VideoEncoderConfiguration"),
    ("GetVideoEncoderConfiguration", onvif.TRT_NS, TRT + "Configuration", "VideoEncoderConfiguration"),
    ("GetVideoEncoderConfigurationOptions", onvif.TRT_NS, TRT + "Options", "VideoEncoderConfigurationOptions"),
    ("GetVideoSourceConfigurations", onvif.TRT_NS, TRT + "Configurations", "VideoSourceConfiguration"),
    ("GetVideoSourceConfiguration", onvif.TRT_NS, TRT + "Configuration", "VideoSourceConfiguration"),
    ("GetVideoSourceConfigurationOptions", onvif.TRT_NS, TRT + "Options", "VideoSourceConfigurationOptions"),
    ("GetAudioSources", onvif.TRT_NS, TRT + "AudioSources", "AudioSource"),
    ("GetAudioEncoderConfigurations", onvif.TRT_NS, TRT + "Configurations", "AudioEncoderConfiguration"),
    ("GetAudioEncoderConfiguration", onvif.TRT_NS, TRT + "Configuration", "AudioEncoderConfiguration"),
    ("GetAudioEncoderConfigurationOptions", onvif.TRT_NS, TRT + "Options", "AudioEncoderConfigurationOptions"),
    ("GetAudioSourceConfigurations", onvif.TRT_NS, TRT + "Configurations", "AudioSourceConfiguration"),
    ("GetAudioSourceConfiguration", onvif.TRT_NS, TRT + "Configuration", "AudioSourceConfiguration"),
    ("GetStreamUri", onvif.TRT_NS, TRT + "MediaUri", "MediaUri"),
    ("GetSnapshotUri", onvif.TRT_NS, TRT + "MediaUri", "MediaUri"),
    ("GetCapabilities", onvif.TDS_NS, TDS + "Capabilities", "Capabilities"),
    ("GetNetworkInterfaces", onvif.TDS_NS, TDS + "NetworkInterfaces", "NetworkInterface"),
    ("GetNetworkProtocols", onvif.TDS_NS, TDS + "NetworkProtocols", "NetworkProtocol"),
    ("GetHostname", onvif.TDS_NS, TDS + "HostnameInformation", "HostnameInformation"),
    ("GetScopes", onvif.TDS_NS, TDS + "Scopes", "Scope"),
    ("GetSystemDateAndTime", onvif.TDS_NS, TDS + "SystemDateAndTime", "SystemDateAndTime"),
]

PASSWORD = "checkpass"


def load_types(schema_path: Path) -> dict[str, ET.Element]:
    root = ET.parse(schema_path).getroot()
    return {t.get("name"): t for t in root.findall(f"{XS}complexType")}


def sequence(types: dict[str, ET.Element], type_name: str, seen: set[str] | None = None) -> list[tuple[str, str, str]]:
    """The element sequence of an ONVIF type, base types first."""
    seen = seen or set()
    if type_name in seen or type_name not in types:
        return []
    seen.add(type_name)
    node = types[type_name]
    result: list[tuple[str, str, str]] = []
    extension = node.find(f"{XS}complexContent/{XS}extension")
    if extension is not None:
        result += sequence(types, extension.get("base", "").split(":")[-1], seen)
        node = extension
    for element in node.findall(f"{XS}sequence/{XS}element"):
        result.append(
            (element.get("name"), (element.get("type") or "").split(":")[-1], element.get("minOccurs", "1"))
        )
    return result


def check(types, element: ET.Element, type_name: str, path: str, findings: list[str]) -> None:
    spec = sequence(types, type_name)
    if not spec:
        return
    order = [name for name, _, _ in spec]
    present = [child.tag.replace(TT, "") for child in element]
    for name, _, min_occurs in spec:
        if min_occurs != "0" and name not in present:
            findings.append(f"{path}: mandatory <{name}> missing from {type_name}")
    positions = [order.index(name) for name in present if name in order]
    if positions != sorted(positions):
        findings.append(f"{path}: element order differs from the schema sequence {order}, got {present}")
    child_types = {name: type_ for name, type_, _ in spec}
    for child in element:
        name = child.tag.replace(TT, "")
        if child_types.get(name) in types:
            check(types, child, child_types[name], f"{path}/{name}", findings)


def build_request(operation: str, namespace: str) -> bytes:
    nonce = b"0123456789abcdef"
    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + PASSWORD.encode()).digest()  # noqa: S324 - WS-Security uses SHA1
    ).decode()
    return (
        f'<s:Envelope xmlns:s="{onvif.SOAP_NS}"><s:Header><Security xmlns="{onvif.WSSE_NS}">'
        f"<UsernameToken><Username>viewer</Username>"
        f'<Password Type="#PasswordDigest">{digest}</Password>'
        f'<Nonce xmlns="{onvif.WSSE_NS}">{base64.b64encode(nonce).decode()}</Nonce>'
        f'<Created xmlns="{onvif.WSU_NS}">{created}</Created>'
        f"</UsernameToken></Security></s:Header>"
        f'<s:Body><{operation} xmlns="{namespace}"/></s:Body></s:Envelope>'
    ).encode()


def make_context(audio_track: str) -> onvif.OnvifContext:
    settings = Settings(
        ha_url="http://homeassistant.local:8123",
        ha_token="",
        dashboard_url="",
        dashboard_path="lovelace/default_view",
        stream_width=1280,
        stream_height=720,
        framerate=15,
        audio_track=audio_track,
        color_scheme="auto",
        render_wait=8,
        reload_interval=0,
        rtsp_port=554,
        onvif_port=8080,
        onvif_extra_port=80,
        onvif_enabled=True,
        onvif_device_name="Dashboard Stream Cam",
        advertise_ip="192.0.2.10",
        stream_username="viewer",
        stream_password=PASSWORD,
        watchdog_interval=15,
        stall_timeout=45,
        log_level="info",
        supervisor_token="",
    )
    return onvif.OnvifContext(
        settings=settings,
        local_ip=settings.advertise_ip,
        device_uuid="3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        snapshot_token="0" * 32,
        mac_address="02:00:00:00:00:01",
    )


def main() -> int:
    if len(sys.argv) > 1:
        schema_path = Path(sys.argv[1])
    else:
        schema_path = Path("/tmp/onvif.xsd")
        if not schema_path.exists():
            print(f"Downloading {SCHEMA_URL}")
            with urllib.request.urlopen(SCHEMA_URL, timeout=60) as response:  # noqa: S310 - fixed https URL
                schema_path.write_bytes(response.read())

    types = load_types(schema_path)
    findings: list[str] = []
    checked = 0
    for audio_track in ("silent", "none"):
        ctx = make_context(audio_track)
        for operation, namespace, tag, type_name in CHECKS:
            try:
                response = onvif.handle_soap_request(build_request(operation, namespace), ctx, peer="schema-check")
            except onvif.OnvifError as err:
                if audio_track == "none" and "audio" in err.reason.lower():
                    continue  # audio queries are expected to refuse when there is no audio
                findings.append(f"{operation} (audio_track={audio_track}): {err.reason}")
                continue
            elements = [e for e in ET.fromstring(response).iter() if e.tag == tag]
            if not elements and not (audio_track == "none" and "Audio" in operation):
                findings.append(f"{operation} (audio_track={audio_track}): no <{tag.split('}')[-1]}> in the response")
            for element in elements:
                check(types, element, type_name, f"{operation} (audio_track={audio_track})", findings)
                checked += 1

    print(f"checked {checked} elements across {len(CHECKS)} operations in both audio modes")
    for finding in findings:
        print(f"  - {finding}")
    print(f"findings: {len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
