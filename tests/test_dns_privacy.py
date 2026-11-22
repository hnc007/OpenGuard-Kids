"""A shared PC can change active users while a loopback DNS query is in progress."""
import dns.message
import dns.rdatatype
import pytest

from agent.dns_proxy import Resolver
from agent.policy import DomainRules, SAFESEARCH_TARGET, Scope
from agent.simulate import demo_policy, example_upstream


@pytest.mark.parametrize("lost_at", ["observation", "upstream"])
def test_safesearch_resumes_normal_dns_when_visible_scope_ends(lost_at):
    visible = True
    queries = []

    def observe(_name, _decision):
        return lost_at != "observation"

    def upstream(query):
        nonlocal visible
        name = query.question[0].name.to_text(omit_final_dot=True)
        queries.append(name)
        if name == SAFESEARCH_TARGET:
            visible = False
        return example_upstream(query)

    resolver = Resolver(DomainRules(demo_policy()), lambda: Scope(True, visible, True),
                        upstream, lambda *_: None, on_allowed=observe)
    original = dns.message.make_query("www.google.com", "A")
    reply = resolver.answer(original)

    assert queries == (["www.google.com"] if lost_at == "observation" else [SAFESEARCH_TARGET, "www.google.com"])
    assert reply.id == original.id and reply.question == original.question
    assert [rr.rdtype for rr in reply.answer] == [dns.rdatatype.A]
    assert not resolver.cache
