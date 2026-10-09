#!/usr/bin/env python3
"""Answer "<hostname>.local" lookups directly to the device that asked.

avahi-daemon answers mDNS queries the standard way, by multicast to
224.0.0.251. Some home routers never deliver multicast to their Wi-Fi
clients, so a laptop or tablet on Wi-Fi asks, avahi answers, and the
answer never arrives. This helper listens next to avahi and, for a query
about this machine's own name, also sends the A record straight to the
asker's port 5353. avahi keeps doing everything else (services, IPv6,
probing, conflicts); this only adds a second copy of one answer.

Standard library only, no privileges: port 5353 is shared with avahi
through SO_REUSEADDR. The listener is bound to the multicast group, so
unicast packets for avahi still go only to avahi.
"""

from __future__ import annotations

import errno
import logging
import socket
import struct
import time

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
TTL = 120
TYPE_A = 1
TYPE_ANY = 255
CLASS_IN = 1
CACHE_FLUSH = 0x8000
QU_BIT = 0x8000
REJOIN_SECONDS = 30
MIN_GAP_SECONDS = 1.0

logger = logging.getLogger("curatables-mdns-unicast")


class BadPacket(ValueError):
    pass


def read_name(buf: bytes, offset: int) -> tuple[str, int]:
    """Decode a DNS name (with compression) at offset. Returns the name
    and the offset just after it in the original position."""
    labels: list[str] = []
    end = None
    jumps = 0
    while True:
        if offset >= len(buf):
            raise BadPacket("name runs past the packet")
        length = buf[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(buf):
                raise BadPacket("truncated pointer")
            jumps += 1
            if jumps > 16:
                raise BadPacket("pointer loop")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | buf[offset + 1]
            continue
        if length > 63:
            raise BadPacket("label too long")
        start = offset + 1
        offset = start + length
        if offset > len(buf):
            raise BadPacket("label runs past the packet")
        labels.append(buf[start:offset].decode("utf-8", "replace"))
    return ".".join(labels), (end if end is not None else offset)


def wants_our_address(packet: bytes, hostname: str) -> bool:
    """True if packet is a multicast-reply query asking for hostname.local's
    IPv4 address. Questions with the QU bit already get a unicast answer
    from avahi, so they are left alone."""
    if len(packet) < 12:
        return False
    flags, qdcount = struct.unpack(">HH", packet[2:6])
    if flags & 0x8000 or qdcount == 0:
        return False
    target = f"{hostname}.local".lower()
    offset = 12
    try:
        for _ in range(qdcount):
            name, offset = read_name(packet, offset)
            if offset + 4 > len(packet):
                return False
            qtype, qclass = struct.unpack(">HH", packet[offset:offset + 4])
            offset += 4
            if (name.lower() == target and qtype in (TYPE_A, TYPE_ANY)
                    and qclass & 0x7FFF in (CLASS_IN, TYPE_ANY)
                    and not qclass & QU_BIT):
                return True
    except BadPacket:
        return False
    return False


def encode_name(name: str) -> bytes:
    out = b""
    for label in name.split("."):
        raw = label.encode("utf-8")
        out += bytes([len(raw)]) + raw
    return out + b"\0"


def build_answer(hostname: str, address: str) -> bytes:
    header = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0)
    record = (encode_name(f"{hostname}.local")
              + struct.pack(">HHIH", TYPE_A, CLASS_IN | CACHE_FLUSH, TTL, 4)
              + socket.inet_aton(address))
    return header + record


def local_address_towards(peer: str) -> str | None:
    """The address of our interface that reaches peer, so a Pi on both
    Ethernet and Wi-Fi answers with the one the asker can use."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer, MDNS_PORT))
        return str(probe.getsockname()[0])
    except OSError:
        return None
    finally:
        probe.close()


def join_all(sock: socket.socket) -> None:
    for index, name in socket.if_nameindex():
        if name == "lo":
            continue
        mreqn = socket.inet_aton(MDNS_ADDR) + socket.inet_aton("0.0.0.0") + struct.pack("@i", index)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreqn)
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, errno.ENODEV, errno.EADDRNOTAVAIL, errno.EINVAL):
                logger.warning("could not listen on %s: %s", name, e)


def send_answer(packet: bytes, source: str, peer: str) -> None:
    """Send from source:5353 (queriers drop answers from any other port)."""
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        out.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        out.bind((source, MDNS_PORT))
        out.sendto(packet, (peer, MDNS_PORT))
    finally:
        out.close()


def serve() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((MDNS_ADDR, MDNS_PORT))
    sock.settimeout(REJOIN_SECONDS)
    last_join = 0.0
    last_sent: dict[str, float] = {}
    while True:
        now = time.monotonic()
        if now - last_join >= REJOIN_SECONDS:
            join_all(sock)
            last_join = now
        try:
            packet, (peer, port) = sock.recvfrom(9000)
        except socket.timeout:
            continue
        if port != MDNS_PORT:
            continue
        hostname = socket.gethostname().split(".")[0]
        if not wants_our_address(packet, hostname):
            continue
        address = local_address_towards(peer)
        if address is None or address == peer:
            continue
        now = time.monotonic()
        if now - last_sent.get(peer, 0.0) < MIN_GAP_SECONDS:
            continue
        if len(last_sent) > 256:
            last_sent.clear()
        last_sent[peer] = now
        try:
            send_answer(build_answer(hostname, address), address, peer)
        except OSError as e:
            logger.debug("answer to %s failed: %s", peer, e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    serve()
