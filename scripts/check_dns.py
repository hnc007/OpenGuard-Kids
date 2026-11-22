"""Exercise the submitted fixtures on the DNS wire. No adapter changes or external upstream."""
import argparse
import csv
import json
import locale
import subprocess
from pathlib import Path

import dns.message
import dns.query
import dns.rcode
from agent.dns_proxy import Resolver, listening
from agent.policy import DomainRules, Scope
from agent.simulate import demo_policy, example_upstream


def run(directory: Path, port: int):
    directory.mkdir(parents=True, exist_ok=True)
    resolver=Resolver(DomainRules(demo_policy()),lambda:Scope(True,True,True),example_upstream,lambda *_:None)
    with open("tests/fixtures/dns_cases.csv",encoding="utf-8") as stream:
        cases=list(csv.DictReader(stream))
    with open("agent/data/domains.csv",encoding="utf-8") as stream:
        cases.extend({"domain":row["domain"],"expected":"allow","group":"education_public"}
                     for row in csv.DictReader(stream) if row["review_status"]=="publisher_identified"
                     and row["category"]=="education")
    with listening(resolver, port) as port:
        output=[]
        for case in cases:
            query=dns.message.make_query(case["domain"],"A")
            reply=dns.query.udp(query,"127.0.0.1",port=port,timeout=2)
            code=dns.rcode.to_text(reply.rcode())
            expected="NXDOMAIN" if case["expected"]=="block" else "NOERROR"
            output.append({**case,"rcode":code,"matches":code==expected})
        transcript = None
        if port == 53:
            lookup=subprocess.run(["nslookup","-timeout=2","-type=CNAME","www.google.com.","127.0.0.1"],
                                  capture_output=True,timeout=10,check=True)
            transcript=(lookup.stdout+lookup.stderr).decode(locale.getpreferredencoding(False),errors="replace")
            (directory/"nslookup-safesearch.txt").write_text(transcript.replace("\r",""),encoding="utf-8")
    with (directory/"dns-acceptance.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    blocked=[r for r in output if r["expected"]=="block"]
    edu=[r for r in output if r["group"]=="education"]
    public=[r for r in output if r["group"]=="education_public"]
    result={"blocked_correct":sum(r["matches"] for r in blocked),"block_cases":len(blocked),
            "education_false_positive":sum(not r["matches"] for r in edu),"education_cases":len(edu),
            "public_education_false_positive":sum(not r["matches"] for r in public),"public_education_cases":len(public),
            "nslookup_safesearch":"forcesafesearch.google.com" in transcript.lower() if transcript else None}
    print(json.dumps(result))
    if not all(row["matches"] for row in output) or result["nslookup_safesearch"] is False:
        raise SystemExit(1)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=Path(".local/dns-check"))
    parser.add_argument("--port",type=int,default=0,help="0 chọn cổng tạm; 53 dùng cho nslookup trong lab.")
    options = parser.parse_args()
    run(options.output, options.port)
