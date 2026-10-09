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


@pytest.mark.parametrize("packet", [
    _query(("other.local", 1, 1)),
    _query(("curatables.local", 28, 1)),           # AAAA: avahi's job
    _query(("curatables.local", 1, 1 | 0x8000)),   # QU: avahi already answers directly
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


def test_picks_the_local_address_that_reaches_the_asker():
    assert mdns.local_address_towards("127.0.0.1") == "127.0.0.1"
