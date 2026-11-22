"""Measure synthetic loopback DNS and the isolated proxy process (no adapter configuration)."""
import argparse
import csv
import json
import math
import multiprocessing as mp
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import dns.message
import dns.query
import psutil
from agent.dns_proxy import Resolver, listening, network_upstream
from agent.policy import DomainRules, Scope
from agent.simulate import demo_policy, example_upstream


def proxy(origin, ready, stop):
    resolver = Resolver(DomainRules(demo_policy()), lambda: Scope(True,True,True),
                        network_upstream("127.0.0.1", origin), lambda *_: None)
    with listening(resolver) as port:
        ready.send(port)
        stop.wait()


def latency(port, key):
    query = dns.message.make_query(f"sample-{key}.example.org", "A")
    start = time.perf_counter_ns()
    reply = dns.query.udp(query, "127.0.0.1", port=port, timeout=2)
    if not reply.answer: raise RuntimeError("Thiếu phản hồi thử nghiệm")
    return (time.perf_counter_ns()-start)/1_000_000


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=Path(".local/measurements"))
    parser.add_argument("--samples",type=int,default=30)
    args=parser.parse_args()
    if args.samples<30: parser.error("Tối thiểu 30 mẫu.")
    args.output.mkdir(parents=True,exist_ok=True)
    rows=[]
    upstream=Resolver(DomainRules(demo_policy()),lambda:Scope(False,False,False),example_upstream,lambda *_:None)
    with listening(upstream) as origin:
        reader,writer=mp.Pipe(duplex=False)
        stop=mp.Event()
        worker=mp.Process(target=proxy,args=(origin,writer,stop))
        worker.start()
        writer.close()
        try:
            if not reader.poll(12): raise TimeoutError("Proxy chưa sẵn sàng.")
            port=reader.recv()
            process=psutil.Process(worker.pid)
            latency(port,"warmup")
            for i in range(args.samples):
                row={"sample":i+1}
                row["idle_cpu_pct"]=process.cpu_percent(interval=.15)
                row["idle_rss_mib"]=process.memory_info().rss/1048576
                row["direct_ms"]=latency(origin,i)
                row["proxy_cold_ms"]=latency(port,i)
                row["proxy_warm_ms"]=latency(port,i)
                process.cpu_percent()
                start=time.perf_counter()
                count=0
                while time.perf_counter()-start<.15:
                    latency(port,i)
                    count+=1
                row.update(load_cpu_pct=process.cpu_percent(),load_rss_mib=process.memory_info().rss/1048576,queries=count)
                rows.append(row)
        finally:
            stop.set()
            worker.join(10)
            reader.close()
    with (args.output/"dns-samples.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result={"platform":platform.platform(),"python":platform.python_version(),"at":datetime.now(timezone.utc).isoformat(),
            "n":args.samples,"scope":"Separate Python DNS proxy process; synthetic A responses from loopback; sequential UDP IPv4; CPU % of one core; 150 ms windows."}
    for field in list(rows[0])[1:]:
        values=sorted(row[field] for row in rows)
        result[field]={k:round(values[math.ceil(len(values)*p)-1],3) for k,p in [("p50",.5),("p95",.95),("max",1)]}
    (args.output/"dns-summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
