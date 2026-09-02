#!/usr/bin/env python3
"""Ask two ONVIF/RTSP devices the same questions and report where they differ.

Written for one situation: an NVR accepts device B (say a Happytime server) and
refuses device A (this app), and nothing in A's own log explains why. Guessing
at the difference has a poor track record, so this asks both and prints it.

    python3 tools/compare_onvif.py \\
        --a http://192.168.61.190:8080 --a-rtsp rtsp://192.168.61.190:554/stream \\
        --b http://192.168.61.200:8000 --b-rtsp rtsp://192.168.61.200:554/stream \\
        --user viewer --password secret

Credentials can be given per device (--a-user/--a-password, --b-user/...) when
they differ. Only the standard library is used, so it runs on any machine that
can reach both devices - a laptop on the same network is the natural place.

Values that must differ (timestamps, UUIDs, addresses, tokens) are normalised
away, so what remains is worth looking at.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
WSSE_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
TDS_NS = "http://www.onvif.org/ver10/device/wsdl"
TRT_NS = "http://www.onvif.org/ver10/media/wsdl"
TEV_NS = "http://www.onvif.org/ver10/events/wsdl"

# operation, namespace, service path
OPERATIONS = [
    ("GetSystemDateAndTime", TDS_NS, "device_service"),
    ("GetDeviceInformation", TDS_NS, "device_service"),
    ("GetCapabilities", TDS_NS, "device_service"),
    ("GetServices", TDS_NS, "device_service"),
    ("GetScopes", TDS_NS, "device_service"),
    ("GetNetworkInterfaces", TDS_NS, "device_service"),
    ("GetServiceCapabilities", TDS_NS, "device_service"),
    ("GetProfiles", TRT_NS, "media_service"),
    ("GetVideoSources", TRT_NS, "media_service"),
    ("GetVideoEncoderConfigurations", TRT_NS, "media_service"),
    ("GetVideoEncoderConfigurationOptions", TRT_NS, "media_service"),
    ("GetAudioEncoderConfigurations", TRT_NS, "media_service"),
    ("GetStreamUri", TRT_NS, "media_service"),
    ("GetSnapshotUri", TRT_NS, "media_service"),
    ("GetServiceCapabilities", TRT_NS, "media_service"),
    ("GetEventProperties", TEV_NS, "events_service"),
    ("GetServiceCapabilities", TEV_NS, "events_service"),
]

# Paths and values that are supposed to differ between two devices
VOLATILE_VALUE = re.compile(
    r"^(?:"
    r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?"           # timestamps
    r"|[0-9a-fA-F-]{16,}"                     # uuids, serials, tokens
    r"|(?:rtsp|http)s?://\S+"                 # addresses
    r"|(?:\d{1,3}\.){3}\d{1,3}"               # IPv4
    r"|(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"  # MAC
    r")$"
)
VOLATILE_PATH = re.compile(r"(Time|Date|Hour|Minute|Second|Year|Month|Day|XAddr|Uri|Address|SerialNumber)$")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def flatten(element: ET.Element, prefix: str = "") -> dict[str, str]:
    """Element tree as path -> value, with repeated siblings numbered."""
    result: dict[str, str] = {}
    counts: dict[str, int] = {}
    for child in element:
        name = local(child.tag)
        counts[name] = counts.get(name, 0) + 1
        index = counts[name]
        path = f"{prefix}/{name}" if index == 1 else f"{prefix}/{name}[{index}]"
        text = (child.text or "").strip()
        if text:
            result[path] = text
        for attribute, value in sorted(child.attrib.items()):
            result[f"{path}@{local(attribute)}"] = value
        result.update(flatten(child, path))
        if not text and not child.attrib and len(child) == 0:
            result[path] = "(empty)"
    return result


def normalise(values: dict[str, str]) -> dict[str, str]:
    out = {}
    for path, value in values.items():
        if VOLATILE_PATH.search(path.split("@")[0]) or VOLATILE_VALUE.match(value):
            value = "<varies>"
        out[path] = value
    return out


def security_header(username: str, password: str) -> str:
    nonce = uuid.uuid4().bytes
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()  # noqa: S324 - required by WS-Security
    ).decode()
    return (
        f'<s:Header><Security s:mustUnderstand="1" xmlns="{WSSE_NS}"><UsernameToken>'
        f"<Username>{username}</Username>"
        f'<Password Type="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>'
        f'<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</Nonce>'
        f'<Created xmlns="{WSU_NS}">{created}</Created>'
        f"</UsernameToken></Security></s:Header>"
    )


def call(base: str, service: str, operation: str, namespace: str, user: str, password: str) -> tuple[str, str]:
    """Returns (status, body). status is "ok" or a short error description."""
    url = f"{base.rstrip('/')}/onvif/{service}"
    envelope = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{SOAP_NS}">{security_header(user, password)}'
        f'<s:Body><{operation} xmlns="{namespace}"/></s:Body></s:Envelope>'
    ).encode()
    request = urllib.request.Request(  # noqa: S310 - user-supplied device URL is the point
        url, data=envelope, headers={"Content-Type": "application/soap+xml; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return "ok", response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")
        reason = ""
        match = re.search(r"<[^>]*Text[^>]*>([^<]+)<", body)
        if match:
            reason = f" ({match.group(1).strip()})"
        return f"HTTP {err.code}{reason}", body
    except (urllib.error.URLError, TimeoutError, socket.timeout) as err:
        return f"unreachable ({err})", ""


AUTH_FAILURE = re.compile(r"HTTP 40[013]|not authorized|incorrect|locked", re.I)


def looks_like_auth_failure(status: str) -> bool:
    return bool(AUTH_FAILURE.search(status))


def body_of(xml: str) -> ET.Element | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    for child in root:
        if local(child.tag) == "Body":
            return child
    return None


def rtsp_describe(url: str, user: str, password: str) -> tuple[str, list[str]]:
    """OPTIONS + DESCRIBE against an RTSP URL, returning (status, sdp lines)."""
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    port = parsed.port or 554
    try:
        sock = socket.create_connection((host, port), timeout=10)
    except OSError as err:
        return f"unreachable ({err})", []

    def exchange(request: str) -> str:
        sock.sendall(request.encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        header, _, rest = data.partition(b"\r\n\r\n")
        length = 0
        match = re.search(rb"Content-Length:\s*(\d+)", header, re.I)
        if match:
            length = int(match.group(1))
        while len(rest) < length:
            chunk = sock.recv(4096)
            if not chunk:
                break
            rest += chunk
        return (header + b"\r\n\r\n" + rest).decode("utf-8", "replace")

    try:
        exchange(f"OPTIONS {url} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: onvif-compare\r\n\r\n")
        response = exchange(f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n\r\n")
        if " 401 " in response.splitlines()[0]:
            challenges = re.findall(r"WWW-Authenticate:\s*(\S+)\s*(.*)", response)
            offered = ", ".join(scheme for scheme, _ in challenges) or "none"
            digest = next((rest for scheme, rest in challenges if scheme.lower() == "digest"), None)
            if not digest:
                return f"401, only {offered} offered", []
            realm = re.search(r'realm="([^"]+)"', digest).group(1)
            nonce = re.search(r'nonce="([^"]+)"', digest).group(1)
            ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()  # noqa: S324 - RTSP digest is MD5
            ha2 = hashlib.md5(f"DESCRIBE:{url}".encode()).hexdigest()  # noqa: S324
            answer = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()  # noqa: S324
            header = (
                f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
                f'uri="{url}", response="{answer}"'
            )
            response = exchange(
                f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 3\r\nAccept: application/sdp\r\n"
                f"Authorization: {header}\r\n\r\n"
            )
        status = response.splitlines()[0].strip()
        sdp = response.partition("\r\n\r\n")[2].strip().splitlines()
        auth = ", ".join(scheme for scheme, _ in re.findall(r"WWW-Authenticate:\s*(\S+)\s*(.*)", response))
        return f"{status}{' | auth offered: ' + auth if auth else ''}", [line.strip() for line in sdp]
    finally:
        sock.close()


def report_operation(operation: str, service: str, a: tuple[str, str], b: tuple[str, str]) -> None:
    (status_a, xml_a), (status_b, xml_b) = a, b
    label = f"{operation} ({service})"
    if status_a != "ok" or status_b != "ok":
        print(f"\n{label}\n  A: {status_a}\n  B: {status_b}")
        return
    body_a, body_b = body_of(xml_a), body_of(xml_b)
    if body_a is None or body_b is None:
        print(f"\n{label}\n  response was not parseable XML on {'A' if body_a is None else 'B'}")
        return
    flat_a = normalise(flatten(body_a))
    flat_b = normalise(flatten(body_b))
    only_a = sorted(set(flat_a) - set(flat_b))
    only_b = sorted(set(flat_b) - set(flat_a))
    differing = sorted(k for k in set(flat_a) & set(flat_b) if flat_a[k] != flat_b[k])
    if not (only_a or only_b or differing):
        print(f"\n{label}: identical")
        return
    print(f"\n{label}")
    for path in only_b:
        print(f"  only B has  {path} = {flat_b[path]}")
    for path in only_a:
        print(f"  only A has  {path} = {flat_a[path]}")
    for path in differing:
        print(f"  differs     {path}: A={flat_a[path]!r} B={flat_b[path]!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", required=True, help="base URL of device A, e.g. http://192.168.1.10:8080")
    parser.add_argument("--b", required=True, help="base URL of device B (the one that works)")
    parser.add_argument("--a-rtsp", help="RTSP URL of device A")
    parser.add_argument("--b-rtsp", help="RTSP URL of device B")
    parser.add_argument("--user", default="", help="username for both devices")
    parser.add_argument("--password", default="", help="password for both devices")
    parser.add_argument("--a-user"), parser.add_argument("--a-password")
    parser.add_argument("--b-user"), parser.add_argument("--b-password")
    args = parser.parse_args()

    a_user, a_password = args.a_user or args.user, args.a_password or args.password
    b_user, b_password = args.b_user or args.user, args.b_password or args.password

    print(f"A = {args.a}   (the one being investigated)")
    print(f"B = {args.b}   (the one that works)")
    print("\nOnly meaningful differences are listed; addresses, timestamps, serials and")
    print("tokens are normalised away because they are supposed to differ.")

    # A real camera will lock an account out after a handful of failed logins,
    # so the first authentication failure stops the questions for that device
    # rather than hammering it with sixteen more.
    stopped: dict[str, str] = {}
    for operation, namespace, service in OPERATIONS:
        results = {}
        for label, base, user, password in (
            ("A", args.a, a_user, a_password),
            ("B", args.b, b_user, b_password),
        ):
            if label in stopped:
                results[label] = (f"skipped after {stopped[label]}", "")
                continue
            status, xml = call(base, service, operation, namespace, user, password)
            if looks_like_auth_failure(status):
                stopped[label] = status
            results[label] = (status, xml)
        report_operation(operation, service, results["A"], results["B"])

    for label, status in stopped.items():
        print(
            f"\n{label} stopped after the first authentication failure ({status}). "
            f"Check the credentials for {label}, and give a locked-out camera a few "
            f"minutes before trying again."
        )

    if args.a_rtsp and args.b_rtsp and not stopped:
        print("\n\n=== RTSP ===")
        status_a, sdp_a = rtsp_describe(args.a_rtsp, a_user, a_password)
        status_b, sdp_b = rtsp_describe(args.b_rtsp, b_user, b_password)
        print(f"A: {status_a}")
        print(f"B: {status_b}")
        for label, status in (("A", status_a), ("B", status_b)):
            if "401" in status:
                print(f"\n{label}: RTSP refused the credentials, so its SDP is missing above.")
        print("\nSDP of A:")
        for line in sdp_a:
            print(f"  {line}")
        print("\nSDP of B:")
        for line in sdp_b:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
