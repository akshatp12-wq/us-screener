"""B. Sentiment and risk appetite: live composite proxy, Baker-Wurgler level, variance risk premium."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..util import last_pct

CITE = {
    "sentiment": "Baker & Wurgler (2006), 'Investor Sentiment and the Cross-Section of Stock Returns', JF",
    "vrp": "Bollerslev, Tauchen & Zhou (2009), 'Expected Stock Returns and Variance Risk Premia', RFS",
}


def rolling_z(s: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    mp = min_periods or max(60, window // 4)
    mu = s.rolling(window, min_periods=mp).mean()
    sd = s.rolling(window, min_periods=mp).std()
    return (s - mu) / sd


def rel_return(ret: pd.DataFrame, a: str, b: str, window: int) -> pd.Series | None:
    if ret is None or a not in ret or b not in ret:
        return None
    x = ret[[a, b]].dropna()
    if len(x) < window + 60:
        return None
    ga = np.expm1(np.log1p(x[a]).rolling(window).sum())
    gb = np.expm1(np.log1p(x[b]).rolling(window).sum())
    return (ga - gb).dropna()


def sentiment_composite(vix: pd.Series | None, ret: pd.DataFrame | None, cfg: dict,
                        bw: pd.Series | None = None) -> dict:
    sc = cfg["sentiment"]
    w, rw = sc["zscore_window_days"], sc["rel_window_days"]
    comps = {}
    if vix is not None and len(vix.dropna()) > 300:
        comps["vix"] = -rolling_z(vix.dropna(), w)  # low VIX -> euphoric
    hb = rel_return(ret, "SPHB", "SPLV", rw)
    if hb is not None:
        comps["high_beta_vs_low_vol"] = rolling_z(hb, w)
    ipo = rel_return(ret, "IPO", "SPY", rw)
    if ipo is not None:
        comps["ipo_vs_market"] = rolling_z(ipo, w)
    out = {"components": {}, "baker_wurgler": None}
    if bw is not None and len(bw):
        out["baker_wurgler"] = {"value": float(bw.iloc[-1]), "as_of": bw.index[-1],
                                "note": "Baker-Wurgler index (monthly, published with a long lag)"}
    if not comps:
        out.update({"status": "unavailable", "reason": "no live sentiment inputs (VIX/ETF prices)", "series": None})
        return out
    df = pd.DataFrame(comps).dropna(how="all")
    raw = df.mean(axis=1, skipna=True)
    comp = rolling_z(raw, w).dropna()
    if comp.empty:
        comp = raw
    v = float(comp.iloc[-1])
    for k, s in comps.items():
        s = s.dropna()
        out["components"][k] = {"z": float(s.iloc[-1]) if len(s) else None,
                                "as_of": s.index[-1] if len(s) else None}
    label = "euphoric" if v > sc["euphoric_z"] else ("fearful" if v < sc["fearful_z"] else "neutral")
    out.update({
        "value": v, "unit": "z-score", "as_of": comp.index[-1], "label": label,
        "percentile": last_pct(comp),
        "status": {"euphoric": "red", "fearful": "amber", "neutral": "green"}[label],
        "threshold": f"euphoric > +{sc['euphoric_z']} sd (red), fearful < {sc['fearful_z']} sd (amber)",
        "series": comp.resample("W").last(),
    })
    return out


def variance_risk_premium(vix: pd.Series | None, mkt_ret: pd.Series | None, cfg: dict) -> dict:
    v = cfg["sentiment"]["vrp"]
    if vix is None or mkt_ret is None or vix.dropna().empty or mkt_ret.dropna().empty:
        return {"status": "unavailable", "reason": "VIX or market returns unavailable", "series": None}
    iv = (vix.dropna() ** 2) / 12.0                                  # monthly implied variance, %^2
    rv = (np.log1p(mkt_ret.dropna()) * 100) ** 2
    rv = rv.rolling(v["rv_window_days"], min_periods=v["rv_window_days"] - 2).sum()
    df = pd.concat([iv.rename("iv"), rv.rename("rv")], axis=1).dropna()
    if len(df) < 250:
        return {"status": "unavailable", "reason": "insufficient overlapping history", "series": None}
    vrp = df["iv"] - df["rv"]
    pct = last_pct(vrp)
    st = "green" if pct >= v["green_pct"] else ("red" if pct < v["amber_pct"] else "amber")
    return {
        "value": float(vrp.iloc[-1]), "unit": "monthly variance, %^2 (VIX^2/12 - realised)",
        "as_of": vrp.index[-1], "percentile": pct, "status": st,
        "threshold": f"higher percentile = better next-quarter odds; green >= {int(v['green_pct']*100)}th, "
                     f"red < {int(v['amber_pct']*100)}th",
        "series": vrp.resample("W").last(),
    }
