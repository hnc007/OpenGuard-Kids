"""Record only DNS payloads exchanged by this test's own connected UDP sockets."""
import argparse
import csv
import hashlib
import socket
import struct
import threading
import time
from pathlib import Path

import dns.message
import dns.rcode
import dns.rdatatype

from agent.dns_proxy import Resolver, listening
from agent.policy import DomainRules, Scope
from agent.simulate import demo_policy, example_upstream


def exported_pdu(data, source, destination):
    # Wireshark Upper PDU describes the observation point without made-up IP/UDP headers.
    fields = [(12, b"dns\0"), (20, socket.inet_aton(source[0])), (21, socket.inet_aton(destination[0])),
              (24, struct.pack("!I", 3)), (25, struct.pack("!I", source[1])),
              (26, struct.pack("!I", destination[1])), (0, b"")]
    return b"".join(struct.pack("!HH", tag, len(value)) + value for tag, value in fields) + data


class Trace:
    def __init__(self):
        self.records = []
        self.lock = threading.Lock()
        self.epoch = time.time_ns()
        self.start = time.perf_counter_ns()

    def timestamp(self):
        return self.epoch + (time.perf_counter_ns() - self.start)

    def exchange(self, query, port, path):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.settimeout(2)
            peer.connect(("127.0.0.1", port))
            local, remote = peer.getsockname(), peer.getpeername()
            request = query.to_wire()
            sent = self.timestamp()
            if peer.send(request) != len(request):
                raise OSError("Incomplete DNS datagram")
            reply = peer.recv(65535)
            received = self.timestamp()
        parsed = dns.message.from_wire(reply)
        if not query.is_response(parsed):
            raise ValueError("Response did not match this test's DNS question")
        with self.lock:
            self.records.extend([(sent, path, local, remote, request), (received, path, remote, local, reply)])
        return parsed

    def write(self, output):
        output.mkdir(parents=True, exist_ok=True)
        with (output / "dns-upper-pdu.pcap").open("xb") as packets, (output / "dns-observations.csv").open(
                "x", newline="", encoding="utf-8") as table:
            # Little-endian nanosecond PCAP, link type 252 (WIRESHARK_UPPER_PDU).
            packets.write(struct.pack("<IHHIIII", 0xa1b23c4d, 2, 4, 0, 0, 65535, 252))
            writer = csv.writer(table)
            writer.writerow(("frame", "unix_ns", "path", "src", "dst", "dns_id", "reply", "name", "rcode",
                             "dns_payload_bytes", "dns_payload_sha256"))
            for i, (stamp, path, source, destination, data) in enumerate(sorted(self.records), 1):
                pdu = exported_pdu(data, source, destination)
                seconds, nanos = divmod(stamp, 1_000_000_000)
                packets.write(struct.pack("<IIII", seconds, nanos, len(pdu), len(pdu)) + pdu)
                message = dns.message.from_wire(data)
                reply = bool(message.flags & dns.flags.QR)
                writer.writerow((i, stamp, path, "%s:%s" % source, "%s:%s" % destination, hex(message.id),
                                 reply, message.question[0].name, dns.rcode.to_text(message.rcode()) if reply else "",
                                 len(data), hashlib.sha256(data).hexdigest()))


def run(output):
    trace = Trace()
    rules = DomainRules(demo_policy())
    upstream = Resolver(rules, lambda: Scope(False, False, False), example_upstream, lambda *_: None)
    with listening(upstream) as origin:
        proxy = Resolver(rules, lambda: Scope(True, True, True),
                         lambda q: trace.exchange(q, origin, "proxy-upstream"), lambda *_: None)
        with listening(proxy) as filtered:
            cases = [("north-maze.example.org", "A", filtered, "client-proxy", "NXDOMAIN"),
                     ("north-school.example.org", "A", filtered, "client-proxy", "192.0.2.1"),
                     ("www.google.com", "CNAME", filtered, "client-proxy", "forcesafesearch.google.com."),
                     ("north-maze.example.org", "A", origin, "client-upstream-control", "192.0.2.1")]
            for name, record_type, port, path, expected in cases:
                reply = trace.exchange(dns.message.make_query(name, record_type), port, path)
                if expected == "NXDOMAIN":
                    valid = reply.rcode() == dns.rcode.NXDOMAIN and not reply.answer
                else:
                    valid = reply.rcode() == dns.rcode.NOERROR and any(
                        r.to_text() == expected for rrset in reply.answer for r in rrset)
                if not valid:
                    raise AssertionError(f"Unexpected result for {name} via {path}")
    trace.write(output)
    return len(trace.records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"{run(args.output)} isolated DNS socket observations")
