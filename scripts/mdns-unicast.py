#!/usr/bin/env python3
"""Answer "<hostname>.local" lookups directly to the device that asked.

avahi-daemon answers mDNS queries the standard way, by multicast to
224.0.0.251. Some home routers never deliver multicast or broadcast to
their Wi-Fi clients, so a laptop or tablet on Wi-Fi asks, avahi answers,
and the answer never arrives. This helper listens next to avahi and, for
a query about this machine's own name, also sends the A record straight
to the asker's port 5353. avahi keeps doing everything else (services,
IPv6, probing, conflicts); this only adds a second copy of one answer.

A normal unicast reply is not enough on those routers: to send it the
kernel first asks for the asker's hardware address by ARP broadcast,
which never arrives either, so after a restart the reply waits until the
asker happens to contact this machine. The helper therefore reads the
query through a packet socket, which also gives the asker's hardware
address, and sends the reply as a complete IPv4 frame to that address,
with no ARP lookup. A kernel filter passes only mDNS queries to the
socket, so other traffic never reaches Python.

Standard library only. The packet socket needs CAP_NET_RAW, and nothing
else; the unit grants only that.
"""

from __future__ import annotations

import array
import errno
import fcntl
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
REJOIN_SECONDS = 30
MIN_GAP_SECONDS = 1.0

ETH_P_IP = 0x0800
IPPROTO_UDP = 17
SO_ATTACH_FILTER = 26
SIOCGIFADDR = 0x8915
PACKET_MULTICAST = 2

# Classic BPF, run by the kernel on each IPv4 packet (offsets from the IP
# header): keep unfragmented UDP to 224.0.0.251 port 5353, drop the rest.
QUERY_FILTER = [
    (0x30, 0, 0, 9),           # 0  load the protocol byte
    (0x15, 0, 8, IPPROTO_UDP), # 1  UDP? else drop
    (0x20, 0, 0, 16),          # 2  load the destination address
    (0x15, 0, 6, 0xE00000FB),  # 3  224.0.0.251? else drop
    (0x28, 0, 0, 6),           # 4  load flags and fragment offset
    (0x45, 4, 0, 0x1FFF),      # 5  a later fragment? drop
    (0xB1, 0, 0, 0),           # 6  X = IP header length
    (0x48, 0, 0, 2),           # 7  load the UDP destination port
    (0x15, 0, 1, MDNS_PORT),   # 8  5353? else drop
    (0x06, 0, 0, 0xFFFF),      # 9  keep
    (0x06, 0, 0, 0),           # 10 drop
]

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
    """True if packet is a query asking for hostname.local's IPv4 address.
    Questions with the QU bit count too: avahi answers those by unicast,
    which needs the same ARP lookup."""
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
                    and qclass & 0x7FFF in (CLASS_IN, TYPE_ANY)):
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


def checksum(data: bytes) -> int:
    """The Internet checksum (RFC 1071) of data."""
    if len(data) % 2:
        data += b"\0"
    total: int = sum(struct.unpack(f">{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def parse_query(packet: bytes) -> tuple[str, int, bytes] | None:
    """Split an IPv4 packet carrying UDP into (source address, source port,
    UDP payload), or None if it is not one."""
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return None
    header_length = (packet[0] & 0x0F) * 4
    total_length = struct.unpack(">H", packet[2:4])[0]
    if header_length < 20 or packet[9] != IPPROTO_UDP or total_length > len(packet):
        return None
    udp = packet[header_length:total_length]
    if len(udp) < 8:
        return None
    source_port, _, udp_length = struct.unpack(">HHH", udp[:6])
    if udp_length < 8 or udp_length > len(udp):
        return None
    return socket.inet_ntoa(packet[12:16]), source_port, udp[8:udp_length]


def build_packet(payload: bytes, source: str, destination: str) -> bytes:
    """An IPv4 packet carrying payload as UDP from source:5353 to
    destination:5353 (queriers drop answers from any other port)."""
    src, dst = socket.inet_aton(source), socket.inet_aton(destination)
    udp_length = 8 + len(payload)
    pseudo = src + dst + struct.pack(">BBH", 0, IPPROTO_UDP, udp_length)
    udp = struct.pack(">HHHH", MDNS_PORT, MDNS_PORT, udp_length, 0) + payload
    udp_sum = checksum(pseudo + udp) or 0xFFFF
    udp = udp[:6] + struct.pack(">H", udp_sum) + udp[8:]
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + udp_length, 0, 0x4000,
                     255, IPPROTO_UDP, 0, src, dst)
    ip = ip[:10] + struct.pack(">H", checksum(ip)) + ip[12:]
    return ip + udp


def attach_query_filter(sock: socket.socket) -> None:
    code = array.array("B", b"".join(struct.pack("=HBBI", *op) for op in QUERY_FILTER))
    address, _ = code.buffer_info()
    sock.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER,
                    struct.pack("HP", len(QUERY_FILTER), address))


def interface_address(sock: socket.socket, ifname: str) -> str | None:
    """The IPv4 address of interface ifname, or None if it has none."""
    try:
        reply = fcntl.ioctl(sock.fileno(), SIOCGIFADDR, struct.pack("256s", ifname.encode()[:15]))
    except OSError:
        return None
    return socket.inet_ntoa(reply[20:24])


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


def serve() -> None:
    # The packet socket sees every frame the network card accepts; this
    # socket makes it accept the mDNS group on every interface.
    membership = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(ETH_P_IP))
    attach_query_filter(sock)
    sock.settimeout(REJOIN_SECONDS)
    last_join = 0.0
    last_sent: dict[str, float] = {}
    while True:
        now = time.monotonic()
        if now - last_join >= REJOIN_SECONDS:
            join_all(membership)
            last_join = now
        try:
            packet, (ifname, _, pkttype, _, mac) = sock.recvfrom(9000)
        except socket.timeout:
            continue
        if pkttype != PACKET_MULTICAST or len(mac) != 6 or ifname == "lo":
            continue
        query = parse_query(packet)
        if query is None:
            continue
        peer, port, payload = query
        if port != MDNS_PORT or peer == "0.0.0.0":
            continue
        hostname = socket.gethostname().split(".")[0]
        if not wants_our_address(payload, hostname):
            continue
        address = interface_address(membership, ifname)
        if address is None or address == peer:
            continue
        now = time.monotonic()
        if now - last_sent.get(peer, 0.0) < MIN_GAP_SECONDS:
            continue
        if len(last_sent) > 256:
            last_sent.clear()
        last_sent[peer] = now
        answer = build_packet(build_answer(hostname, address), address, peer)
        try:
            sock.sendto(answer, (ifname, ETH_P_IP, 0, 0, mac))
        except OSError as e:
            logger.debug("answer to %s failed: %s", peer, e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    serve()
