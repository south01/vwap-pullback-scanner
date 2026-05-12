"""
Minimal Flask monitoring dashboard — single-page, token-protected.
Access: https://<your-railway-domain>/?token=<DASHBOARD_TOKEN>
"""

import os
from functools import wraps

from flask import Flask, Response, jsonify, request, session

import config
from shared_state import state

_TOKEN = config.DASHBOARD_TOKEN


def _check_token(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        # Accept token via query-param on first load, then persist in session
        if request.args.get("token") == _TOKEN:
            session["auth"] = True
        if not session.get("auth"):
            return Response("Unauthorized — append ?token=<DASHBOARD_TOKEN> to the URL", 401)
        return f(*args, **kwargs)
    return wrapper


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = _TOKEN  # session signing key

    @app.route("/")
    @_check_token
    def index():
        return _render_dashboard()

    @app.route("/api/state")
    @_check_token
    def api_state():
        return jsonify(state.snapshot())

    @app.route("/api/health")
    def health():
        return jsonify({"ok": True})

    return app


# ---------------------------------------------------------------------------
# HTML renderer — inline template, no external dependencies
# ---------------------------------------------------------------------------

_COND_KEYS = ["C1", "C2", "C3", "C4", "C5", "C7", "C8"]

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0d1117; color: #c9d1d9;
  font-family: 'Courier New', monospace; font-size: 13px;
}
a { color: #58a6ff; text-decoration: none; }
header {
  background: #161b22; border-bottom: 1px solid #30363d;
  padding: 14px 24px; display: flex; align-items: center; gap: 24px;
}
header h1 { font-size: 16px; color: #f0f6fc; letter-spacing: 1px; }
.badge {
  padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: bold;
}
.badge-green  { background: #1a4a2e; color: #56d364; border: 1px solid #2ea043; }
.badge-yellow { background: #4a3800; color: #e3b341; border: 1px solid #9e6a03; }
.badge-gray   { background: #21262d; color: #8b949e; border: 1px solid #30363d; }
.meta { color: #8b949e; font-size: 12px; margin-left: auto; }
.meta span { margin-left: 16px; }
section { padding: 20px 24px; }
section + section { border-top: 1px solid #21262d; }
h2 { color: #8b949e; font-size: 11px; letter-spacing: 1px;
     text-transform: uppercase; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left; padding: 6px 10px;
  color: #8b949e; font-size: 11px; letter-spacing: 1px;
  border-bottom: 1px solid #21262d; white-space: nowrap;
}
td {
  padding: 7px 10px; border-bottom: 1px solid #161b22;
  white-space: nowrap;
}
tr:hover td { background: #161b22; }
.pass { color: #56d364; }
.fail { color: #f85149; }
.na   { color: #30363d; }
.t1   { color: #e3b341; font-weight: bold; }
.t2   { color: #56d364; font-weight: bold; }
.ticker { color: #f0f6fc; font-weight: bold; font-size: 13px; }
.price  { color: #79c0ff; }
.dim    { color: #8b949e; }
.alert-row td { border-bottom: 1px solid #1c2128; }
.refresh { font-size: 11px; color: #6e7681; text-align: right;
           padding: 8px 24px; }
.touch-btn {
  color: #79c0ff; cursor: pointer; border-bottom: 1px dashed #79c0ff;
  font-weight: bold;
}
.touch-btn:hover { color: #fff; }

/* Modal */
#touch-modal {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.7); z-index: 100;
  align-items: center; justify-content: center;
}
#touch-modal.open { display: flex; }
#touch-box {
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 20px 24px; min-width: 280px; max-width: 360px;
}
#touch-box h3 { color: #f0f6fc; font-size: 13px; margin-bottom: 12px; }
#touch-box table { width: 100%; }
#touch-box th { color: #8b949e; font-size: 11px; padding: 4px 8px; text-align: left; }
#touch-box td { padding: 5px 8px; border-bottom: 1px solid #21262d; }
#touch-close {
  margin-top: 14px; float: right; background: #21262d; border: none;
  color: #c9d1d9; padding: 4px 14px; border-radius: 4px; cursor: pointer;
  font-family: inherit; font-size: 12px;
}
#touch-close:hover { background: #30363d; }
"""


def _badge(s: dict) -> str:
    if s["is_paused"]:
        return '<span class="badge badge-yellow">PAUSED</span>'
    if s["is_market_open"]:
        return '<span class="badge badge-green">SCANNING</span>'
    return '<span class="badge badge-gray">MARKET CLOSED</span>'


def _cond(val) -> str:
    if val is True:
        return '<span class="pass">✓</span>'
    if val is False:
        return '<span class="fail">✗</span>'
    return '<span class="na">—</span>'


def _spy_fmt(chg: float) -> str:
    cls = "pass" if chg >= 0 else "fail"
    sign = "+" if chg >= 0 else ""
    return f'<span class="{cls}">SPY {sign}{chg:.2f}%</span>'


def _vix_fmt(vix: float) -> str:
    cls = "pass" if vix < config.VIX_MAX else "fail"
    return f'<span class="{cls}">VIX {vix:.1f}</span>'


def _touch_popup_data(ticker: str, history: list[dict]) -> str:
    """Encode touch history as a JSON-safe data attribute string."""
    import json
    return json.dumps(history)


def _ticker_rows(s: dict) -> str:
    rows = []
    for ticker in s["tickers"]:
        snap    = s["ticker_status"].get(ticker, {})
        conds   = snap.get("conditions", {})
        price   = snap.get("price", 0.0)
        vwap    = snap.get("vwap", 0.0)
        delta   = price - vwap if price and vwap else 0.0
        delta_cls  = "pass" if delta >= 0 else "fail"
        delta_sign = "+" if delta >= 0 else ""

        t1_cell = '<span class="t1">T1</span>' if snap.get("tier1_fired") else '<span class="dim">—</span>'
        t2_cell = '<span class="t2">T2</span>' if snap.get("tier2_fired") else '<span class="dim">—</span>'

        cond_cells = "".join(
            f"<td>{_cond(conds.get(k))}</td>" for k in _COND_KEYS
        )

        touch_count   = snap.get("touch_count", 0)
        touch_history = s.get("touch_history", {}).get(ticker, [])

        if touch_count > 0 and touch_history:
            import json
            data = json.dumps(touch_history).replace("'", "&#39;").replace('"', "&quot;")
            touch_cell = (
                f'<span class="touch-btn" '
                f'data-ticker="{ticker}" data-touches="{data}" '
                f'onclick="showTouches(this)" '
                f'title="Click to see touch history">'
                f'{touch_count}</span>'
            )
        else:
            touch_cell = f'<span class="dim">{touch_count}</span>'

        rows.append(f"""
        <tr>
          <td class="ticker">{ticker}</td>
          <td class="price">${price:.2f}</td>
          <td>${vwap:.2f}</td>
          <td class="{delta_cls}">{delta_sign}{delta:.2f}</td>
          <td>{snap.get('rsi', 0.0):.1f}</td>
          <td>${snap.get('atr', 0.0):.2f}</td>
          <td>{touch_cell}</td>
          {cond_cells}
          <td>{t1_cell}</td>
          <td>{t2_cell}</td>
          <td class="dim">{snap.get('last_updated', '—')}</td>
        </tr>""")
    return "\n".join(rows)


def _alert_rows(alerts: list) -> str:
    if not alerts:
        return '<tr><td colspan="5" class="dim" style="padding:12px">No alerts today</td></tr>'
    rows = []
    for a in reversed(alerts[-50:]):   # newest first, cap at 50
        tier_cell = f'<span class="t1">Tier 1</span>' if a["tier"] == 1 \
                    else f'<span class="t2">Tier 2</span>'
        rows.append(f"""
        <tr class="alert-row">
          <td class="dim">{a['time']}</td>
          <td class="ticker">{a['ticker']}</td>
          <td>{tier_cell}</td>
          <td class="price">${a['price']:.2f}</td>
          <td class="dim">{a.get('details','')}</td>
        </tr>""")
    return "\n".join(rows)


def _render_dashboard() -> str:
    s = state.snapshot()
    badge = _badge(s)
    spy   = _spy_fmt(s["spy_chg"])
    vix   = _vix_fmt(s["vix"])

    ticker_rows = _ticker_rows(s)
    alert_rows  = _alert_rows(s["alerts_today"])

    cond_headers = "".join(f"<th>{k}</th>" for k in _COND_KEYS)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>VWAP Scanner</title>
  <style>{_CSS}</style>
</head>
<body>

<header>
  <h1>⚡ VWAP Scanner</h1>
  {badge}
  <span class="dim" style="font-size:12px">
    Last scan: <b style="color:#c9d1d9">{s['last_scan_time']}</b>
    &nbsp;|&nbsp; Cycles: {s['scan_count']}
    &nbsp;|&nbsp; Up since: {s['started_at']}
  </span>
  <span class="meta">
    {spy}
    <span>{vix}</span>
    <span>T1: <b class="t1">{s['tier1_count']}</b></span>
    <span>T2: <b class="t2">{s['tier2_count']}</b></span>
  </span>
</header>

<section>
  <h2>Active Tickers ({len(s['tickers'])})</h2>
  <table>
    <thead>
      <tr>
        <th>Ticker</th><th>Price</th><th>VWAP</th><th>Δ VWAP</th>
        <th>RSI</th><th>ATR</th><th>Touch#</th>
        {cond_headers}
        <th>T1</th><th>T2</th><th>Updated</th>
      </tr>
    </thead>
    <tbody>
      {ticker_rows}
    </tbody>
  </table>
</section>

<section>
  <h2>Today's Alerts</h2>
  <table>
    <thead>
      <tr><th>Time</th><th>Ticker</th><th>Tier</th><th>Price</th><th>Detail</th></tr>
    </thead>
    <tbody>
      {alert_rows}
    </tbody>
  </table>
</section>

<p class="refresh">Auto-refreshes every 30 s &nbsp;|&nbsp;
  <a href="/api/state">Raw JSON</a></p>

<!-- Touch history modal -->
<div id="touch-modal" onclick="if(event.target===this)closeTouches()">
  <div id="touch-box">
    <h3 id="touch-title">Touch History</h3>
    <table>
      <thead><tr><th>#</th><th>Time (ET)</th><th>Price</th></tr></thead>
      <tbody id="touch-body"></tbody>
    </table>
    <button id="touch-close" onclick="closeTouches()">Close</button>
  </div>
</div>

<script>
function showTouches(el) {{
  var ticker  = el.dataset.ticker;
  var touches = JSON.parse(el.dataset.touches.replace(/&quot;/g, '"').replace(/&#39;/g, "'"));
  document.getElementById('touch-title').textContent = ticker + ' — Touch History';
  var body = document.getElementById('touch-body');
  body.innerHTML = '';
  touches.forEach(function(t) {{
    var row = '<tr>'
      + '<td style="color:#8b949e">#' + t.num + '</td>'
      + '<td>' + t.time + '</td>'
      + '<td style="color:#79c0ff">$' + t.price.toFixed(2) + '</td>'
      + '</tr>';
    body.innerHTML += row;
  }});
  document.getElementById('touch-modal').classList.add('open');
}}
function closeTouches() {{
  document.getElementById('touch-modal').classList.remove('open');
}}
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closeTouches();
}});
</script>

</body>
</html>"""
