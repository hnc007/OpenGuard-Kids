import csv
from pathlib import Path

import dns.exception
import dns.message
import dns.query
import dns.rcode
import pytest

from agent.dns_proxy import Resolver, listening, network_upstream
from agent.policy import Application, DomainRules, Scope, app_decision, catalogue, executable_hash
from agent.simulate import demo_policy, example_upstream

ACTIVE = Scope(True, True, True)
INACTIVE = Scope(True, False, True)


def test_application_hash_survives_rename_and_name_spoof_cannot_gain_allow():
    digest = "a" * 64
    rule = {"name": "notepad.exe", "sha256": digest}
    policy = demo_policy() | {"blocked_apps": [rule]}
    renamed = Application("game.exe", digest, "child")
    decision = app_decision(policy, renamed, "child", ACTIVE)
    assert decision.action == "block"
    assert all(text in decision.explain(renamed.name) for text in ("game.exe", "Phụ huynh mẫu", "xem lại"))
    allow_only = policy | {"blocked_apps": [], "allowed_apps": [rule]}
    assert app_decision(allow_only, renamed, "child", ACTIVE).action == "allow"
    assert app_decision(allow_only, Application("notepad.exe", "b"*64, "child"), "child", ACTIVE).action == "block"


def test_file_digest_matches_after_rename(tmp_path):
    original = tmp_path / "notepad.exe"
    original.write_bytes(b"fixture, not an executable")
    digest = executable_hash(original)
    renamed = original.rename(tmp_path / "game.exe")
    assert executable_hash(renamed) == digest
    policy = demo_policy() | {"blocked_apps": [{"sha256": digest, "name": "notepad.exe"}]}
    assert app_decision(policy, Application(renamed.name, executable_hash(renamed), "kid"), "kid", ACTIVE).action == "block"


@pytest.mark.parametrize("app", [Application("lsass.exe", "", "kid"), Application("game.exe", "", "adult"),
                                 Application("help.exe", "", "kid", protected=True)])
def test_protected_processes(app):
    policy = demo_policy() | {"allowed_apps": [{"name": "sample.exe"}]}
    assert app_decision(policy, app, "kid", ACTIVE).action == "allow"


def test_emergency_contact_apps_survive_explicit_block_and_allow_list():
    from agent.lab_input import PRIVATE_APPS
    from agent.policy import HELP_APPS

    assert HELP_APPS <= PRIVATE_APPS
    digest = "1" * 64
    policy = demo_policy() | {"allowed_apps": [{"name": "lesson.exe"}],
                              "blocked_apps": [{"name": "dialer.exe", "sha256": digest}]}
    for name in HELP_APPS:
        decision = app_decision(policy, Application(name, digest, "kid"), "kid", ACTIVE)
        assert decision.action == "allow" and "trợ giúp" in decision.reason


def test_synthetic_classification_expectations():
    with (Path(__file__).parent / "fixtures" / "dns_cases.csv").open() as file:
        cases = list(csv.DictReader(file))
    rules = DomainRules(demo_policy())
    assert len(catalogue()) >= 200 and len(set(catalogue().values())) == 6
    assert sum(row["expected"] == "block" for row in cases) == 50
    assert sum(row["group"] == "education" for row in cases) == 20
    for row in cases:
        assert rules.decide(row["domain"], ACTIVE).action == row["expected"], row


def test_subdomains_boundaries_priority_and_safety():
    rules = DomainRules(demo_policy() | {"blocked_domains": ["example.org", "vn"],
                                       "allowed_domains": ["allow.example.org"]})
    for name in ("PORTAL.HCMUS.EDU.VN.", "contact.tongdai111.vn", "safe.allow.example.org", "notexample.org"):
        assert rules.decide(name, ACTIVE).action == "allow"
    assert rules.decide("sub.example.org", ACTIVE).action == "block"
    assert rules.decide("sub.example.org", INACTIVE).action == "allow"


def test_cache_ttl_and_expiry():
    now, calls = [10], []
    def upstream(query):
        calls.append(query.id)
        return example_upstream(query)
    resolver = Resolver(DomainRules(demo_policy()), lambda: ACTIVE, upstream, lambda *_: None, lambda: now[0])
    def ask():
        return resolver.answer(dns.message.make_query("north-school.example.org", "A"))
    initial = ask()
    now[0] += 5
    second = ask()
    assert second.answer[0].ttl == initial.answer[0].ttl - 5 and len(calls) == 1
    now[0] += 26
    assert ask().answer and len(calls) == 2


def test_upstream_timeout_is_servfail():
    def timeout(query):
        raise dns.exception.Timeout()
    resolver = Resolver(DomainRules(demo_policy()), lambda: ACTIVE, timeout, lambda *_: None)
    assert resolver.answer(dns.message.make_query("library.example.org", "A")).rcode() == dns.rcode.SERVFAIL
    assert resolver.wire(b"abcdef", False) is None


def test_dns_cache_omits_help_and_text_and_drops_old_visibility_generation():
    def upstream(query):
        if query.question[0].name.to_text() == "late.example.org.":
            resolver.clear_cache()  # the UI paused while the upstream request was pending
        if query.question[0].rdtype == dns.rdatatype.TXT:
            reply = dns.message.make_response(query)
            reply.answer.append(dns.rrset.from_text(query.question[0].name, 30, "IN", "TXT", '"fixture"'))
            return reply
        return example_upstream(query)

    resolver = Resolver(DomainRules(demo_policy()), lambda: ACTIVE, upstream, lambda *_: None)
    for name, kind in [("contact.tongdai111.vn", "A"), ("demo.example.org", "TXT"),
                       ("late.example.org", "A")]:
        assert resolver.answer(dns.message.make_query(name, kind)).answer
        assert not resolver.cache


def test_dns_cache_excludes_help_aliases_and_expires_unused_queries():
    now = [10.]
    def upstream(query):
        if query.question[0].name.to_text() == "alias.example.org.":
            reply = dns.message.make_response(query)
            reply.answer.append(dns.rrset.from_text(query.question[0].name, 30, "IN", "CNAME", "contact.tongdai111.vn."))
            return reply
        return example_upstream(query)

    resolver = Resolver(DomainRules(demo_policy()), lambda: ACTIVE, upstream, lambda *_: None, lambda: now[0])
    assert resolver.answer(dns.message.make_query("alias.example.org", "A")).answer
    assert not resolver.cache
    assert resolver.answer(dns.message.make_query("library.example.org", "A")).answer
    assert resolver.cache
    now[0] += 61
    resolver.prune_cache()
    assert not resolver.cache


def test_loopback_dns_udp_tcp_and_scope_pause():
    scope, reasons = [ACTIVE], []
    resolver = Resolver(DomainRules(demo_policy()), lambda: scope[0], example_upstream,
                        lambda name, decision: reasons.append(decision.explain(name)))
    with listening(resolver) as port:
        def ask(name, transport=dns.query.udp):
            return transport(dns.message.make_query(name, "A"), "127.0.0.1", port=port, timeout=2)
        assert ask("north-maze.example.org").rcode() == dns.rcode.NXDOMAIN
        assert len(reasons) == 1
        assert ask("north-school.example.org", dns.query.tcp).answer[0][0].address == "192.0.2.1"
        answer = ask("www.google.com")
        assert answer.answer[0][0].target.to_text() == "forcesafesearch.google.com."
        assert answer.answer[1][0].address == "192.0.2.1"
        scope[0] = INACTIVE
        assert ask("north-maze.example.org").rcode() == dns.rcode.NOERROR
        assert not resolver.cache and len(reasons) == 1


def test_forwarding_uses_selected_lab_resolver():
    rules = DomainRules(demo_policy())
    origin = Resolver(rules, lambda: INACTIVE, example_upstream, lambda *_: None)
    with listening(origin) as port:
        proxy = Resolver(rules, lambda: ACTIVE, network_upstream("127.0.0.1", port), lambda *_: None)
        response = proxy.answer(dns.message.make_query("library.example.org", "A", use_edns=0))
        assert response.answer[0][0].address == "192.0.2.1"
