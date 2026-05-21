"""
Generate strategy notes for a ticker's reflexivity reading.

Uses Claude (claude-haiku-4-5-20251001) when ANTHROPIC_API_KEY is set
and composite >= 55; otherwise falls back to rule-based notes.
"""

import logging
import os

log = logging.getLogger("vwap_scanner")

_client = None
_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def _get_client():
    global _client
    if _client is None and _ANTHROPIC_KEY:
        try:
            import anthropic
            _client = anthropic.Anthropic(api_key=_ANTHROPIC_KEY)
        except ImportError:
            log.warning("anthropic package not installed — using rule-based strategy notes")
    return _client


def generate_note(
    symbol: str,
    composite: float,
    classification: str,
    momentum: float,
    volume: float,
    sentiment: float,
) -> str:
    client = _get_client()
    if client and composite >= 55:
        try:
            return _claude_note(client, symbol, composite, classification, momentum, volume, sentiment)
        except Exception as exc:
            log.debug("Claude note failed for %s: %s — falling back to rules", symbol, exc)

    return _rule_note(composite, classification, momentum, volume, sentiment)


def _claude_note(client, symbol, composite, classification, momentum, volume, sentiment) -> str:
    import anthropic
    prompt = (
        f"Ticker: {symbol}\n"
        f"Reflexivity Score: {composite:.0f}/100 | Classification: {classification}\n"
        f"Momentum: {momentum:.0f} | Volume: {volume:.0f} | Sentiment: {sentiment:.0f}\n\n"
        "Describe the current reflexivity loop state in 1-2 sentences and give a brief tactical note "
        "(ride momentum / caution / watch for reversal). Be direct and specific."
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=120,
        system="You are a concise quantitative trading assistant. Respond in 1-2 sentences only.",
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def _rule_note(composite, classification, momentum, volume, sentiment) -> str:
    if classification == "LOOP_ACTIVE":
        if momentum >= 80:
            return "Strong momentum loop — ride with tight trailing stop; watch for volume exhaustion."
        return "Active reflexivity loop — price self-reinforcing; size position conservatively."
    if classification == "LOOP_FORMING":
        if volume >= 65:
            return "Loop building with volume confirmation — monitor for breakout entry."
        return "Early loop formation — needs volume catalyst to accelerate."
    if classification == "LOOP_COOLING":
        if sentiment >= 60:
            return "Loop cooling but sentiment still positive — wait for re-acceleration or reduce exposure."
        return "Loop decelerating — avoid new longs; manage existing positions."
    return "No active reflexivity loop — stand aside or look for other setups."
