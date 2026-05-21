"""
Classify a ticker's reflexivity loop state from its scores.

States
------
LOOP_ACTIVE   — strong self-reinforcing feedback loop underway
LOOP_FORMING  — early loop signals building
LOOP_COOLING  — loop losing steam
NO_LOOP       — no reflexivity signal
"""


def classify(composite: float, momentum_score: float, volume_score: float) -> str:
    if composite >= 72 and momentum_score >= 60 and volume_score >= 60:
        return "LOOP_ACTIVE"
    if composite >= 55:
        return "LOOP_FORMING"
    if composite >= 38:
        return "LOOP_COOLING"
    return "NO_LOOP"
