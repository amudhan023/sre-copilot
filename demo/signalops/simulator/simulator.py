#!/usr/bin/env python3
"""Small reproducible payment-api signal generator for the SRE Copilot demo."""
import json, os, random, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TENANT = os.getenv("TENANT", "acme")
SERVICE = os.getenv("SERVICE", "payment-api")
OTLP = os.getenv("OTLP_HTTP", "http://localhost:4318").rstrip("/")
PORT = int(os.getenv("SIMULATOR_PORT", "8000"))
RPS = int(os.getenv("RPS", "20"))
BUCKETS = [0.005, .01, .025, .05, .075, .1, .25, .5, 1, 2.5, 3, 3.5, 4, 4.5, 5, 10]
_pool, _total, _errors, _sum = 50, 0, 0, 0.0
_counts = [0] * (len(BUCKETS) + 1)
_lock = threading.Lock()
_deploys = [{"version":"v1.41.0","tenant":TENANT,"service":SERVICE,"at":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()-3*86400)),"change":"routine dependency bump"}]

def observe(seconds):
    global _total, _sum
    i = 0
    while i < len(BUCKETS) and seconds > BUCKETS[i]: i += 1
    with _lock:
        _counts[i] += 1; _total += 1; _sum += seconds

def metrics():
    labels=f'tenant="{TENANT}",service="{SERVICE}"'
    with _lock: counts=list(_counts); total=_total; sm=_sum; errors=_errors; pool=_pool
    out=["# TYPE payment_api_request_duration_seconds histogram"]; running=0
    for le,c in zip(BUCKETS,counts):
        running+=c; out.append(f'payment_api_request_duration_seconds_bucket{{{labels},le="{le:g}"}} {running}')
    out += [f'payment_api_request_duration_seconds_bucket{{{labels},le="+Inf"}} {total}',f'payment_api_request_duration_seconds_sum{{{labels}}} {sm}',f'payment_api_request_duration_seconds_count{{{labels}}} {total}',"# TYPE payment_api_database_errors_total counter",f'payment_api_database_errors_total{{{labels}}} {errors}',"# TYPE payment_api_db_pool_size gauge",f'payment_api_db_pool_size{{{labels}}} {pool}',""]
    return "\n".join(out)

def emit_otlp(requests):
    spans=[]; logs=[]
    for app,db,timeout in requests:
        tid=f"{random.getrandbits(128):032x}"; root=f"{random.getrandbits(64):016x}"; child=f"{random.getrandbits(64):016x}"
        end=time.time_ns(); start=end-int((app+db)*1e9); db_start=start+int(app*1e9)
        attrs=lambda d:[{"key":k,"value":{"doubleValue":v} if isinstance(v,float) else {"stringValue":str(v)}} for k,v in d.items()]
        spans += [{"traceId":tid,"spanId":root,"name":"POST /payments","kind":2,"startTimeUnixNano":str(start),"endTimeUnixNano":str(end),"attributes":attrs({"http.route":"/payments","http.status_code":504 if timeout else 200,"duration_ms":round((app+db)*1000,1)}),"status":{"code":2 if timeout else 1}}, {"traceId":tid,"spanId":child,"parentSpanId":root,"name":"SELECT payments.transaction","kind":3,"startTimeUnixNano":str(db_start),"endTimeUnixNano":str(end),"attributes":attrs({"db.system":"postgresql","db.pool.max":_pool,"duration_ms":round(db*1000,1)}),"status":{"code":2 if timeout else 1}}]
        if timeout: logs.append({"timeUnixNano":str(end),"severityNumber":17,"severityText":"ERROR","body":{"stringValue":f"Database connection timeout waiting for pooled connection (pool max={_pool})"},"traceId":tid,"spanId":root})
    res={"attributes":[{"key":"service.name","value":{"stringValue":SERVICE}},{"key":"tenant","value":{"stringValue":TENANT}}]}
    for path,payload in [("/v1/logs",{"resourceLogs":[{"resource":res,"scopeLogs":[{"logRecords":logs}]}]}),("/v1/traces",{"resourceSpans":[{"resource":res,"scopeSpans":[{"spans":spans}]}]})]:
        if not payload.get("resourceLogs",payload.get("resourceSpans"))[0].get("scopeLogs",payload.get("scopeSpans"))[0].get("logRecords",payload.get("spans",[])) if False else False: continue
        try:
            req=urllib.request.Request(OTLP+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"},method="POST"); urllib.request.urlopen(req,timeout=2).read()
        except Exception: pass

def workload():
    global _errors
    while True:
        start=time.time(); reqs=[]
        for _ in range(RPS):
            app=max(.001,random.gauss(.04,.01)); broken=_pool==10; slow=broken and random.random()<.35; db=max(.001,random.gauss(3.8,.35) if slow else random.gauss(.04,.008)); timeout=slow and random.random()<.6
            reqs.append((app,db,timeout)); observe(app+db)
        with _lock: _errors += sum(x[2] for x in reqs)
        emit_otlp(reqs); time.sleep(max(0,1-(time.time()-start)))

class Handler(BaseHTTPRequestHandler):
    def send(self,body,ctype="application/json"):
        body=body.encode() if isinstance(body,str) else body; self.send_response(200); self.send_header("Content-Type",ctype); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path.startswith('/metrics'): self.send(metrics(),"text/plain; version=0.0.4")
        elif self.path.startswith('/deployments'): self.send(json.dumps({"deployments":_deploys}))
        elif self.path=='/': self.send(json.dumps({"tenant":TENANT,"service":SERVICE,"db_pool_max":_pool,"state":"incident" if _pool==10 else "healthy"}))
        else: self.send_error(404)
    def do_POST(self):
        global _pool
        if self.path=='/break': _pool=10; _deploys.append({"version":"v1.42.0","tenant":TENANT,"service":SERVICE,"at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"change":"maxPoolSize: 50 -> 10"}); self.send(json.dumps({"state":"incident","db_pool_max":10}))
        elif self.path=='/heal': _pool=50; _deploys.append({"version":"v1.42.1","tenant":TENANT,"service":SERVICE,"at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"change":"revert maxPoolSize: 10 -> 50"}); self.send(json.dumps({"state":"healthy","db_pool_max":50}))
        else: self.send_error(404)
    def log_message(self,*args): pass

if __name__=='__main__':
    threading.Thread(target=workload,daemon=True).start(); print(f"simulator: tenant={TENANT} service={SERVICE} :{PORT}",flush=True); ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
