#!/usr/bin/env python3
"""Compare two RTSP streams packet by packet, and say where they differ.

The companion to compare_onvif.py, one layer down. Written for the case where
an NVR has finished with ONVIF - it asked for the profiles, got the stream URI,
opened the RTSP session and is reading it - and still shows no picture. At that
point the ONVIF description is not the problem and the bytes are, so this
plays both streams and reports what is actually on the wire:

    python3 tools/compare_rtsp.py \\
        --a rtsp://viewer:secret@192.168.61.190:554/stream \\
        --b rtsp://admin:secret@192.168.50.175:554/h264Preview_01_main

Device B should be a camera the NVR does display. Everything reported is
measured from the session: the SDP verbatim, the RTP headers, the H.264 NAL
units and the sequence parameter set the decoder is handed.

Only the standard library is used, so it runs from any machine that can reach
both devices. Credentials are taken from the URLs (Basic and Digest are both
supported). A device is asked exactly once more after an authentication
failure - never in a loop - because real cameras lock an account out.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import re
import socket
import struct
import sys
import time
import urllib.parse

USER_AGENT = "rtsp-compare"


class AuthFailed(Exception):
    """The device refused the credentials. Never retried - cameras lock out."""


class RtspError(Exception):
    pass


# ---------------------------------------------------------------------------
# RTSP client (TCP interleaved, which is what NVRs use)
# ---------------------------------------------------------------------------


class RtspSession:
    def __init__(self, url: str, timeout: float = 10.0):
        parsed = urllib.parse.urlsplit(url)
        self.user = urllib.parse.unquote(parsed.username or "")
        self.password = urllib.parse.unquote(parsed.password or "")
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        self.url = urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))
        self.host = parsed.hostname or ""
        self.port = parsed.port or 554
        self.cseq = 0
        self.session_id = ""
        self.challenge: tuple[str, dict] | None = None
        self.buffer = b""
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    # -- transport ---------------------------------------------------------

    def _recv(self, want: int) -> bytes:
        while len(self.buffer) < want:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RtspError("connection closed by the device")
            self.buffer += chunk
        data, self.buffer = self.buffer[:want], self.buffer[want:]
        return data

    def _recv_line(self) -> bytes:
        while b"\r\n" not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RtspError("connection closed by the device")
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\r\n")
        return line

    def read_interleaved(self) -> tuple[int, bytes]:
        """One $-framed binary packet: (channel, payload)."""
        header = self._recv(4)
        if header[0:1] != b"$":
            raise RtspError(f"expected an interleaved frame, got {header!r}")
        channel = header[1]
        length = struct.unpack(">H", header[2:4])[0]
        return channel, self._recv(length)

    def _authorization(self, method: str, url: str) -> str:
        if not self.challenge:
            return ""
        scheme, params = self.challenge
        if scheme == "basic":
            token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            return f"Basic {token}"
        realm, nonce = params.get("realm", ""), params.get("nonce", "")
        ha1 = hashlib.md5(f"{self.user}:{realm}:{self.password}".encode()).hexdigest()  # noqa: S324 - RTSP digest is MD5
        ha2 = hashlib.md5(f"{method}:{url}".encode()).hexdigest()  # noqa: S324
        answer = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()  # noqa: S324
        return (
            f'Digest username="{self.user}", realm="{realm}", nonce="{nonce}", '
            f'uri="{url}", response="{answer}"'
        )

    def request(self, method: str, url: str | None = None, headers: dict | None = None) -> tuple[int, dict, str]:
        url = url or self.url
        response = self._exchange(method, url, headers or {})
        status, response_headers, body = response
        if status == 401:
            self.challenge = _parse_challenge(response_headers.get("www-authenticate", ""))
            if not self.challenge:
                raise AuthFailed(f"{method} refused with 401 and no usable challenge")
            status, response_headers, body = self._exchange(method, url, headers or {})
            if status == 401:
                # Exactly one retry, ever: a real camera locks the account out.
                raise AuthFailed(f"{method} refused with 401 after authenticating - wrong username or password")
        if status >= 400:
            raise RtspError(f"{method} {url} -> {status}")
        return status, response_headers, body

    def _exchange(self, method: str, url: str, extra: dict) -> tuple[int, dict, str]:
        self.cseq += 1
        lines = [f"{method} {url} RTSP/1.0", f"CSeq: {self.cseq}", f"User-Agent: {USER_AGENT}"]
        if self.session_id:
            lines.append(f"Session: {self.session_id}")
        authorization = self._authorization(method, url)
        if authorization:
            lines.append(f"Authorization: {authorization}")
        for key, value in extra.items():
            lines.append(f"{key}: {value}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

        while True:
            line = self._recv_line()
            if line.startswith(b"RTSP/1.0"):
                break
            if line.startswith(b"$"):  # data raced ahead of the reply; skip that frame
                self.buffer = line + b"\r\n" + self.buffer
                self.read_interleaved()
        status = int(line.split()[1])
        headers: dict[str, str] = {}
        while True:
            line = self._recv_line()
            if not line:
                break
            key, _, value = line.decode("utf-8", "replace").partition(":")
            headers[key.strip().lower()] = value.strip()
        body = ""
        length = int(headers.get("content-length", "0") or 0)
        if length:
            body = self._recv(length).decode("utf-8", "replace")
        if "session" in headers and not self.session_id:
            self.session_id = headers["session"].split(";")[0].strip()
        return status, headers, body


def _parse_challenge(header: str) -> tuple[str, dict] | None:
    if not header:
        return None
    scheme, _, rest = header.strip().partition(" ")
    params = dict(re.findall(r'(\w+)="([^"]*)"', rest))
    return scheme.lower(), params


# ---------------------------------------------------------------------------
# SDP
# ---------------------------------------------------------------------------


class Media:
    def __init__(self, kind: str, payload_type: int):
        self.kind = kind
        self.payload_type = payload_type
        self.control = ""
        self.rtpmap = ""
        self.fmtp = ""
        self.attributes: list[str] = []
        self.bandwidth = ""

    @property
    def clock_rate(self) -> int:
        match = re.search(r"/(\d+)", self.rtpmap)
        return int(match.group(1)) if match else 90000


def parse_sdp(sdp: str) -> tuple[list[str], list[Media]]:
    session_lines: list[str] = []
    media: list[Media] = []
    current: Media | None = None
    for raw in sdp.splitlines():
        line = raw.strip()
        if line.startswith("m="):
            parts = line[2:].split()
            current = Media(parts[0], int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else -1)
            media.append(current)
            continue
        if current is None:
            session_lines.append(line)
            continue
        if line.startswith("a=control:"):
            current.control = line[len("a=control:"):]
        elif line.startswith("a=rtpmap:"):
            current.rtpmap = line[len("a=rtpmap:"):]
        elif line.startswith("a=fmtp:"):
            current.fmtp = line[len("a=fmtp:"):]
        elif line.startswith("b="):
            current.bandwidth = line
        if line.startswith("a="):
            current.attributes.append(line)
    return session_lines, media


def resolve_control(base: str, content_base: str, session_control: str, control: str) -> str:
    if control.startswith("rtsp://"):
        return control
    root = content_base or (session_control if session_control.startswith("rtsp://") else "") or base
    if not control or control == "*":
        return root.rstrip("/")
    return root.rstrip("/") + "/" + control.lstrip("/")


# ---------------------------------------------------------------------------
# H.264: NAL units and the sequence parameter set
# ---------------------------------------------------------------------------

NAL_NAMES = {1: "non-IDR", 5: "IDR", 6: "SEI", 7: "SPS", 8: "PPS", 9: "AUD", 12: "filler"}


class BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def bit(self) -> int:
        byte = self.data[self.pos >> 3]
        value = (byte >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return value

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def ue(self) -> int:
        zeros = 0
        while self.bit() == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("malformed Exp-Golomb code")
        return (1 << zeros) - 1 + (self.bits(zeros) if zeros else 0)

    def se(self) -> int:
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)


def unescape_rbsp(data: bytes) -> bytes:
    out = bytearray()
    zeros = 0
    for byte in data:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def parse_sps(nal: bytes) -> dict:
    """Decode the fields of a sequence parameter set a decoder actually acts on."""
    reader = BitReader(unescape_rbsp(nal[1:]))
    info: dict = {}
    profile_idc = reader.bits(8)
    constraints = reader.bits(8)
    level_idc = reader.bits(8)
    info["profile_idc"] = profile_idc
    info["constraint_flags"] = f"0x{constraints:02x}"
    info["level"] = f"{level_idc // 10}.{level_idc % 10}"
    info["profile"] = {66: "Baseline", 77: "Main", 88: "Extended", 100: "High"}.get(profile_idc, str(profile_idc))
    if profile_idc == 66 and constraints & 0x40:
        info["profile"] = "Constrained Baseline"
    reader.ue()  # seq_parameter_set_id
    if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma_format_idc = reader.ue()
        if chroma_format_idc == 3:
            reader.bit()
        reader.ue()
        reader.ue()
        reader.bit()
        if reader.bit():  # seq_scaling_matrix_present_flag
            for i in range(8 if chroma_format_idc != 3 else 12):
                if reader.bit():
                    size = 16 if i < 6 else 64
                    last_scale, next_scale = 8, 8
                    for _ in range(size):
                        if next_scale:
                            next_scale = (last_scale + reader.se() + 256) % 256
                        last_scale = next_scale or last_scale
    info["log2_max_frame_num"] = reader.ue() + 4
    pic_order_cnt_type = reader.ue()
    info["pic_order_cnt_type"] = pic_order_cnt_type
    if pic_order_cnt_type == 0:
        reader.ue()
    elif pic_order_cnt_type == 1:
        reader.bit()
        reader.se()
        reader.se()
        for _ in range(reader.ue()):
            reader.se()
    info["max_num_ref_frames"] = reader.ue()
    info["gaps_in_frame_num_allowed"] = bool(reader.bit())
    width_mbs = reader.ue() + 1
    height_map = reader.ue() + 1
    frame_mbs_only = reader.bit()
    info["frame_mbs_only"] = bool(frame_mbs_only)
    if not frame_mbs_only:
        reader.bit()
    reader.bit()  # direct_8x8_inference_flag
    crop_left = crop_right = crop_top = crop_bottom = 0
    if reader.bit():  # frame_cropping_flag
        crop_left, crop_right, crop_top, crop_bottom = reader.ue(), reader.ue(), reader.ue(), reader.ue()
    width = width_mbs * 16 - (crop_left + crop_right) * 2
    height = (2 - frame_mbs_only) * height_map * 16 - (crop_top + crop_bottom) * 2
    info["resolution"] = f"{width}x{height}"
    info["vui"] = "absent"
    if reader.bit():  # vui_parameters_present_flag
        vui = ["present"]
        if reader.bit():  # aspect_ratio_info_present_flag
            if reader.bits(8) == 255:
                reader.bits(16)
                reader.bits(16)
        if reader.bit():  # overscan_info_present_flag
            reader.bit()
        if reader.bit():  # video_signal_type_present_flag
            reader.bits(3)
            reader.bit()
            if reader.bit():  # colour_description_present_flag
                reader.bits(24)
        if reader.bit():  # chroma_loc_info_present_flag
            reader.ue()
            reader.ue()
        if reader.bit():  # timing_info_present_flag
            num_units_in_tick = reader.bits(32)
            time_scale = reader.bits(32)
            fixed = reader.bit()
            rate = time_scale / (2 * num_units_in_tick) if num_units_in_tick else 0
            vui.append(f"timing {rate:g} fps{', fixed rate' if fixed else ''}")
        else:
            vui.append("no timing info")
        info["vui"] = ", ".join(vui)
    return info


def split_nals(payload: bytes) -> list[bytes]:
    """The NAL units carried by one RTP payload (single, STAP-A or FU-A)."""
    if not payload:
        return []
    kind = payload[0] & 0x1F
    if kind == 24:  # STAP-A
        out, offset = [], 1
        while offset + 2 <= len(payload):
            size = struct.unpack(">H", payload[offset:offset + 2])[0]
            offset += 2
            out.append(payload[offset:offset + size])
            offset += size
        return out
    if kind == 28:  # FU-A - only the first fragment names the unit
        if len(payload) < 2 or not payload[1] & 0x80:
            return []
        header = bytes([(payload[0] & 0xE0) | (payload[1] & 0x1F)])
        return [header + payload[2:]]
    return [payload]


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def play_and_measure(url: str, seconds: float, verbose: bool = False) -> dict:
    session = RtspSession(url)
    result: dict = {"url": url}
    try:
        _, headers, _ = session.request("OPTIONS")
        result["options"] = headers.get("public", "(not reported)")
        _, headers, sdp = session.request("DESCRIBE", headers={"Accept": "application/sdp"})
        result["sdp"] = sdp
        content_base = headers.get("content-base", "") or headers.get("content-location", "")
        session_lines, media = parse_sdp(sdp)
        result["session_lines"] = session_lines
        result["media"] = media
        session_control = next((line[len("a=control:"):] for line in session_lines if line.startswith("a=control:")), "")
        result["session_control"] = session_control or "(none)"

        channels: dict[int, Media] = {}
        for index, track in enumerate(media):
            control = resolve_control(session.url, content_base, session_control, track.control)
            interleaved = f"{index * 2}-{index * 2 + 1}"
            _, setup_headers, _ = session.request(
                "SETUP", control, {"Transport": f"RTP/AVP/TCP;unicast;interleaved={interleaved}"}
            )
            channels[index * 2] = track
            result.setdefault("transport", setup_headers.get("transport", ""))
            result.setdefault("timeout", setup_headers.get("session", ""))

        _, play_headers, _ = session.request("PLAY", headers={"Range": "npt=0.000-"})
        result["play_headers"] = {k: v for k, v in play_headers.items() if k in ("rtp-info", "range")}

        stats = {track: {"packets": 0, "bytes": 0, "timestamps": [], "markers": 0, "nals": {}, "first": None,
                         "sps": None, "seq": [], "ssrc": None, "pt": None,
                         "units": {}, "unit_order": []}
                 for track in media}
        rtcp = {track: {"sr": 0, "first_sr": None} for track in media}
        started = time.monotonic()
        deadline = started + seconds
        session.sock.settimeout(max(2.0, seconds))
        while time.monotonic() < deadline:
            try:
                channel, data = session.read_interleaved()
            except (socket.timeout, RtspError):
                break
            now = time.monotonic() - started
            track = channels.get(channel & 0xFE)
            if track is None or len(data) < 8:
                continue
            if channel % 2:  # RTCP
                if data[1] == 200:  # sender report
                    entry = rtcp[track]
                    entry["sr"] += 1
                    if entry["first_sr"] is None:
                        entry["first_sr"] = now
                continue
            entry = stats[track]
            entry["packets"] += 1
            entry["bytes"] += len(data)
            entry["pt"] = data[1] & 0x7F
            entry["markers"] += 1 if data[1] & 0x80 else 0
            entry["seq"].append(struct.unpack(">H", data[2:4])[0])
            entry["timestamps"].append(struct.unpack(">I", data[4:8])[0])
            entry["ssrc"] = struct.unpack(">I", data[8:12])[0] if len(data) >= 12 else None
            if entry["first"] is None:
                entry["first"] = now
            csrc = data[0] & 0x0F
            payload = data[12 + csrc * 4:]
            if track.kind != "video":
                continue
            timestamp = struct.unpack(">I", data[4:8])[0]
            if timestamp not in entry["units"]:
                entry["units"][timestamp] = []
                entry["unit_order"].append(timestamp)
            for nal in split_nals(payload):
                if not nal:
                    continue
                kind = nal[0] & 0x1F
                name = NAL_NAMES.get(kind, f"type {kind}")
                entry["nals"][name] = entry["nals"].get(name, 0) + 1
                entry["units"][timestamp].append(kind)
                if kind == 7 and entry["sps"] is None:
                    try:
                        entry["sps"] = parse_sps(nal)
                    except (ValueError, IndexError) as err:
                        entry["sps"] = {"error": f"could not parse: {err}"}
        result["stats"] = stats
        result["rtcp"] = rtcp
        result["duration"] = min(time.monotonic() - started, seconds)
        try:
            session.request("TEARDOWN")
        except (RtspError, AuthFailed, OSError):
            pass
    finally:
        session.close()
    return result


def sps_from_fmtp(fmtp: str) -> dict | None:
    match = re.search(r"sprop-parameter-sets=([^;\s]+)", fmtp)
    if not match:
        return None
    for part in match.group(1).split(","):
        try:
            nal = base64.b64decode(part + "==")
        except ValueError:
            continue
        if nal and nal[0] & 0x1F == 7:
            try:
                return parse_sps(nal)
            except (ValueError, IndexError):
                return None
    return None


def summarise(result: dict) -> dict:
    """Flatten one measurement into comparable facts."""
    facts: dict[str, str] = {}
    facts["RTSP methods"] = result.get("options", "")
    facts["session a=control"] = result.get("session_control", "")
    for line in result.get("session_lines", []):
        if line.startswith(("a=range:", "b=", "a=tool:", "a=type:")):
            facts[f"session {line.split(':')[0].split('=')[0]}={line.split('=', 1)[0]}"] = line
    facts["session-level attributes"] = ", ".join(
        line for line in result.get("session_lines", []) if line.startswith(("a=", "b="))
    ) or "(none)"
    facts["Transport granted"] = result.get("transport", "")
    facts["RTP-Info"] = result.get("play_headers", {}).get("rtp-info", "(none)")
    duration = result.get("duration", 0) or 1

    for index, track in enumerate(result.get("media", [])):
        prefix = f"track {index} ({track.kind})"
        entry = result["stats"][track]
        facts[f"{prefix} rtpmap"] = track.rtpmap or "(none)"
        facts[f"{prefix} fmtp"] = track.fmtp or "(none)"
        facts[f"{prefix} bandwidth"] = track.bandwidth or "(none)"
        facts[f"{prefix} direction"] = next(
            (a for a in track.attributes if a in ("a=recvonly", "a=sendonly", "a=sendrecv", "a=inactive")), "(none)"
        )
        facts[f"{prefix} control"] = track.control or "(none)"
        facts[f"{prefix} payload type"] = str(entry["pt"])
        facts[f"{prefix} packets/s"] = f"{entry['packets'] / duration:.1f}"
        facts[f"{prefix} kbit/s"] = f"{entry['bytes'] * 8 / duration / 1000:.0f}"
        facts[f"{prefix} first packet after"] = f"{entry['first']:.2f}s" if entry["first"] is not None else "never"
        unique = sorted(set(entry["timestamps"]))
        if len(unique) > 1:
            deltas = [b - a for a, b in zip(unique, unique[1:])]
            median = sorted(deltas)[len(deltas) // 2]
            facts[f"{prefix} frames/s (from RTP timestamps)"] = (
                f"{track.clock_rate / median:.2f}" if median else "n/a"
            )
            facts[f"{prefix} timestamp step"] = f"{median} ({track.clock_rate} Hz clock)"
        facts[f"{prefix} marker bits"] = f"{entry['markers']} in {entry['packets']} packets"
        sequence = entry["seq"]
        gaps = sum(1 for a, b in zip(sequence, sequence[1:]) if (b - a) & 0xFFFF != 1)
        facts[f"{prefix} sequence gaps"] = str(gaps)
        rtcp_entry = result["rtcp"][track]
        facts[f"{prefix} RTCP sender reports"] = (
            f"{rtcp_entry['sr']}, first after {rtcp_entry['first_sr']:.2f}s"
            if rtcp_entry["sr"] else "none received"
        )
        if track.kind != "video":
            continue
        facts[f"{prefix} NAL units"] = ", ".join(f"{k}x{v}" for k, v in sorted(entry["nals"].items())) or "(none)"

        # An access unit is one frame: all the NAL units sharing an RTP
        # timestamp. Counting slices instead of frames is what makes a
        # multi-slice encoder look like it sends keyframes eight times a
        # second, so everything below is per access unit.
        units = entry["units"]
        order = entry["unit_order"]
        facts[f"{prefix} frames (access units)"] = str(len(units))
        slices = sorted(sum(1 for kind in kinds if kind in (1, 5)) for kinds in units.values())
        if slices:
            facts[f"{prefix} slices per frame"] = (
                f"{slices[len(slices) // 2]} (min {slices[0]}, max {slices[-1]})"
            )
        idr = [ts for ts in order if 5 in units[ts]]
        facts[f"{prefix} keyframes"] = str(len(idr))
        if len(idr) > 1:
            spacing = [(b - a) / track.clock_rate for a, b in zip(idr, idr[1:])]
            facts[f"{prefix} keyframe interval"] = f"{sum(spacing) / len(spacing):.2f}s"
        # A decoder that joins a running stream has only what the stream
        # itself carries. A camera repeats SPS and PPS ahead of every
        # keyframe; an encoder that leaves them in the SDP alone plays fine
        # in a media player and can stay black on an NVR.
        if not idr:
            facts[f"{prefix} parameter sets in-band"] = "no keyframe seen"
        elif all(7 in units[ts] and 8 in units[ts] for ts in idr):
            facts[f"{prefix} parameter sets in-band"] = "SPS+PPS with every keyframe"
        elif any(7 in kinds for kinds in units.values()):
            facts[f"{prefix} parameter sets in-band"] = "sometimes - not with every keyframe"
        else:
            facts[f"{prefix} parameter sets in-band"] = "never (only in the SDP's sprop-parameter-sets)"
        sps = entry["sps"] or sps_from_fmtp(track.fmtp)
        facts[f"{prefix} SPS source"] = "in-band" if entry["sps"] else (
            "SDP only (not repeated in the stream)" if sps else "none found"
        )
        if sps:
            for key, value in sps.items():
                facts[f"{prefix} SPS {key}"] = str(value)
    return facts


def report(label_a: str, label_b: str, a: dict, b: dict, show_same: bool) -> None:
    keys = list(a)
    keys += [key for key in b if key not in a]
    same, differ = [], []
    for key in keys:
        left, right = a.get(key, "(not present)"), b.get(key, "(not present)")
        (same if left == right else differ).append((key, left, right))

    print(f"\n=== differences ({len(differ)}) ===")
    print(f"A = {label_a}\nB = {label_b}\n")
    for key, left, right in differ:
        print(f"{key}:")
        print(f"    A: {left}")
        print(f"    B: {right}")
    if show_same:
        print(f"\n=== identical ({len(same)}) ===")
        for key, left, _ in same:
            print(f"{key}: {left}")
    else:
        print(f"\n({len(same)} facts identical; --show-same prints them too)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", required=True, help="RTSP URL of device A (credentials in the URL)")
    parser.add_argument("--b", required=True, help="RTSP URL of device B - the one that works")
    parser.add_argument("--seconds", type=float, default=8.0, help="how long to read each stream (default 8)")
    parser.add_argument("--show-same", action="store_true", help="also list the facts that match")
    parser.add_argument("--sdp", action="store_true", help="print both SDPs verbatim")
    args = parser.parse_args()

    measurements = {}
    for label, url in (("A", args.a), ("B", args.b)):
        print(f"reading {label}: {_redact(url)} for {args.seconds:g}s ...", flush=True)
        try:
            measurements[label] = play_and_measure(url, args.seconds)
        except AuthFailed as err:
            print(f"  {label}: {err}")
            print("  Not retrying - repeated failures lock a camera account out. Check the credentials.")
            return 1
        except (RtspError, OSError) as err:
            print(f"  {label}: {err}")
            return 1

    if args.sdp:
        for label in ("A", "B"):
            print(f"\n=== SDP {label} ===\n{measurements[label]['sdp'].strip()}")

    report(_redact(args.a), _redact(args.b), summarise(measurements["A"]), summarise(measurements["B"]), args.show_same)
    return 0


def _redact(url: str) -> str:
    return re.sub(r"//[^/@]*@", "//<credentials>@", url)


if __name__ == "__main__":
    sys.exit(main())
