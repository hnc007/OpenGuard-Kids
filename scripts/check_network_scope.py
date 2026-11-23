"""Observe the DNS filter's scope using fixed, loopback-only synthetic test fixtures."""
import argparse
import csv
import http.client
import socketserver
import ssl
import struct
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import dns.message
import dns.query
import dns.rcode

from agent.dns_proxy import Resolver, listening
from agent.policy import DomainRules, Scope
from agent.simulate import demo_policy, example_upstream
from scripts.lab_certificate import certificates


NAME = "north-maze.example.org."
LESSON = b"local-synthetic-lesson"


def fixed_answer(packet):
    message = dns.message.from_wire(packet)
    if len(message.question) != 1 or message.question[0] != dns.message.make_query(NAME, "A").question[0]:
        raise ValueError("This fixture only answers its one declared A question")
    return example_upstream(message).to_wire()


class LocalWeb(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, content, kind):
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", 0))
        if self.path != "/dns-query" or self.headers.get("Content-Type") != "application/dns-message" or not 0 < size < 512:
            return self.send_error(400)
        self.reply(fixed_answer(self.rfile.read(size)), "application/dns-message")

    def do_GET(self):
        if self.path != "/synthetic-lesson":
            return self.send_error(404)
        self.reply(LESSON, "text/plain")


class LocalDot(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        size, = struct.unpack("!H", self.rfile.read(2))
        if 0 < size < 512:
            wire = fixed_answer(self.rfile.read(size))
            self.wfile.write(struct.pack("!H", len(wire)) + wire)


@contextmanager
def fixture_server(server_type, handler, tls=None):
    with server_type(("127.0.0.1", 0), handler) as server:
        if tls:
            server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(2)


def fetch(port, ca, query=None):
    client = http.client.HTTPSConnection("127.0.0.1", port, context=ca, timeout=3)
    try:
        if query:
            client.request("POST", "/dns-query", query.to_wire(), {"Content-Type": "application/dns-message"})
        else:
            client.request("GET", "/synthetic-lesson")
        response = client.getresponse()
        if response.status != 200:
            raise AssertionError(response.status)
        return response.read()
    finally:
        client.close()


def run(folder):
    certificates(folder)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(folder / "server.pem", folder / "server-key.pem")
    ca = ssl.create_default_context(cafile=str(folder / "ca.pem"))
    rules = DomainRules(demo_policy())
    active = True
    received = []

    class CountedResolver(Resolver):
        def wire(self, packet, tcp):
            received.append(1)
            return super().wire(packet, tcp)

    filt = CountedResolver(rules, lambda: Scope(True, True, active), example_upstream, lambda *_: None)
    neutral = Resolver(rules, lambda: Scope(False, False, False), example_upstream, lambda *_: None)
    query = dns.message.make_query(NAME, "A")
    def udp(port):
        return dns.query.udp(query, "127.0.0.1", port=port, timeout=2)
    def describe(answer):
        if not query.is_response(answer):
            raise AssertionError("Mismatched DNS response")
        return dns.rcode.to_text(answer.rcode()) if not answer.answer else answer.answer[0][0].to_text()
    rows = []
    def probe(name, attempt, value, expected_seen):
        initial = len(received)
        result = attempt()
        delta = len(received) - initial
        if result != value or delta != expected_seen:
            raise AssertionError((name, result, delta))
        rows.append(dict(scenario=name, result=result, filter_queries=delta))

    with listening(filt) as port, listening(neutral) as alternate, fixture_server(HTTPServer, LocalWeb, tls) as web:
        probe("filtered_udp_control", lambda: describe(udp(port)), "NXDOMAIN", 1)
        probe("alternate_resolver_fixture", lambda: describe(udp(alternate)), "192.0.2.1", 0)
        probe("https_dns_fixture", lambda: describe(dns.message.from_wire(fetch(web, ca, query))), "192.0.2.1", 0)
        with fixture_server(socketserver.TCPServer, LocalDot, tls) as dot:
            probe("tls_dns_fixture", lambda: describe(dns.query.tls(query, "127.0.0.1", port=dot,
                  ssl_context=ca, server_hostname="localhost", timeout=2)), "192.0.2.1", 0)
        probe("https_numeric_address_fixture", lambda: fetch(web, ca).decode(), LESSON.decode(), 0)

        # Test one immutable, in-process gateway path. No arbitrary target, CONNECT or public proxy.
        class FixedGateway(LocalWeb):
            def do_GET(self):
                if self.path != "/synthetic-lesson":
                    return self.send_error(404)
                self.reply(fetch(web, ca), "text/plain")

        with fixture_server(HTTPServer, FixedGateway, tls) as gateway:
            probe("https_fixed_reverse_proxy_fixture", lambda: fetch(gateway, ca).decode(), LESSON.decode(), 0)
        active = False
        probe("synthetic_child_scope_inactive", lambda: describe(udp(port)), "192.0.2.1", 1)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="openguard-scope-") as folder:
        rows = run(Path(folder))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} isolated scope checks verified")
