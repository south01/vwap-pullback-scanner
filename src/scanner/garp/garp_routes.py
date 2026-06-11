"""
GARP Plus Flask blueprint — drop-in for scanner_v2.

Registers a /garp page and JSON API on the existing Flask app without touching
any of the five existing signal sets.

Integration (in main.py, after `app = Flask(...)`):

    from src.scanner.garp.garp_routes import garp_bp
    app.register_blueprint(garp_bp)

Routes:
    GET /garp                -> dashboard page (dark theme, matches scanner_v2)
    GET /garp/api/scan       -> runs scan on watchlist, returns JSON
    GET /garp/api/scan?tickers=KGC,SSRM,AEM  -> ad-hoc universe

Caching: results cached in-memory for CACHE_TTL seconds (fundamentals are
quarterly — there is no reason to hammer the API intraday).
"""

import os
import time
import threading
from flask import Blueprint, jsonify, request, render_template_string

from .garp_scanner import MMDClient, extract_metrics, score_universe
from .sp500_tickers import SP500_TICKERS
from dataclasses import asdict

garp_bp = Blueprint("garp", __name__, url_prefix="/garp")

CACHE_TTL = 6 * 3600  # 6 hours
_cache = {"key": None, "ts": 0, "data": None}
_lock = threading.Lock()

DEFAULT_WATCHLIST = [
    # Gold peer basket
    "KGC", "SSRM", "AEM", "NEM", "GOLD", "BTG", "AU", "EGO", "IAG", "HMY", "WPM", "FNV",
    # Comms / infra
    "COMM", "ANET", "CIEN", "JNPR",
    # Holdings
    "SOFI", "POET", "IBM", "RDDT",
]

# Full S&P 500 scan runs in the background — too slow (~500 tickers x several
# API calls each) to serve synchronously within an HTTP request/proxy timeout.
_sp500_state = {"status": "idle", "progress": 0, "total": len(SP500_TICKERS), "data": None, "ts": 0, "error": None}
_sp500_lock = threading.Lock()


def _run_scan(tickers):
    client = MMDClient()
    metrics = [extract_metrics(client, t) for t in tickers]
    return [asdict(s) for s in score_universe(metrics)]


def _run_sp500_scan():
    with _sp500_lock:
        if _sp500_state["status"] == "running":
            return
        _sp500_state.update(status="running", progress=0, total=len(SP500_TICKERS), error=None)
    try:
        client = MMDClient()
        metrics = []
        for i, t in enumerate(SP500_TICKERS):
            metrics.append(extract_metrics(client, t))
            with _sp500_lock:
                _sp500_state["progress"] = i + 1
        data = [asdict(s) for s in score_universe(metrics)]
        with _sp500_lock:
            _sp500_state.update(status="done", data=data, ts=time.time())
    except Exception as e:
        with _sp500_lock:
            _sp500_state.update(status="error", error=str(e))


@garp_bp.route("/api/scan")
def api_scan():
    raw = request.args.get("tickers", "")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()] or DEFAULT_WATCHLIST
    key = ",".join(sorted(tickers))
    with _lock:
        if _cache["key"] == key and time.time() - _cache["ts"] < CACHE_TTL:
            return jsonify({"cached": True, "results": _cache["data"]})
    data = _run_scan(tickers)
    with _lock:
        _cache.update(key=key, ts=time.time(), data=data)
    return jsonify({"cached": False, "results": data})


@garp_bp.route("/api/scan/sp500")
def api_scan_sp500():
    with _sp500_lock:
        state = dict(_sp500_state)

    if state["status"] == "done" and time.time() - state["ts"] < CACHE_TTL:
        return jsonify({"status": "done", "cached": True, "results": state["data"]})

    if state["status"] != "running":
        threading.Thread(target=_run_sp500_scan, name="garp-sp500-scan", daemon=True).start()

    with _sp500_lock:
        return jsonify({
            "status": "scanning",
            "progress": _sp500_state["progress"],
            "total": _sp500_state["total"],
            "error": _sp500_state["error"],
        })


PAGE = """
<!doctype html><html><head><title>GARP Plus — Scanner v2</title>
<style>
 body{background:#0d1117;color:#c9d1d9;font-family:Menlo,Consolas,monospace;margin:24px}
 h1{color:#58a6ff;font-size:18px} .sub{color:#8b949e;font-size:12px;margin-bottom:16px}
 table{border-collapse:collapse;width:100%;font-size:13px}
 th,td{padding:6px 10px;text-align:left;border-bottom:1px solid #21262d}
 th{color:#8b949e;font-weight:normal}
 .g-A,.g-Ap,.g-Am{color:#3fb950}.g-B,.g-Bp,.g-Bm{color:#a5d6a7}
 .g-C,.g-Cp,.g-Cm{color:#d29922}.g-D,.g-Dp,.g-Dm{color:#f0883e}.g-F{color:#f85149}
 .v-STRONGBUY{color:#3fb950;font-weight:bold}.v-BUY{color:#a5d6a7}
 .v-HOLD{color:#d29922}.v-SELL{color:#f0883e}.v-STRONGSELL{color:#f85149;font-weight:bold}
 button{background:#21262d;color:#58a6ff;border:1px solid #30363d;padding:6px 14px;cursor:pointer}
 input{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;padding:6px;width:420px}
</style></head><body>
<h1>GARP PLUS — 5-Factor Sector-Relative Scan</h1>
<div class="sub">Value 20% · Growth 20% · Profitability 25% · Momentum 20% · Revisions 15% · PEG circuit breaker active</div>
<input id="tk" placeholder="Tickers (blank = default watchlist)">
<button onclick="scan()">Scan</button>
<button onclick="scanSP500()">S&amp;P 500 (full scan)</button>
<span id="st" class="sub"></span>
<table id="tbl"><thead><tr>
<th>Ticker</th><th>Verdict</th><th>Score</th><th>Val</th><th>Grw</th><th>Prof</th>
<th>Mom</th><th>Rev</th><th>PEG</th><th>PEG-G</th><th>Price</th><th>Sector</th>
</tr></thead><tbody></tbody></table>
<script>
let sp500Timer = null;
function gc(g){return 'g-'+(g||'').replace('+','p').replace('-','m');}
function renderResults(results){
 const tb=document.querySelector('#tbl tbody'); tb.innerHTML='';
 for(const s of results){
  const g=s.factor_grades||{};
  tb.insertAdjacentHTML('beforeend',`<tr>
   <td><b>${s.ticker}</b></td>
   <td class="v-${s.verdict.replace(' ','')}">${s.verdict}${s.circuit_breaker?' *CB':''}</td>
   <td>${s.composite}</td>
   <td class="${gc(g.value)}">${g.value||'--'}</td>
   <td class="${gc(g.growth)}">${g.growth||'--'}</td>
   <td class="${gc(g.profitability)}">${g.profitability||'--'}</td>
   <td class="${gc(g.momentum)}">${g.momentum||'--'}</td>
   <td class="${gc(g.revisions)}">${g.revisions||'--'}</td>
   <td>${s.peg??'--'}</td>
   <td class="${gc(s.peg_grade)}">${s.peg_grade}</td>
   <td>${s.price?('$'+s.price.toFixed(2)):'--'}</td>
   <td style="color:#8b949e">${s.sector}</td></tr>`);
 }
}
async function scan(){
 if(sp500Timer){clearInterval(sp500Timer); sp500Timer=null;}
 const st=document.getElementById('st'); st.textContent='Scanning… (cold scan can take ~1s/ticker)';
 const q=document.getElementById('tk').value.trim();
 const r=await fetch('/garp/api/scan'+(q?('?tickers='+encodeURIComponent(q)):''));
 const j=await r.json(); st.textContent=(j.cached?'cached':'fresh')+' · '+j.results.length+' names';
 renderResults(j.results);
}
async function scanSP500(){
 const st=document.getElementById('st');
 const r=await fetch('/garp/api/scan/sp500');
 const j=await r.json();
 if(j.status==='done'){
  if(sp500Timer){clearInterval(sp500Timer); sp500Timer=null;}
  st.textContent=(j.cached?'cached':'fresh')+' · '+j.results.length+' names (S&P 500)';
  renderResults(j.results);
 } else if(j.status==='error'){
  if(sp500Timer){clearInterval(sp500Timer); sp500Timer=null;}
  st.textContent='Error: '+j.error;
 } else {
  st.textContent='Scanning S&P 500… '+j.progress+'/'+j.total+' (background, ~1s/ticker — leave this open)';
  if(!sp500Timer) sp500Timer=setInterval(scanSP500, 5000);
 }
}
scan();
</script></body></html>
"""


@garp_bp.route("/")
def page():
    return render_template_string(PAGE)
