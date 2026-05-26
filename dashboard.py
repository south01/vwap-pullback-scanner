"""
Minimal Flask monitoring dashboard — single-page, token-protected.
Access: https://<your-railway-domain>/?token=<DASHBOARD_TOKEN>
"""

import json
import os
import time
from functools import wraps

from flask import Flask, Response, jsonify, request, session, stream_with_context

import config
from shared_state import state

_TOKEN = config.DASHBOARD_TOKEN

# Injected by main.py after engine is created
_reflexivity_engine = None


def set_reflexivity_engine(engine) -> None:
    global _reflexivity_engine
    _reflexivity_engine = engine


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
    app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

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

    # ------------------------------------------------------------------
    # Reflexivity / Loop Radar routes
    # ------------------------------------------------------------------

    @app.route("/loop-radar")
    @_check_token
    def loop_radar():
        return _render_loop_radar()

    @app.route("/api/reflexivity/scores")
    @_check_token
    def reflexivity_scores():
        if _reflexivity_engine is None:
            return jsonify({"ok": False, "error": "engine not initialised"}), 503
        return jsonify({
            "ok":       True,
            "last_run": _reflexivity_engine.last_run(),
            "scores":   _reflexivity_engine.latest(),
        })

    @app.route("/api/reflexivity/ticker/<symbol>")
    @_check_token
    def reflexivity_ticker(symbol: str):
        if _reflexivity_engine is None:
            return jsonify({"ok": False, "error": "engine not initialised"}), 503
        detail = _reflexivity_engine.get_ticker_detail(symbol.upper())
        if detail is None:
            return jsonify({"ok": False, "error": "no data yet"}), 404
        return jsonify({"ok": True, **detail})

    @app.route("/api/reflexivity/stream")
    @_check_token
    def reflexivity_stream():
        """SSE endpoint — pushes updated scores every 10 s."""
        if _reflexivity_engine is None:
            return Response("data: {}\n\n", mimetype="text/event-stream")

        def _generate():
            while True:
                payload = json.dumps({
                    "last_run": _reflexivity_engine.last_run(),
                    "scores":   _reflexivity_engine.latest(),
                })
                yield f"data: {payload}\n\n"
                time.sleep(10)

        return Response(
            stream_with_context(_generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control":   "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


# ---------------------------------------------------------------------------
# HTML renderer — inline template, no external dependencies
# ---------------------------------------------------------------------------

_COND_KEYS = ["C1", "C2", "C3", "C4", "C5", "C7", "C8"]

_COND_DESC = {
    "C1": ("Established Uptrend",
           f"At least {config.MIN_BARS_ABOVE_VWAP} consecutive closed 5-min bars "
           f"with close above VWAP earlier in the session (excluding the current bar). "
           f"Confirms the stock was in a sustained uptrend before pulling back."),
    "C2": ("In VWAP Touch Zone",
           f"Current price is within {config.ATR_TOUCH_MULTIPLIER}× ATR above VWAP. "
           f"Price is approaching VWAP from above — the pullback is entering the setup zone."),
    "C3": ("Touch Count Valid",
           f"The number of VWAP touches today has not exceeded the max of {config.MAX_VWAP_TOUCHES}. "
           f"Too many touches indicate a choppy, indecisive tape."),
    "C4": ("Volume Drying Up",
           f"The prior 2 closed bars both have volume below the {config.VOLUME_AVG_PERIOD}-bar rolling average. "
           f"Low-volume pullback suggests sellers are not committing — healthy consolidation."),
    "C5": ("RSI Rising in Zone",
           f"RSI ({config.RSI_PERIOD}-period) is between {config.RSI_MIN} and {config.RSI_MAX} AND "
           f"higher than one bar ago. Momentum is pulling back but not oversold, and is starting to turn up."),
    "C7": ("SPY Green (disabled)",
           "SPY current price ≥ previous close — broad market is in a positive tone. "
           "Currently disabled; always passes."),
    "C8": ("VIX Below Threshold (disabled)",
           f"VIX is below {config.VIX_MAX} — market fear is contained. "
           f"Currently disabled; always passes."),
}

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

/* Condition explanation modal */
.cond-hdr {
  cursor: pointer; border-bottom: 1px dashed #8b949e;
}
.cond-hdr:hover { color: #f0f6fc; }
#cond-modal {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.7); z-index: 100;
  align-items: center; justify-content: center;
}
#cond-modal.open { display: flex; }
#cond-box {
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 20px 24px; min-width: 300px; max-width: 420px;
}
#cond-box h3 { color: #f0f6fc; font-size: 13px; margin-bottom: 6px; }
#cond-box p  { color: #c9d1d9; font-size: 12px; line-height: 1.6; margin-top: 8px; }
#cond-close {
  margin-top: 14px; float: right; background: #21262d; border: none;
  color: #c9d1d9; padding: 4px 14px; border-radius: 4px; cursor: pointer;
  font-family: inherit; font-size: 12px;
}
#cond-close:hover { background: #30363d; }
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
            touch_cell = (
                f'<span class="touch-btn" '
                f'data-ticker="{ticker}" '
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
    import json
    s = state.snapshot()
    badge = _badge(s)
    spy   = _spy_fmt(s["spy_chg"])
    vix   = _vix_fmt(s["vix"])
    cond_json       = json.dumps({k: list(v) for k, v in _COND_DESC.items()})
    touch_data_json = json.dumps(s.get("touch_history", {}))

    ticker_rows = _ticker_rows(s)
    alert_rows  = _alert_rows(s["alerts_today"])

    cond_headers = "".join(
        f'<th><span class="cond-hdr" onclick="showCond(\'{k}\')" title="Click for details">{k}</span></th>'
        for k in _COND_KEYS
    )

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
    <span style="margin-left:16px"><a href="/loop-radar" style="color:#d2a8ff;font-weight:bold;font-size:12px">🔄 Loop Radar</a></span>
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

<!-- Condition explanation modal -->
<div id="cond-modal" onclick="if(event.target===this)closeCond()">
  <div id="cond-box">
    <h3 id="cond-title"></h3>
    <p id="cond-desc"></p>
    <button id="cond-close" onclick="closeCond()">Close</button>
  </div>
</div>

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
var COND_INFO   = {cond_json};
var TOUCH_DATA  = {touch_data_json};

function showCond(key) {{
  var info = COND_INFO[key];
  if (!info) return;
  document.getElementById('cond-title').textContent = key + ' — ' + info[0];
  document.getElementById('cond-desc').textContent  = info[1];
  document.getElementById('cond-modal').classList.add('open');
}}
function closeCond() {{
  document.getElementById('cond-modal').classList.remove('open');
}}

function showTouches(el) {{
  var ticker  = el.dataset.ticker;
  var touches = TOUCH_DATA[ticker] || [];
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
  if (e.key === 'Escape') {{ closeTouches(); closeCond(); }}
}});
</script>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Loop Radar renderer
# ---------------------------------------------------------------------------

_LOOP_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0d1117; color: #c9d1d9;
  font-family: 'Courier New', monospace; font-size: 13px;
}
a { color: #58a6ff; text-decoration: none; }
header {
  background: #161b22; border-bottom: 1px solid #30363d;
  padding: 14px 24px; display: flex; align-items: center; gap: 16px;
}
header h1 { font-size: 16px; color: #f0f6fc; letter-spacing: 1px; }
.meta { color: #8b949e; font-size: 12px; margin-left: auto; }
section { padding: 20px 24px; }
h2 { color: #8b949e; font-size: 11px; letter-spacing: 1px;
     text-transform: uppercase; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left; padding: 6px 10px;
  color: #8b949e; font-size: 11px; letter-spacing: 1px;
  border-bottom: 1px solid #21262d; white-space: nowrap;
}
td { padding: 7px 10px; border-bottom: 1px solid #161b22; white-space: nowrap; }
tr:hover td { background: #161b22; }
.ticker { color: #f0f6fc; font-weight: bold; }
.dim    { color: #8b949e; }
.score-bar {
  display: inline-block; height: 8px; border-radius: 4px;
  vertical-align: middle; margin-right: 6px;
}
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: bold; white-space: nowrap;
}
.cls-LOOP_ACTIVE  { background: #1a4a2e; color: #56d364; border: 1px solid #2ea043; }
.cls-LOOP_FORMING { background: #4a3800; color: #e3b341; border: 1px solid #9e6a03; }
.cls-LOOP_COOLING { background: #2d1f00; color: #d29922; border: 1px solid #6e4800; }
.cls-NO_LOOP      { background: #21262d; color: #8b949e; border: 1px solid #30363d; }
.exit-flag { color: #f85149; font-weight: bold; }
.note { color: #c9d1d9; font-size: 11px; white-space: normal; max-width: 360px; }
.refresh { font-size: 11px; color: #6e7681; text-align: right; padding: 8px 24px; }
#live-badge {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: #21262d; color: #8b949e; border: 1px solid #30363d;
}
#live-badge.connected { background: #1a4a2e; color: #56d364; border-color: #2ea043; }
"""

_CLS_LABELS = {
    "LOOP_ACTIVE":  "LOOP ACTIVE",
    "LOOP_FORMING": "FORMING",
    "LOOP_COOLING": "COOLING",
    "NO_LOOP":      "NO LOOP",
}


def _score_bar(val: float, color: str) -> str:
    w = max(2, int(val))
    return (
        f'<span class="score-bar" '
        f'style="width:{w}px;background:{color}" title="{val:.0f}"></span>'
        f'<span style="color:{color}">{val:.0f}</span>'
    )


def _radar_rows(scores: list[dict]) -> str:
    if not scores:
        return '<tr><td colspan="9" class="dim" style="padding:12px">No data yet — engine not run or market closed</td></tr>'
    rows = []
    for r in scores:
        cls      = r.get("classification", "NO_LOOP")
        cls_html = f'<span class="badge cls-{cls}">{_CLS_LABELS.get(cls, cls)}</span>'
        exit_html = '<span class="exit-flag">EXIT</span>' if r.get("exit_signal") else '<span class="dim">—</span>'
        comp   = r.get("composite_score", 0)
        # Bar colour by score level
        if comp >= 72:   bar_col = "#56d364"
        elif comp >= 55: bar_col = "#e3b341"
        elif comp >= 38: bar_col = "#d29922"
        else:            bar_col = "#484f58"

        rows.append(f"""
        <tr>
          <td class="ticker"><a href="/api/reflexivity/ticker/{r['symbol']}?token={_TOKEN}" style="color:#f0f6fc">{r['symbol']}</a></td>
          <td>{_score_bar(comp, bar_col)}</td>
          <td>{_score_bar(r.get('momentum_score', 0),  '#79c0ff')}</td>
          <td>{_score_bar(r.get('volume_score', 0),    '#d2a8ff')}</td>
          <td>{_score_bar(r.get('sentiment_score', 0), '#ffa657')}</td>
          <td>{_score_bar(r.get('catalyst_score', 0),  '#ff7b72')}</td>
          <td>{cls_html}</td>
          <td>{exit_html}</td>
          <td class="note">{r.get('strategy_note', '—')}</td>
        </tr>""")
    return "\n".join(rows)


def _render_loop_radar() -> str:
    scores   = _reflexivity_engine.latest() if _reflexivity_engine else []
    last_run = _reflexivity_engine.last_run() if _reflexivity_engine else "—"
    rows_html = _radar_rows(scores)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Loop Radar</title>
  <style>{_LOOP_CSS}</style>
</head>
<body>

<header>
  <h1>🔄 Loop Radar</h1>
  <span class="dim" style="font-size:12px">
    Last scan: <b style="color:#c9d1d9" id="last-run">{last_run}</b>
    &nbsp;|&nbsp; {len(scores)} tickers
  </span>
  <span class="meta">
    <span id="live-badge">SSE OFF</span>
    &nbsp;|&nbsp;
    <a href="/">VWAP Scanner</a>
  </span>
</header>

<section>
  <h2>Reflexivity Scores</h2>
  <table>
    <thead>
      <tr>
        <th>Ticker</th>
        <th>Composite</th>
        <th title="Price momentum">M</th>
        <th title="Volume acceleration">V</th>
        <th title="Sentiment">S</th>
        <th title="News catalyst">C</th>
        <th>Classification</th>
        <th>Exit?</th>
        <th>Strategy Note</th>
      </tr>
    </thead>
    <tbody id="scores-body">
      {rows_html}
    </tbody>
  </table>
</section>

<p class="refresh" id="footer-ts">
  Auto-refreshes via SSE &nbsp;|&nbsp;
  <a href="/api/reflexivity/scores">Raw JSON</a>
</p>

<script>
var TOKEN = (new URLSearchParams(window.location.search)).get('token') || '';

function barHtml(val, color) {{
  var w = Math.max(2, Math.round(val));
  return '<span class="score-bar" style="width:' + w + 'px;background:' + color + '" title="' + val.toFixed(0) + '"></span>'
       + '<span style="color:' + color + '">' + val.toFixed(0) + '</span>';
}}

var CLS_LABEL = {{
  'LOOP_ACTIVE':  'LOOP ACTIVE',
  'LOOP_FORMING': 'FORMING',
  'LOOP_COOLING': 'COOLING',
  'NO_LOOP':      'NO LOOP'
}};

function renderRows(scores) {{
  if (!scores || scores.length === 0) {{
    document.getElementById('scores-body').innerHTML =
      '<tr><td colspan="9" class="dim" style="padding:12px">No data yet</td></tr>';
    return;
  }}
  var html = '';
  scores.forEach(function(r) {{
    var comp = r.composite_score || 0;
    var bc   = comp >= 72 ? '#56d364' : comp >= 55 ? '#e3b341' : comp >= 38 ? '#d29922' : '#484f58';
    var cls  = r.classification || 'NO_LOOP';
    var clsLabel = CLS_LABEL[cls] || cls;
    var exitHtml = r.exit_signal
      ? '<span class="exit-flag">EXIT</span>'
      : '<span class="dim">—</span>';
    html += '<tr>'
      + '<td class="ticker"><a href="/api/reflexivity/ticker/' + r.symbol + '?token=' + TOKEN + '" style="color:#f0f6fc">' + r.symbol + '</a></td>'
      + '<td>' + barHtml(comp, bc) + '</td>'
      + '<td>' + barHtml(r.momentum_score  || 0, '#79c0ff') + '</td>'
      + '<td>' + barHtml(r.volume_score    || 0, '#d2a8ff') + '</td>'
      + '<td>' + barHtml(r.sentiment_score || 0, '#ffa657') + '</td>'
      + '<td>' + barHtml(r.catalyst_score  || 0, '#ff7b72') + '</td>'
      + '<td><span class="badge cls-' + cls + '">' + clsLabel + '</span></td>'
      + '<td>' + exitHtml + '</td>'
      + '<td class="note">' + (r.strategy_note || '—') + '</td>'
      + '</tr>';
  }});
  document.getElementById('scores-body').innerHTML = html;
}}

// SSE live updates
function connectSSE() {{
  var badge = document.getElementById('live-badge');
  var url   = '/api/reflexivity/stream?token=' + TOKEN;
  var es    = new EventSource(url);

  es.onopen = function() {{
    badge.textContent = 'LIVE';
    badge.classList.add('connected');
  }};

  es.onmessage = function(e) {{
    try {{
      var data = JSON.parse(e.data);
      if (data.scores) renderRows(data.scores);
      if (data.last_run) document.getElementById('last-run').textContent = data.last_run;
    }} catch(err) {{}}
  }};

  es.onerror = function() {{
    badge.textContent = 'RECONNECTING';
    badge.classList.remove('connected');
    es.close();
    setTimeout(connectSSE, 5000);
  }};
}}

connectSSE();
</script>

</body>
</html>"""
