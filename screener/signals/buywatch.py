"""Buy watch: the one entry pattern that worked both in 1960-1999 and out of sample since 2000.

Signal: the market fell at least 15% from its high within the last 12 months and has since closed a
month back above its 10-month average. Capitulation flag: a 20%+ drawdown with VIX >= 35 or
fearful sentiment (best entries historically, but it fired only twice after 2000).

It is meant to speed up money that was going to be invested anyway. Holding a cash reserve to wait
for it cost about 0.6% a year since 1960 in the crash study (screener/backtest/crashes.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CITE = ("Crash study in this repo: rules chosen on 1960-1999 and tested on 2000+; "
        "Faber (2007) for the 10-month average")


def buy_watch(mkt_daily: pd.Series | None, cfg: dict, vix: pd.Series | None = None,
              sentiment_z: float | None = None) -> dict:
    bw = cfg["buy_watch"]
    if mkt_daily is None or mkt_daily.dropna().shape[0] < 400:
        return {"status": "unavailable", "reason": "not enough market history"}
    r = mkt_daily.dropna()
    px = (1 + r).cumprod()
    m = px.resample("ME").last()              # last value = latest close for the current month
    n, look = int(bw["sma_months"]), int(bw["lookback_months"])
    sma = m.rolling(n).mean()
    above = m > sma
    dd_ath = m / m.cummax() - 1
    worst_dd = float(dd_ath.iloc[-look:].min())
    dd_now = float(px.iloc[-1] / px.cummax().iloc[-1] - 1)
    above_now = bool(above.iloc[-1])
    # month in which the index last crossed back above its average
    cross = above & ~above.shift(1, fill_value=True)
    last_cross = cross[cross].index[-1] if cross.any() else None
    months_since_cross = int(len(m.loc[last_cross:]) - 1) if last_cross is not None else None
    setup = worst_dd <= -bw["drawdown"]
    signal = bool(setup and above_now and months_since_cross is not None and months_since_cross <= 2)
    vix_now = float(vix.dropna().iloc[-1]) if vix is not None and len(vix.dropna()) else None
    capit = bool(dd_now <= -bw["capitulation_drawdown"] and (
        (vix_now is not None and vix_now >= bw["capitulation_vix"]) or
        (sentiment_z is not None and sentiment_z <= bw["capitulation_sentiment_z"])))
    if signal:
        state, status = "Buy signal", "green"
        text = (f"The market fell {abs(worst_dd):.0%} from its high within the last {look} months and has closed "
                f"back above its {n}-month average. Historically a good time to speed up planned investing.")
    elif setup and not above_now:
        state, status = "Watching", "amber"
        text = (f"The market is down {abs(worst_dd):.0%} from its high but still below its {n}-month average. "
                "The signal fires when it closes a month back above it.")
    else:
        state, status = "No setup", "neutral"
        text = (f"No {bw['drawdown']:.0%}+ drawdown in the last {look} months (worst: {abs(worst_dd):.0%}). "
                "Invest on your normal schedule; holding cash to wait for dips has cost more than it saved.")
    return {
        "status": status, "state": state, "text": text, "capitulation": capit,
        "drawdown_now": dd_now, "worst_drawdown_lookback": worst_dd, "above_sma": above_now,
        "sma_months": n, "months_since_cross": months_since_cross, "vix": vix_now,
        "sentiment_z": None if sentiment_z is None or (isinstance(sentiment_z, float) and np.isnan(sentiment_z)) else float(sentiment_z),
        "rule": f"Drawdown >= {bw['drawdown']:.0%} within {look} months, then a monthly close above the {n}-month average",
        "capitulation_rule": (f"Drawdown >= {bw['capitulation_drawdown']:.0%} and (VIX >= {bw['capitulation_vix']} "
                              f"or sentiment z <= {bw['capitulation_sentiment_z']})"),
        "use": "Speeds up money you were going to invest anyway. Not a reason to hold cash.",
    }
