"""
GARP Plus Quant Scanner — 5-Factor Sector-Relative Scoring Engine
==================================================================
Replicates the Seeking Alpha GARP Plus methodology described by Steven Cress:
  - 5 factors: Value, Growth, Profitability, Momentum, EPS Revisions
  - All metrics scored RELATIVE to sector peers (percentile rank -> letter grade)
  - PEG ratio as the bridge between value and growth
  - "Circuit breaker": a stock cannot be Strong Buy if its Value grade is D- or F,
    UNLESS its PEG grade is B+ or better (growth justifies the multiple)
  - Composite weighted score -> Strong Buy / Buy / Hold / Sell / Strong Sell

Data provider: Massive Market Data (Polygon.io API-compatible).
Set MMD_API_KEY env var or pass api_key to GarpScanner().

Designed to drop into Scanner v2.0 as an additional signal set, or run standalone:
    python garp_scanner.py --tickers KGC,COMM,SSRM,SOFI,POET
    python garp_scanner.py --tickers-file watchlist.txt --json out.json

Default factor weights (configurable in CONFIG):
    Value 20% | Growth 20% | Profitability 25% | Momentum 20% | Revisions 15%
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

CONFIG = {
    "base_url": "https://api.polygon.io",
    "weights": {
        "value": 0.20,
        "growth": 0.20,
        "profitability": 0.25,
        "momentum": 0.20,
        "revisions": 0.15,
    },
    # Verdict thresholds on composite percentile score (0-100)
    "verdicts": [
        (80, "STRONG BUY"),
        (60, "BUY"),
        (40, "HOLD"),
        (20, "SELL"),
        (0, "STRONG SELL"),
    ],
    # Circuit breaker: cap at HOLD if value grade <= this AND PEG grade < B+
    "circuit_breaker_value_grades": {"D-", "F"},
    "circuit_breaker_peg_override": {"A+", "A", "A-", "B+"},
    "request_pause": 0.15,  # polite pacing between API calls
}

GRADE_BANDS = [
    (97, "A+"), (90, "A"), (83, "A-"),
    (76, "B+"), (69, "B"), (62, "B-"),
    (55, "C+"), (48, "C"), (41, "C-"),
    (34, "D+"), (27, "D"), (20, "D-"),
    (0, "F"),
]


def pct_to_grade(p: Optional[float]) -> str:
    if p is None or math.isnan(p):
        return "N/A"
    for cutoff, grade in GRADE_BANDS:
        if p >= cutoff:
            return grade
    return "F"


# ----------------------------------------------------------------------------
# Data layer (Massive Market Data / Polygon-compatible)
# ----------------------------------------------------------------------------

class MMDClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("MASSIVE_API_KEY") or os.environ.get("MMD_API_KEY") or os.environ.get("POLYGON_API_KEY")
        if not self.api_key:
            sys.exit("Set MASSIVE_API_KEY environment variable.")
        self.s = requests.Session()

    def _get(self, path: str, **params) -> dict:
        params["apiKey"] = self.api_key
        r = self.s.get(f"{CONFIG['base_url']}{path}", params=params, timeout=30)
        time.sleep(CONFIG["request_pause"])
        if r.status_code == 429:
            time.sleep(12)
            r = self.s.get(f"{CONFIG['base_url']}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def ticker_details(self, ticker: str) -> dict:
        return self._get(f"/v3/reference/tickers/{ticker}").get("results", {})

    def financials(self, ticker: str, limit: int = 8) -> list[dict]:
        """Quarterly financials, newest first."""
        j = self._get(
            "/vX/reference/financials",
            ticker=ticker, timeframe="quarterly", limit=limit, sort="period_of_report_date",
            order="desc",
        )
        return j.get("results", [])

    def daily_closes(self, ticker: str, days: int = 400) -> list[float]:
        end = datetime.utcnow().date()
        start = end - timedelta(days=int(days * 1.6))
        j = self._get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
            adjusted="true", sort="asc", limit=50000,
        )
        return [bar["c"] for bar in j.get("results", [])]

    def prev_close(self, ticker: str) -> Optional[float]:
        j = self._get(f"/v2/aggs/ticker/{ticker}/prev", adjusted="true")
        res = j.get("results") or []
        return res[0]["c"] if res else None


# ----------------------------------------------------------------------------
# Metric extraction
# ----------------------------------------------------------------------------

def _fin(stmt: dict, section: str, key: str) -> Optional[float]:
    try:
        return stmt["financials"][section][key]["value"]
    except (KeyError, TypeError):
        return None


@dataclass
class RawMetrics:
    ticker: str
    sector: str = "Unknown"
    price: Optional[float] = None
    market_cap: Optional[float] = None
    # Value
    pe: Optional[float] = None
    ps: Optional[float] = None
    peg: Optional[float] = None
    # Growth
    rev_growth_yoy: Optional[float] = None
    eps_growth_yoy: Optional[float] = None
    # Profitability
    gross_margin: Optional[float] = None
    ebitda_margin: Optional[float] = None
    net_margin: Optional[float] = None
    roe: Optional[float] = None
    # Momentum
    ret_1m: Optional[float] = None
    ret_3m: Optional[float] = None
    ret_6m: Optional[float] = None
    ret_12m: Optional[float] = None
    # Revisions proxy (earnings trajectory: TTM EPS now vs TTM EPS two quarters ago)
    eps_trend: Optional[float] = None
    notes: list[str] = field(default_factory=list)


def extract_metrics(client: MMDClient, ticker: str) -> RawMetrics:
    m = RawMetrics(ticker=ticker)

    # --- Reference / sector ---
    try:
        det = client.ticker_details(ticker)
        m.sector = det.get("sic_description") or det.get("sector") or "Unknown"
        m.market_cap = det.get("market_cap")
        shares = det.get("weighted_shares_outstanding") or det.get("share_class_shares_outstanding")
    except Exception as e:
        m.notes.append(f"details: {e}")
        shares = None

    # --- Price & momentum ---
    try:
        closes = client.daily_closes(ticker)
        if closes:
            m.price = closes[-1]
            def ret(n):
                return (closes[-1] / closes[-n] - 1) * 100 if len(closes) > n else None
            m.ret_1m, m.ret_3m = ret(21), ret(63)
            m.ret_6m, m.ret_12m = ret(126), ret(252)
    except Exception as e:
        m.notes.append(f"prices: {e}")

    # --- Fundamentals ---
    try:
        q = client.financials(ticker, limit=10)
        if len(q) >= 4:
            def ttm(section, key, offset=0):
                vals = [_fin(s, section, key) for s in q[offset:offset + 4]]
                return sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None

            rev_ttm = ttm("income_statement", "revenues")
            ni_ttm = ttm("income_statement", "net_income_loss")
            gp_ttm = ttm("income_statement", "gross_profit")
            opinc_ttm = ttm("income_statement", "operating_income_loss")
            eps_ttm = ttm("income_statement", "diluted_earnings_per_share")
            equity = _fin(q[0], "balance_sheet", "equity")

            if rev_ttm:
                m.gross_margin = (gp_ttm / rev_ttm * 100) if gp_ttm is not None else None
                m.net_margin = (ni_ttm / rev_ttm * 100) if ni_ttm is not None else None
                # EBITDA proxy: operating income margin (D&A not always exposed)
                m.ebitda_margin = (opinc_ttm / rev_ttm * 100) if opinc_ttm is not None else None
            if equity and ni_ttm is not None and equity > 0:
                m.roe = ni_ttm / equity * 100

            # YoY growth: TTM now vs TTM one year earlier
            if len(q) >= 8:
                rev_prior = ttm("income_statement", "revenues", offset=4)
                eps_prior = ttm("income_statement", "diluted_earnings_per_share", offset=4)
                if rev_ttm and rev_prior:
                    m.rev_growth_yoy = (rev_ttm / rev_prior - 1) * 100
                if eps_ttm and eps_prior and eps_prior > 0:
                    m.eps_growth_yoy = (eps_ttm / eps_prior - 1) * 100

            # Revisions proxy: EPS trajectory over last two quarters
            if len(q) >= 6:
                eps_recent = ttm("income_statement", "diluted_earnings_per_share", offset=0)
                eps_2q_ago = ttm("income_statement", "diluted_earnings_per_share", offset=2)
                if eps_recent is not None and eps_2q_ago not in (None, 0):
                    m.eps_trend = (eps_recent / abs(eps_2q_ago) - math.copysign(1, eps_2q_ago)) * 100

            # Valuation
            if m.price and eps_ttm and eps_ttm > 0:
                m.pe = m.price / eps_ttm
                if m.eps_growth_yoy and m.eps_growth_yoy > 0:
                    m.peg = m.pe / m.eps_growth_yoy
            if m.market_cap and rev_ttm:
                m.ps = m.market_cap / rev_ttm
            elif m.price and shares and rev_ttm:
                m.ps = (m.price * shares) / rev_ttm
    except Exception as e:
        m.notes.append(f"financials: {e}")

    return m


# ----------------------------------------------------------------------------
# Sector-relative scoring
# ----------------------------------------------------------------------------

# metric -> (factor, higher_is_better)
METRIC_MAP = {
    "pe": ("value", False),
    "ps": ("value", False),
    "peg": ("value", False),
    "rev_growth_yoy": ("growth", True),
    "eps_growth_yoy": ("growth", True),
    "gross_margin": ("profitability", True),
    "ebitda_margin": ("profitability", True),
    "net_margin": ("profitability", True),
    "roe": ("profitability", True),
    "ret_1m": ("momentum", True),
    "ret_3m": ("momentum", True),
    "ret_6m": ("momentum", True),
    "ret_12m": ("momentum", True),
    "eps_trend": ("revisions", True),
}


def percentile_rank(value: float, peers: list[float], higher_is_better: bool) -> float:
    if not peers:
        return 50.0
    below = sum(1 for p in peers if p < value)
    equal = sum(1 for p in peers if p == value)
    pct = (below + 0.5 * equal) / len(peers) * 100
    return pct if higher_is_better else 100 - pct


@dataclass
class ScoredStock:
    ticker: str
    sector: str
    price: Optional[float]
    factor_scores: dict        # factor -> 0-100
    factor_grades: dict        # factor -> letter
    peg: Optional[float]
    peg_grade: str
    composite: float
    verdict: str
    circuit_breaker: bool
    metrics: dict
    notes: list[str]


def score_universe(metrics: list[RawMetrics]) -> list[ScoredStock]:
    # Group peers by sector; fall back to whole universe if a sector has < 3 names
    by_sector: dict[str, list[RawMetrics]] = {}
    for m in metrics:
        by_sector.setdefault(m.sector, []).append(m)

    results = []
    for m in metrics:
        peers = by_sector[m.sector] if len(by_sector[m.sector]) >= 3 else metrics

        factor_pcts: dict[str, list[float]] = {}
        for attr, (factor, hib) in METRIC_MAP.items():
            v = getattr(m, attr)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            peer_vals = [getattr(p, attr) for p in peers if getattr(p, attr) is not None]
            if len(peer_vals) < 2:
                continue
            factor_pcts.setdefault(factor, []).append(percentile_rank(v, peer_vals, hib))

        factor_scores = {f: round(sum(v) / len(v), 1) for f, v in factor_pcts.items()}
        factor_grades = {f: pct_to_grade(s) for f, s in factor_scores.items()}

        # PEG grade (sector-relative, lower better)
        peg_peers = [p.peg for p in peers if p.peg is not None and p.peg > 0]
        peg_pct = percentile_rank(m.peg, peg_peers, False) if (m.peg and peg_peers) else None
        peg_grade = pct_to_grade(peg_pct)

        # Composite
        w = CONFIG["weights"]
        total_w = sum(w[f] for f in factor_scores)
        composite = round(sum(factor_scores[f] * w[f] for f in factor_scores) / total_w, 1) if total_w else 50.0

        # Verdict + circuit breaker
        verdict = next(v for cutoff, v in CONFIG["verdicts"] if composite >= cutoff)
        cb = False
        if (
            verdict == "STRONG BUY"
            and factor_grades.get("value") in CONFIG["circuit_breaker_value_grades"]
            and peg_grade not in CONFIG["circuit_breaker_peg_override"]
        ):
            verdict, cb = "HOLD", True

        results.append(ScoredStock(
            ticker=m.ticker, sector=m.sector, price=m.price,
            factor_scores=factor_scores, factor_grades=factor_grades,
            peg=round(m.peg, 2) if m.peg else None, peg_grade=peg_grade,
            composite=composite, verdict=verdict, circuit_breaker=cb,
            metrics={k: (round(v, 2) if isinstance(v, float) else v)
                     for k, v in asdict(m).items() if k not in ("notes",)},
            notes=m.notes,
        ))
    return sorted(results, key=lambda r: r.composite, reverse=True)


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------

def print_report(scored: list[ScoredStock]):
    hdr = f"{'TICKER':<7}{'VERDICT':<13}{'SCORE':<7}{'VAL':<5}{'GRW':<5}{'PROF':<6}{'MOM':<5}{'REV':<5}{'PEG':<7}{'PEG-G':<6}{'PRICE':<9}"
    print("\n" + "=" * len(hdr))
    print("GARP PLUS SCANNER — 5-Factor Sector-Relative Scoring")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for s in scored:
        g = s.factor_grades
        flag = " *CB" if s.circuit_breaker else ""
        print(
            f"{s.ticker:<7}{s.verdict + flag:<13}{s.composite:<7}"
            f"{g.get('value','--'):<5}{g.get('growth','--'):<5}{g.get('profitability','--'):<6}"
            f"{g.get('momentum','--'):<5}{g.get('revisions','--'):<5}"
            f"{(s.peg if s.peg is not None else '--'):<7}{s.peg_grade:<6}"
            f"{('$' + format(s.price, '.2f')) if s.price else '--':<9}"
        )
    print("-" * len(hdr))
    print("*CB = circuit breaker triggered (Value D-/F without PEG override -> capped at HOLD)")
    print(f"Weights: {CONFIG['weights']}\n")


def main():
    ap = argparse.ArgumentParser(description="GARP Plus 5-factor quant scanner")
    ap.add_argument("--tickers", help="Comma-separated tickers")
    ap.add_argument("--tickers-file", help="File with one ticker per line")
    ap.add_argument("--json", help="Write full results to JSON file")
    args = ap.parse_args()

    tickers = []
    if args.tickers:
        tickers += [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.tickers_file:
        with open(args.tickers_file) as f:
            tickers += [l.strip().upper() for l in f if l.strip() and not l.startswith("#")]
    if not tickers:
        ap.error("Provide --tickers or --tickers-file")

    client = MMDClient()
    print(f"Fetching data for {len(tickers)} tickers...")
    metrics = []
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {t}", flush=True)
        metrics.append(extract_metrics(client, t))

    scored = score_universe(metrics)
    print_report(scored)

    if args.json:
        with open(args.json, "w") as f:
            json.dump([asdict(s) for s in scored], f, indent=2, default=str)
        print(f"Full results written to {args.json}")


if __name__ == "__main__":
    main()
