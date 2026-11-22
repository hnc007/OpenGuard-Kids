"""Loopback DNS transport. The caller supplies the upstream and a visibility/consent gate."""
from __future__ import annotations

import copy
import ipaddress
import socket
import socketserver
import struct
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import contextmanager

import dns.exception
import dns.flags
import dns.message
import dns.opcode
import dns.query
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.rrset

from agent.policy import Decision, DomainRules, SAFESEARCH_TARGET, Scope

Message = dns.message.Message


def network_upstream(address: str, port: int = 53) -> Callable[[Message], Message]:
    address = str(ipaddress.ip_address(address))

    def resolve(query: Message) -> Message:
        # Minimise upstream metadata. In particular, drop any client subnet option.
        question = query.question[0]
        clean = dns.message.make_query(question.name, question.rdtype, question.rdclass)
        clean.flags = query.flags & (dns.flags.RD | dns.flags.CD)
        if query.edns >= 0:
            clean.use_edns(edns=0, ednsflags=query.ednsflags & dns.flags.DO, payload=1232)
        answer, _ = dns.query.udp_with_fallback(clean, address, port=port, timeout=1.5)
        answer.id = query.id
        return answer

    return resolve


class Resolver:
    def __init__(self, rules: DomainRules, scope: Callable[[], Scope],
                 upstream: Callable[[Message], Message],
                 notify: Callable[[str, Decision], bool | None], clock=time.monotonic,
                 on_allowed: Callable[[str, Decision], bool | None] | None = None):
        self.rules, self.scope, self.upstream = rules, scope, upstream
        self.notify, self.clock = notify, clock
        self.on_allowed = on_allowed
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def _forward_without_policy(self, query: Message) -> Message:
        with self.lock:
            self.cache.clear()
        return self.upstream(query)

    def _resolve(self, query: Message) -> Message:
        # Share only positive, plain IN answers. Do not cache flags, questions or
        # EDNS options on behalf of a different request.
        q = query.question[0]
        key = (q.name, q.rdtype, query.flags)
        cacheable = query.edns < 0 and q.rdclass == dns.rdataclass.IN
        now = self.clock()
        with self.lock:
            entry = self.cache.get(key) if cacheable else None
            if entry:
                saved, lifetime, result = entry
                if now - saved < lifetime:
                    self.cache.move_to_end(key)
                    response = copy.deepcopy(result)
                    response.id = query.id
                    response.question = list(query.question)
                    for section in (response.answer, response.authority, response.additional):
                        for rrset in section:
                            rrset.ttl = max(0, rrset.ttl - int(now - saved))
                    return response
                del self.cache[key]
        response = self.upstream(query)
        if cacheable and response.rcode() == dns.rcode.NOERROR and response.answer:
            ttl = min(rr.ttl for section in (response.answer, response.authority, response.additional)
                      for rr in section)
            if ttl:
                with self.lock:
                    self.cache[key] = (now, min(ttl, 60), copy.deepcopy(response))
                    self.cache.move_to_end(key)
                    if len(self.cache) > 256:
                        self.cache.popitem(last=False)
        return response

    def answer(self, query: Message) -> Message:
        reply = dns.message.make_response(query, recursion_available=True)
        if (query.opcode() != dns.opcode.QUERY or len(query.question) != 1 or
                query.question[0].rdclass != dns.rdataclass.IN):
            reply.set_rcode(dns.rcode.REFUSED)
            return reply
        q = query.question[0]
        if q.rdtype in (dns.rdatatype.AXFR, dns.rdatatype.IXFR):
            reply.set_rcode(dns.rcode.REFUSED)
            return reply
        try:
            scope = self.scope()
            if not scope.active:
                # No policy lookup, notification, or retention when support pauses.
                return self._forward_without_policy(query)
            try:
                decision = self.rules.decide(q.name.to_text(), scope)
            except (ValueError, UnicodeError):
                # SRV/TXT service labels and the DNS root are valid DNS names.
                # Apply parent restrictions to the hostname part of service names.
                labels = q.name.to_text(omit_final_dot=True).split(".")
                while labels and labels[0].startswith("_"):
                    labels.pop(0)
                if not labels or q.name == dns.name.root:
                    return self._resolve(query)
                try:
                    decision = self.rules.decide(".".join(labels), scope)
                except (ValueError, UnicodeError):
                    reply.set_rcode(dns.rcode.REFUSED)
                    return reply
            if decision.action == "block":
                if self.notify(q.name.to_text(omit_final_dot=True), decision) is False or not self.scope().active:
                    return self._forward_without_policy(query)
                reply.set_rcode(dns.rcode.NXDOMAIN)
                return reply
            if self.on_allowed and self.on_allowed(q.name.to_text(omit_final_dot=True), decision) is False:
                return self._forward_without_policy(query)
            if not self.scope().active:
                return self._forward_without_policy(query)
            if decision.action == "safesearch":
                reply.answer.append(dns.rrset.from_text(q.name, 60, "IN", "CNAME", SAFESEARCH_TARGET + "."))
                if q.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
                    canonical = self._resolve(dns.message.make_query(SAFESEARCH_TARGET, q.rdtype))
                    if not self.scope().active:
                        return self._forward_without_policy(query)
                    if canonical.rcode() != dns.rcode.NOERROR:
                        reply.answer.clear()
                        reply.set_rcode(dns.rcode.SERVFAIL)
                    else:
                        reply.answer.extend(canonical.answer)
                return reply
            return self._resolve(query)
        except (OSError, dns.exception.DNSException):
            reply.answer.clear()
            reply.set_rcode(dns.rcode.SERVFAIL)
            return reply

    def wire(self, packet: bytes, tcp: bool) -> bytes | None:
        try:
            query = dns.message.from_wire(packet)
            if query.flags & dns.flags.QR:
                return None
            reply = self.answer(query)
            limit = 65535 if tcp else min(max(512, query.payload), 1232)
            return reply.to_wire(max_size=limit, prefer_truncation=True)
        except (dns.exception.DNSException, ValueError):
            return None


@contextmanager
def listening(resolver: Resolver, port: int = 0):
    """Bind UDP and TCP on the same local port, closing both on any startup or exit path."""
    class UDP(socketserver.BaseRequestHandler):
        def handle(self):
            data, transport = self.request
            result = resolver.wire(data, tcp=False)
            if result:
                transport.sendto(result, self.client_address)

    class TCP(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            try:
                header = self.rfile.read(2)
                if len(header) != 2:
                    return
                size, = struct.unpack("!H", header)
                packet = self.rfile.read(size)
                if len(packet) != size:
                    return
                result = resolver.wire(packet, tcp=True)
                if result:
                    self.wfile.write(struct.pack("!H", len(result)) + result)
            except (socket.timeout, ConnectionError):
                return

    with socketserver.UDPServer(("127.0.0.1", port), UDP) as udp:
        with socketserver.TCPServer(udp.server_address, TCP) as tcp:
            threads = []
            for server in (udp, tcp):
                thread = threading.Thread(target=server.serve_forever, args=(0.1,), daemon=True)
                thread.start()
                threads.append(thread)
            try:
                yield udp.server_address[1]
            finally:
                udp.shutdown()
                tcp.shutdown()
                for thread in threads:
                    thread.join()
