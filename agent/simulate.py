"""Run a synthetic child session. Does not sample the host, change DNS or lock Windows."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import dns.message
import dns.query
import dns.rcode
import dns.rrset

from agent.dns_proxy import Resolver, listening
from agent.policy import Application, DomainRules, Scope, app_decision
from agent.screen_time import ScreenClock


def demo_policy() -> dict:
    return {
        "issuer": "Phụ huynh mẫu", "weekday_minutes": 3, "weekend_minutes": 3,
        "schedule": ["1" * 48] * 7, "blocked_apps": [{"name": "game.exe"}],
        "allowed_apps": [], "allowed_domains": [], "blocked_domains": [],
        "blocked_categories": ["inappropriate", "social", "games"], "safe_search": True,
    }


def example_upstream(query):
    response = dns.message.make_response(query, recursion_available=True)
    question = query.question[0]
    if question.rdtype == dns.rdatatype.A:
        response.answer.append(dns.rrset.from_text(question.name, 30, "IN", "A", "192.0.2.1"))
    return response


def simulate(policy: dict, start: int) -> None:
    print("MÔ PHỎNG • Phiên của em dùng dữ liệu giả. Đồng hồ chạy nhanh để minh họa.")
    scope = Scope(consent=True, tray_visible=True, child_session=True)
    clock = ScreenClock()
    last = None
    for tick in range(181):
        reading = clock.sample(policy, monotonic=tick, wall=start + tick, scope=scope)
        if tick == 0:
            print(f"Lúc bắt đầu em còn {reading.remaining_seconds} giây.")
        for seconds in reading.warnings:
            print(f"Em còn {seconds // 60} phút. Em nhớ lưu bài nhé.")
        if reading.action != last:
            if reading.action == "lock":
                print(f"Giây {tick}: đến giờ nghỉ theo quy tắc của {policy['issuer']}. Em có thể xin thêm giờ.")
            if reading.action == "grace":
                print(f"Giây {tick}: bắt đầu phút cuối để em lưu bài.")
        last = reading.action
    application = Application("game.exe", "", "demo-child")
    print(app_decision(policy, application, "demo-child", scope).explain(application.name))
    reasons = []
    resolver = Resolver(DomainRules(policy), lambda: scope, example_upstream,
                        lambda name, decision: reasons.append(decision.explain(name)))
    with listening(resolver) as port:
        for host in ("north-school.example.org", "north-maze.example.org", "www.google.com"):
            reply = dns.query.udp(dns.message.make_query(host, "A"), "127.0.0.1", port=port, timeout=2)
            print(f"{host} → {dns.rcode.to_text(reply.rcode())}")
            for rrset in reply.answer:
                print(rrset.to_text())
            for reason in reasons:
                print(reason)
            reasons.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, help="Dùng quy tắc đã ghép và ký trong kho phát triển.")
    args = parser.parse_args()
    if args.store:
        from agent.store import AgentStore

        if not args.store.is_file():
            parser.exit(2, "Chưa có kho của thiết bị. Hãy ghép đôi và đồng bộ trước.\n")
        store = AgentStore(args.store)
        identity = store.identity()
        policy = (store.read_policy(identity["device_id"], identity["verification_key"])
                  if identity else None)
        if not policy:
            parser.exit(2, "Tạm dừng vì chưa có quy tắc đã xác minh còn hạn.\n")
    else:
        policy = demo_policy()
    simulate(policy, int(time.time()))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
