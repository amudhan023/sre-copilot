"""
SRE Copilot — FastAPI REST API + dashboard UI.

Endpoints:
  GET  /                        — Dashboard HTML
  GET  /incidents               — List incidents (with status filter)
  GET  /incidents/{id}          — Full incident detail
  GET  /incidents/{id}/timeline — Agent event log
  GET  /incidents/{id}/postmortem — Generated postmortem
  POST /incidents/{id}/resolve  — Manually resolve an incident
  GET  /agents/health           — Poll all agent /health endpoints
  GET  /knowledge/search        — Vector search proxy
  GET  /health                  — API health check
"""
from __future__ import annotations
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, "/app")
from shared.db_client import (
    init_pool, list_incidents, get_incident,
    get_incident_timeline, get_postmortem, update_incident,
    get_service_registry,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("sre-api")

app = FastAPI(title="SRE Copilot", version="1.0.0", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

AGENT_URLS = {
    "detection-agent":           os.getenv("DETECTION_AGENT_URL",    "http://detection-agent:8200"),
    "correlation-agent":         os.getenv("CORRELATION_AGENT_URL",  "http://correlation-agent:8201"),
    "investigation-agent":       os.getenv("INVESTIGATION_AGENT_URL","http://investigation-agent:8202"),
    "knowledge-retrieval-agent": os.getenv("KR_AGENT_URL",           "http://knowledge-retrieval-agent:8203"),
    "remediation-agent":         os.getenv("REMEDIATION_AGENT_URL",  "http://remediation-agent:8204"),
    "communication-agent":       os.getenv("COMMUNICATION_AGENT_URL","http://communication-agent:8205"),
    "postmortem-agent":          os.getenv("POSTMORTEM_AGENT_URL",   "http://postmortem-agent:8206"),
}


@app.on_event("startup")
async def startup():
    try:
        init_pool()
        logger.info("Database pool initialised.")
    except Exception as exc:
        logger.warning("DB pool startup failed: %s", exc)


# ─── Dashboard ────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SRE Copilot — Incident Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.topbar{background:#1e293b;border-bottom:1px solid #334155;padding:0 24px;height:56px;display:flex;align-items:center;justify-content:space-between}
.logo{font-size:16px;font-weight:700;color:#f8fafc}
.logo span{color:#3b82f6}
.status{font-size:12px;color:#64748b}
.status .dot{width:8px;height:8px;border-radius:50%;background:#22c55e;display:inline-block;margin-right:6px}
.main{padding:24px;max-width:1200px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.card{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:20px}
.card-value{font-size:32px;font-weight:700;color:#f8fafc}
.card-label{font-size:12px;color:#64748b;margin-top:4px;text-transform:uppercase;letter-spacing:.5px}
.card.critical .card-value{color:#ef4444}
.card.active .card-value{color:#f59e0b}
.card.resolved .card-value{color:#22c55e}
.incidents-table{background:#1e293b;border:1px solid #334155;border-radius:10px;overflow:hidden}
.table-header{padding:16px 20px;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center}
.table-header h2{font-size:14px;font-weight:600;color:#f8fafc}
.refresh-btn{background:#3b82f6;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px}
table{width:100%;border-collapse:collapse}
th{font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;padding:10px 16px;text-align:left;border-bottom:1px solid #1e293b}
td{font-size:13px;padding:12px 16px;border-bottom:1px solid #1e293b;color:#cbd5e1}
tr:hover{background:rgba(255,255,255,.02)}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge.CRITICAL{background:#fee2e2;color:#dc2626}
.badge.HIGH{background:#fef3c7;color:#d97706}
.badge.MEDIUM{background:#dbeafe;color:#2563eb}
.badge.DETECTING{background:#334155;color:#94a3b8}
.badge.CORRELATING{background:#3730a3;color:#a5b4fc}
.badge.INVESTIGATING{background:#1e40af;color:#93c5fd}
.badge.RCA_COMPLETE{background:#0e7490;color:#a5f3fc}
.badge.REMEDIATING{background:#1d4ed8;color:#bfdbfe}
.badge.RESOLVED{background:#14532d;color:#86efac}
.id-cell{font-family:monospace;font-size:11px;color:#64748b}
.no-data{text-align:center;padding:48px;color:#475569}
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">SRE <span>Copilot</span></div>
  <div class="status"><span class="dot"></span>Live</div>
</div>
<div class="main">
  <div class="cards">
    <div class="card critical"><div class="card-value" id="cnt-critical">-</div><div class="card-label">Critical Active</div></div>
    <div class="card active"><div class="card-value" id="cnt-active">-</div><div class="card-label">Total Active</div></div>
    <div class="card resolved"><div class="card-value" id="cnt-resolved">-</div><div class="card-label">Resolved (24h)</div></div>
    <div class="card"><div class="card-value" id="avg-mttr">-</div><div class="card-label">Avg MTTR (min)</div></div>
  </div>
  <div class="incidents-table">
    <div class="table-header">
      <h2>Incidents</h2>
      <button class="refresh-btn" onclick="loadIncidents()">Refresh</button>
    </div>
    <table>
      <thead><tr>
        <th>ID</th><th>Severity</th><th>Service</th><th>Anomaly</th><th>Status</th><th>Detected</th><th>MTTR</th><th>Root Cause</th>
      </tr></thead>
      <tbody id="incidents-body"><tr><td colspan="8" class="no-data">Loading...</td></tr></tbody>
    </table>
  </div>
</div>
<script>
function fmt(ts){if(!ts)return'N/A';return new Date(ts/1).toISOString().replace('T',' ').slice(0,19)+' UTC';}
function loadIncidents(){
  fetch('/incidents?limit=50').then(r=>r.json()).then(data=>{
    const tbody=document.getElementById('incidents-body');
    const rows=data.incidents||[];
    if(!rows.length){tbody.innerHTML='<tr><td colspan="8" class="no-data">No incidents yet — waiting for first failure injection</td></tr>';return;}
    let crit=0,active=0,res=0,mttrs=[];
    tbody.innerHTML=rows.map(i=>{
      if(i.severity==='CRITICAL'&&i.status!=='RESOLVED')crit++;
      if(i.status!=='RESOLVED')active++;
      if(i.status==='RESOLVED'){res++;if(i.mttr_minutes)mttrs.push(i.mttr_minutes);}
      const det=i.detection_time?new Date(typeof i.detection_time==='string'?i.detection_time:i.detection_time/1).toISOString().replace('T',' ').slice(0,16)+' UTC':'N/A';
      return '<tr>'
        +'<td class="id-cell">'+(i.id||'').slice(0,8).toUpperCase()+'</td>'
        +'<td><span class="badge '+i.severity+'">'+i.severity+'</span></td>'
        +'<td>'+(i.affected_services||[]).join(', ')+'</td>'
        +'<td>'+(i.anomaly_type||'')+'</td>'
        +'<td><span class="badge '+i.status+'">'+i.status+'</span></td>'
        +'<td>'+det+'</td>'
        +'<td>'+(i.mttr_minutes?i.mttr_minutes+'m':'-')+'</td>'
        +'<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+(i.top_root_cause||'-')+'</td>'
        +'</tr>';
    }).join('');
    document.getElementById('cnt-critical').textContent=crit;
    document.getElementById('cnt-active').textContent=active;
    document.getElementById('cnt-resolved').textContent=res;
    document.getElementById('avg-mttr').textContent=mttrs.length?Math.round(mttrs.reduce((a,b)=>a+b)/mttrs.length):'-';
  }).catch(()=>{document.getElementById('incidents-body').innerHTML='<tr><td colspan="8" class="no-data">Failed to load incidents</td></tr>';});
}
loadIncidents();
setInterval(loadIncidents,15000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


# ─── Incidents ────────────────────────────────────────────────────────────────

@app.get("/incidents")
async def list_incidents_endpoint(
    status: Optional[str] = Query(None),
    limit:  int           = Query(50, ge=1, le=200),
    offset: int           = Query(0, ge=0),
):
    try:
        incidents = list_incidents(status=status, limit=limit, offset=offset)
        # Convert datetime objects to ISO strings for JSON serialisation
        for inc in incidents:
            for k in ("detection_time", "resolution_time", "created_at", "updated_at"):
                if inc.get(k) and hasattr(inc[k], "isoformat"):
                    inc[k] = inc[k].isoformat()
        return {"incidents": incidents, "count": len(incidents)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/incidents/{incident_id}")
async def get_incident_endpoint(incident_id: str):
    try:
        inc = get_incident(incident_id)
        if not inc:
            raise HTTPException(status_code=404, detail="Incident not found")
        for k in ("detection_time", "resolution_time", "created_at", "updated_at"):
            if inc.get(k) and hasattr(inc[k], "isoformat"):
                inc[k] = inc[k].isoformat()
        return inc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/incidents/{incident_id}/timeline")
async def get_timeline_endpoint(incident_id: str):
    try:
        events = get_incident_timeline(incident_id)
        for ev in events:
            if ev.get("created_at") and hasattr(ev["created_at"], "isoformat"):
                ev["created_at"] = ev["created_at"].isoformat()
        return {"events": events}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/incidents/{incident_id}/postmortem")
async def get_postmortem_endpoint(incident_id: str):
    try:
        pm = get_postmortem(incident_id)
        if not pm:
            raise HTTPException(status_code=404, detail="Postmortem not available yet")
        for k in ("generated_at",):
            if pm.get(k) and hasattr(pm[k], "isoformat"):
                pm[k] = pm[k].isoformat()
        return pm
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/incidents/{incident_id}/resolve")
async def resolve_incident_endpoint(incident_id: str):
    try:
        inc = get_incident(incident_id)
        if not inc:
            raise HTTPException(status_code=404, detail="Incident not found")
        if inc.get("status") == "RESOLVED":
            return {"message": "Already resolved"}
        from shared.models import now_ms
        update_incident(incident_id, {
            "status": "RESOLVED",
            "resolution_time": now_ms(),
        })
        return {"message": "Incident resolved", "incident_id": incident_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ─── Agents health ────────────────────────────────────────────────────────────

@app.get("/agents/health")
async def agents_health():
    results = {}
    for name, url in AGENT_URLS.items():
        try:
            r = requests.get(f"{url}/health", timeout=3)
            results[name] = r.json()
        except Exception as exc:
            results[name] = {"status": "unreachable", "error": str(exc)}
    return results


# ─── Service registry ─────────────────────────────────────────────────────────

@app.get("/services")
async def get_services():
    try:
        return {"services": get_service_registry()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "sre-api"}
