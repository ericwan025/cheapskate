"""Read-only status dashboard (Phase 4).

A tiny FastAPI service that shows what the system is doing right now:
  - jobs waiting (queue depth) and jobs completed,
  - the active worker fleet, broken down by purchase type (spot vs on-demand, or
    a single local bucket when running against redis),
  - a live cost burn-rate estimate ($/hr) implied by that fleet.

It only *reads* — it never touches the queue's jobs or the fleet — so it's safe
to leave running. GET /api/stats returns JSON; GET / serves an HTML page that
polls that endpoint and repaints every DASHBOARD_REFRESH_SECONDS.

Run:  python -m cheapskate.dashboard
"""
from __future__ import annotations

import time

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from . import config, events, fleet
from .queue import make_queue

app = FastAPI()

# The dashboard is a consumer of queue *stats* only; it never reserves jobs.
_queue = make_queue(worker_id="dashboard")


def _stats() -> dict:
    counts = fleet.fleet_counts()
    return {
        "backend": config.QUEUE_BACKEND,
        "pending": _queue.pending_depth(),
        "completed": _queue.completed_count(),
        "workers": counts,
        "worker_total": sum(counts.values()),
        "cost_per_hour": fleet.cost_per_hour(counts),
        "events": events.recent(25),
        "ts": int(time.time()),
    }


@app.get("/api/stats")
def api_stats() -> dict:
    return _stats()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE.replace("__REFRESH__", str(config.DASHBOARD_REFRESH_SECONDS))


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cheapskate</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 0;
         background: #0f1115; color: #e6e6e6; }
  header { padding: 20px 24px; border-bottom: 1px solid #222; }
  h1 { margin: 0; font-size: 20px; letter-spacing: .5px; }
  .sub { color: #8a8a8a; font-size: 13px; margin-top: 4px; }
  .grid { display: grid; gap: 16px; padding: 24px;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
  .card { background: #171a21; border: 1px solid #232733; border-radius: 12px;
          padding: 18px 20px; }
  .label { color: #8a8a8a; font-size: 12px; text-transform: uppercase;
           letter-spacing: .6px; }
  .value { font-size: 34px; font-weight: 600; margin-top: 8px; }
  .value.small { font-size: 22px; }
  .bucket { display: flex; justify-content: space-between; font-size: 15px;
            padding: 4px 0; }
  .bucket .n { font-weight: 600; }
  .spot { color: #4ade80; } .on_demand { color: #60a5fa; } .local { color: #c4b5fd; }
  .log { padding: 0 24px 8px; }
  .log h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .6px;
            color: #8a8a8a; margin: 0 0 10px; }
  .evt { display: grid; grid-template-columns: 78px 90px 1fr; gap: 12px;
         font-size: 13px; padding: 6px 10px; border-radius: 8px; align-items: baseline; }
  .evt:nth-child(odd) { background: #14171d; }
  .evt .t { color: #666; font-variant-numeric: tabular-nums; }
  .tag { font-size: 11px; font-weight: 600; text-transform: uppercase;
         letter-spacing: .4px; }
  .tag.interrupt { color: #f59e0b; } .tag.requeue { color: #38bdf8; }
  .tag.orphan { color: #f87171; }
  .evt .d { color: #cfcfcf; }
  .evt .d b { color: #e6e6e6; }
  .foot { padding: 0 24px 24px; color: #666; font-size: 12px; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
         background: #4ade80; margin-right: 6px; vertical-align: middle; }
</style>
</head>
<body>
<header>
  <h1>cheapskate <span class="dot"></span></h1>
  <div class="sub">cost-aware autoscaler &middot; backend: <span id="backend">—</span></div>
</header>
<div class="grid">
  <div class="card"><div class="label">Jobs waiting</div><div class="value" id="pending">—</div></div>
  <div class="card"><div class="label">Jobs completed</div><div class="value" id="completed">—</div></div>
  <div class="card"><div class="label">Workers active</div><div class="value" id="worker_total">—</div></div>
  <div class="card"><div class="label">Cost</div><div class="value small"><span id="cost">—</span> <span style="font-size:14px;color:#8a8a8a">/hr</span></div></div>
  <div class="card" style="grid-column: 1 / -1;">
    <div class="label">Fleet breakdown</div>
    <div id="buckets" style="margin-top:10px;">—</div>
  </div>
</div>
<div class="log">
  <h2>Interruptions &amp; retries</h2>
  <div id="events">—</div>
</div>
<div class="foot">auto-refresh every __REFRESH__s &middot; updated <span id="updated">—</span></div>
<script>
const REFRESH = __REFRESH__ * 1000;
function fmtBuckets(w) {
  const keys = Object.keys(w);
  if (!keys.length) return '<span style="color:#666">no workers running</span>';
  return keys.map(k =>
    `<div class="bucket"><span class="${k}">${k.replace('_',' ')}</span><span class="n">${w[k]}</span></div>`
  ).join('');
}
function esc(s) { return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function fmtEvents(evts) {
  if (!evts || !evts.length) return '<span style="color:#666">no interruptions yet</span>';
  return evts.map(e => {
    const t = new Date(e.ts * 1000).toLocaleTimeString();
    const job = e.job_id != null ? ` <b>job ${esc(e.job_id)}</b>` : '';
    return `<div class="evt"><span class="t">${t}</span>`
      + `<span class="tag ${esc(e.type)}">${esc(e.type)}</span>`
      + `<span class="d">${esc(e.detail)}${job} <span style="color:#666">@${esc(e.worker_id)}</span></span></div>`;
  }).join('');
}
async function tick() {
  try {
    const r = await fetch('/api/stats');
    const s = await r.json();
    document.getElementById('backend').textContent = s.backend;
    document.getElementById('pending').textContent = s.pending;
    document.getElementById('completed').textContent = s.completed;
    document.getElementById('worker_total').textContent = s.worker_total;
    document.getElementById('cost').textContent = '$' + s.cost_per_hour.toFixed(4);
    document.getElementById('buckets').innerHTML = fmtBuckets(s.workers);
    document.getElementById('events').innerHTML = fmtEvents(s.events);
    document.getElementById('updated').textContent = new Date(s.ts * 1000).toLocaleTimeString();
  } catch (e) {
    document.getElementById('updated').textContent = 'unreachable';
  }
}
tick();
setInterval(tick, REFRESH);
</script>
</body>
</html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.DASHBOARD_PORT, log_level="warning")
