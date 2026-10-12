"""scripts/mdns-unicast.py: which queries get a direct answer, and what it says."""

import importlib.util
import socket
import struct
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mdns-unicast.py"
spec = importlib.util.spec_from_file_location("mdns_unicast", SCRIPT)
mdns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mdns)


def _query(*questions, flags=0):
    body = b""
    for name, qtype, qclass in questions:
        body += mdns.encode_name(name) + struct.pack(">HH", qtype, qclass)
    return struct.pack(">HHHHHH", 0, flags, len(questions), 0, 0, 0) + body


def test_answers_a_plain_lookup_for_its_own_name():
    assert mdns.wants_our_address(_query(("curatables.local", 1, 1)), "curatables")


def test_name_match_ignores_case():
    assert mdns.wants_our_address(_query(("Curatables.LOCAL", 255, 1)), "curatables")


def test_answers_a_lookup_that_asks_for_a_unicast_reply():
    assert mdns.wants_our_address(_query(("curatables.local", 1, 1 | 0x8000)), "curatables")


@pytest.mark.parametrize("packet", [
    _query(("other.local", 1, 1)),
    _query(("curatables.local", 28, 1)),           # AAAA: avahi's job
    _query(("curatables.local", 1, 1), flags=0x8400),  # a response, not a query
    _query(),
    b"\x00\x01",
])
def test_leaves_everything_else_to_avahi(packet):
    assert not mdns.wants_our_address(packet, "curatables")


def test_finds_the_name_among_several_questions_with_compression():
    first = mdns.encode_name("_http._tcp.local") + struct.pack(">HH", 12, 1)
    # second question: "curatables" + pointer to "local" inside the first name
    local_offset = 12 + len(b"\x05_http\x04_tcp")
    second = b"\x0acuratables" + struct.pack(">H", 0xC000 | local_offset) + struct.pack(">HH", 1, 1)
    packet = struct.pack(">HHHHHH", 0, 0, 2, 0, 0, 0) + first + second
    assert mdns.wants_our_address(packet, "curatables")


@pytest.mark.parametrize("packet", [
    struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + b"\xc0\x0c" + b"\x00\x01\x00\x01",  # self-pointer
    struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + b"\x0acurat",                     # cut short
    struct.pack(">HHHHHH", 0, 0, 3, 0, 0, 0) + mdns.encode_name("curatables.local"),
])
def test_broken_packets_are_ignored(packet):
    assert not mdns.wants_our_address(packet, "curatables")


def test_answer_is_a_cache_flush_a_record_for_the_given_address():
    packet = mdns.build_answer("curatables", "192.0.2.16")
    flags, qd, an = struct.unpack(">HHH", packet[2:8])
    assert flags == 0x8400 and qd == 0 and an == 1
    name, offset = mdns.read_name(packet, 12)
    assert name == "curatables.local"
    rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", packet[offset:offset + 10])
    assert (rtype, rclass, ttl, rdlen) == (1, 0x8001, 120, 4)
    assert socket.inet_ntoa(packet[offset + 10:offset + 14]) == "192.0.2.16"


def _run_filter(packet):
    """Run QUERY_FILTER the way the kernel does; True if it keeps packet."""
    acc = x = pc = 0
    while True:
        code, jt, jf, k = mdns.QUERY_FILTER[pc]
        pc += 1
        if code == 0x30:
            acc = packet[k]
        elif code == 0x28:
            acc = struct.unpack(">H", packet[k:k + 2])[0]
        elif code == 0x20:
            acc = struct.unpack(">I", packet[k:k + 4])[0]
        elif code == 0x48:
            acc = struct.unpack(">H", packet[x + k:x + k + 2])[0]
        elif code == 0xB1:
            x = (packet[k] & 0x0F) * 4
        elif code == 0x15:
            pc += jt if acc == k else jf
        elif code == 0x45:
            pc += jt if acc & k else jf
        elif code == 0x06:
            return k != 0
        else:
            raise AssertionError(f"opcode {code:#x} not modelled")


def _ip(payload, source="192.0.2.9", destination="224.0.0.251", dport=5353, fragment=0, protocol=17):
    packet = mdns.build_packet(payload, source, destination)
    packet = packet[:6] + struct.pack(">H", fragment) + packet[8:]
    packet = packet[:9] + bytes([protocol]) + packet[10:]
    return packet[:22] + struct.pack(">H", dport) + packet[24:]


def test_filter_keeps_only_mdns_queries():
    query = _query(("curatables.local", 1, 1))
    assert _run_filter(_ip(query))
    assert not _run_filter(_ip(query, destination="192.0.2.16"))
    assert not _run_filter(_ip(query, dport=53))
    assert not _run_filter(_ip(query, protocol=6))
    assert not _run_filter(_ip(query, fragment=0x2000 | 3))


def test_filter_reads_the_port_after_ip_options():
    packet = mdns.build_packet(_query(("curatables.local", 1, 1)), "192.0.2.9", "224.0.0.251")
    length = struct.pack(">H", len(packet) + 4)
    with_options = bytes([0x46]) + packet[1:2] + length + packet[4:20] + b"\x01\x01\x01\x00" + packet[20:]
    assert _run_filter(with_options)
    assert mdns.parse_query(with_options)[2] == _query(("curatables.local", 1, 1))


def test_answer_packet_is_valid_ipv4_udp_between_mdns_ports():
    payload = mdns.build_answer("curatables", "192.0.2.16")
    packet = mdns.build_packet(payload, "192.0.2.16", "192.0.2.9")
    assert mdns.checksum(packet[:20]) == 0
    pseudo = packet[12:20] + struct.pack(">BBH", 0, 17, len(packet) - 20)
    assert mdns.checksum(pseudo + packet[20:]) == 0
    assert packet[8] == 255 and struct.unpack(">H", packet[2:4])[0] == len(packet)
    assert struct.unpack(">HH", packet[20:24]) == (5353, 5353)
    assert mdns.parse_query(packet) == ("192.0.2.16", 5353, payload)


@pytest.mark.parametrize("packet", [
    b"",
    b"\x60" + bytes(39),                                           # IPv6
    _ip(b"x", protocol=6),                                          # TCP
    mdns.build_packet(b"x" * 20, "192.0.2.9", "224.0.0.251")[:30],  # cut short
])
def test_parse_query_rejects_what_is_not_ipv4_udp(packet):
    assert mdns.parse_query(packet) is None


def test_checksum_matches_rfc_1071_example():
    assert mdns.checksum(bytes.fromhex("0001f203f4f5f6f7")) == ~0xDDF2 & 0xFFFF
