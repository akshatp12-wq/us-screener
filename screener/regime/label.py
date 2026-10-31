"""Overall regime label: Calm / Stretched / Fragile.

Fragile   = (credit gap red OR a red zone red) AND Stretched
Stretched = euphoric sentiment OR >= 20% of sector/industry ETFs bubble-flagged
            (v2: OR credit dial red on its own, via regime.credit_alone_label)
Calm      = otherwise

This is a reading of risk now, not a forecast of when a decline starts: in the 1960-2026 replay the
label mostly turned at or after market peaks.
"""
from __future__ import annotations


def regime_label(credit_gap_status: str, red_zone_statuses: list[str], sentiment_label: str | None,
                 bubble_flagged_share: float | None, cfg: dict) -> dict:
    stretched_share = cfg["bubble"]["stretched_share"]
    euphoric = sentiment_label == "euphoric"
    bubbly = bubble_flagged_share is not None and bubble_flagged_share >= stretched_share
    stretched = euphoric or bubbly
    credit_red = credit_gap_status == "red" or any(s == "red" for s in red_zone_statuses)
    if credit_red and stretched:
        label = "Fragile"
    elif stretched:
        label = "Stretched"
    elif credit_red:
        label = cfg["regime"].get("credit_alone_label", "Calm")
    else:
        label = "Calm"
    reasons = []
    if euphoric:
        reasons.append("sentiment euphoric")
    if bubbly:
        reasons.append(f"{bubble_flagged_share:.0%} of sector/industry ETFs bubble-flagged")
    if credit_red:
        reasons.append("credit dial red")
    missing = []
    if credit_gap_status == "unavailable":
        missing.append("credit gap")
    if sentiment_label is None:
        missing.append("sentiment")
    if bubble_flagged_share is None:
        missing.append("bubble watch")
    return {
        "label": label,
        "multiplier": cfg["regime"]["multipliers"][label],
        "stretched": stretched, "euphoric": euphoric, "credit_red": credit_red,
        "reasons": reasons or ["no stretch or credit warnings"],
        "missing_inputs": missing,
    }
