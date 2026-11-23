import csv
import hashlib
import socket
import struct

import dns.flags
import dns.message

from scripts.capture_dns_evidence import run
from scripts.check_network_scope import run as check_scope


def test_exported_datagrams_match_socket_observations(tmp_path):
    assert run(tmp_path) == 10
    with (tmp_path / "dns-observations.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    with (tmp_path / "dns-upper-pdu.pcap").open("rb") as trace:
        assert struct.unpack("<IHHIIII", trace.read(24)) == (0xa1b23c4d, 2, 4, 0, 0, 65535, 252)
        previous_ns = 0
        for row in rows:
            sec, ns, captured, size = struct.unpack("<IIII", trace.read(16))
            assert previous_ns <= sec * 10**9 + ns == int(row["unix_ns"])
            previous_ns = sec * 10**9 + ns
            assert size == captured
            raw = trace.read(captured)
            index, options = 0, {}
            while True:
                tag, length = struct.unpack_from("!HH", raw, index)
                index += 4
                if tag == 0:
                    assert length == 0
                    break
                options[tag] = raw[index:index + length]
                index += length
            assert options[12] == b"dns\0" and options[24] == struct.pack("!I", 3)
            for host_tag, port_tag, key in ((20, 25, "src"), (21, 26, "dst")):
                port, = struct.unpack("!I", options[port_tag])
                assert socket.inet_ntoa(options[host_tag]) == "127.0.0.1"
                assert row[key] == f"127.0.0.1:{port}"
            payload = raw[index:]
            assert len(payload) == int(row["dns_payload_bytes"])
            assert hashlib.sha256(payload).hexdigest() == row["dns_payload_sha256"]
            message = dns.message.from_wire(payload)
            assert hex(message.id) == row["dns_id"]
            assert str(bool(message.flags & dns.flags.QR)) == row["reply"]
            assert str(message.question[0].name) == row["name"]
        assert not trace.read(1)


def test_scope_fixtures_verify_their_own_positive_dns_control(tmp_path):
    rows = {r["scenario"]: r for r in check_scope(tmp_path)}
    assert len(rows) == 7
    assert rows["filtered_udp_control"]["result"] == "NXDOMAIN"
    assert rows["synthetic_child_scope_inactive"]["filter_queries"] == 1
    for channel in ("alternate_resolver_fixture", "https_dns_fixture", "tls_dns_fixture"):
        assert rows[channel]["result"] == "192.0.2.1"
        assert rows[channel]["filter_queries"] == 0
