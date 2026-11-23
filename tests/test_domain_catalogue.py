"""Check the packaged catalogue and classify the school snapshot without external DNS."""
import csv
import hashlib
import re

import dns.message
import dns.rcode

from agent.dns_proxy import Resolver
from agent.policy import DATA, DomainRules, Scope, ancestors, catalogue, safety_domains
from agent.simulate import demo_policy, example_upstream


MOE = "https://data.gov.sg/datasets/d_688b934f82c1059ed0a6993d2a829089/view"
ACTIVE = Scope(True, True, True)


def rows():
    with (DATA / "domains.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_public_catalogue_excludes_fixtures_and_has_no_duplicate_hosts():
    entries = rows()
    assert len(entries) == len(catalogue()) == len({r["domain"].casefold() for r in entries})
    public = [r for r in entries if r["review_status"] in {"official_directory", "publisher_identified"}]
    fixtures = [r for r in entries if r["review_status"] == "synthetic"]
    assert len(public) >= 200 and len(fixtures) == 240 and len(entries) == len(public) + len(fixtures)
    assert all(r["domain"].endswith(".example.org") for r in fixtures)
    assert all(not r["domain"].endswith(".example.org") and r["source"].startswith("https://") for r in public)
    assert all(re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", r["domain"]) for r in entries)


def test_primary_school_projection_matches_attributed_snapshot():
    schools = [r for r in rows() if r["review_status"] == "official_directory"]
    assert all(r["source"] == MOE and r["category"] == "education" for r in schools)
    hosts = sorted(r["domain"] for r in schools)
    assert len(hosts) == len({h.removeprefix("www.") for h in hosts}) == 177
    assert all(re.fullmatch(r"(?:www\.)?[a-z0-9][a-z0-9-]*\.moe\.edu\.sg", h) for h in hosts)
    assert "www.mgs.moe.edu.sg" not in hosts and "www.scgs.moe.edu.sg" not in hosts
    digest = hashlib.sha256(("\n".join(hosts) + "\n").encode("ascii")).hexdigest()
    assert digest == "2aa3b8755536afd7a22460e20f5bf6184b86d0b8ba539c55273f807cbb884bd8"


def test_school_hosts_are_classified_individually_and_parent_can_limit_access():
    schools = [r["domain"] for r in rows() if r["source"] == MOE]
    policy = demo_policy() | {"blocked_categories": ["unknown"]}
    resolver = Resolver(DomainRules(policy), lambda: ACTIVE, example_upstream, lambda *_: None)
    for school in schools:
        assert not safety_domains().intersection(ancestors(school))
        assert resolver.answer(dns.message.make_query(school, "A")).rcode() == dns.rcode.NOERROR
    for other in ("moe.edu.sg", "unlisted-school.moe.edu.sg", "www.mgs.moe.edu.sg", "ahmadibrahimpri.moe.edu.sg"):
        assert resolver.answer(dns.message.make_query(other, "A")).rcode() == dns.rcode.NXDOMAIN
    edited = DomainRules(policy | {"blocked_domains": [schools[0]]})
    assert edited.decide(schools[0], ACTIVE).action == "block"
    assert edited.decide(schools[1], ACTIVE).action == "allow"


def test_ten_entertainment_fixtures_follow_category_choice():
    names = ("cartoon", "comics", "songs", "stories", "craft", "theatre", "puzzle", "radio", "poetry", "festival")
    allowed = DomainRules(demo_policy())
    limited = DomainRules(demo_policy() | {"blocked_categories": ["entertainment"]})
    for name in names:
        host = f"north-{name}.example.org"
        assert catalogue()[host] == "entertainment"
        assert allowed.decide(host, ACTIVE).action == "allow"
        assert limited.decide(host, ACTIVE).action == "block"
