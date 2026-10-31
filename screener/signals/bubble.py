"""C. Bubble and crash monitors: sector/industry bubble watch, momentum crash guard, market trend."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..util import expanding_pct

CITE = {
    "bubble": "Greenwood, Shleifer & You (2019), 'Bubbles for Fama', Journal of Financial Economics",
    "crash_guard": "Daniel & Moskowitz (2016), 'Momentum Crashes', Journal of Financial Economics",
    "trend": "Moskowitz, Ooi & Pedersen (2012), 'Time Series Momentum', JFE; Faber (2007) 10-month SMA",
}


def _cum(r: pd.Series) -> float:
    r = r.dropna()
    return float(np.expm1(np.log1p(r).sum())) if len(r) else np.nan


def bubble_metrics(r_daily: pd.Series, mkt_daily: pd.Series, cfg: dict, issuance_z: float | None = None) -> dict:
    """Bubble warning for one sector/industry return series (daily total returns)."""
    b = cfg["bubble"]
    days = int(b["window_months"] * 21)
    x = pd.concat([r_daily.rename("r"), mkt_daily.rename("m")], axis=1).dropna().iloc[-days:]
    if len(x) < days * 0.9:
        return {"status": "unavailable", "reason": "less than 2 years of history"}
    r2, m2 = _cum(x["r"]), _cum(x["m"])
    excess = r2 - m2
    recent = _cum(x["r"].iloc[-b["accel_months"] * 21:])
    accel = (np.log1p(recent) / np.log1p(r2)) if r2 > 0 and recent > 0 else 0.0
    vol_now = x["r"].iloc[-252:].std() * np.sqrt(252)
    vol_prev = x["r"].iloc[-504:-252].std() * np.sqrt(252)
    vol_ratio = vol_now / vol_prev if vol_prev > 0 else np.nan
    warnings = []
    if accel > b["accel_share"]:
        warnings.append("price acceleration")
    if vol_ratio > b["vol_ratio"]:
        warnings.append("rising volatility")
    if issuance_z is not None and issuance_z > 1.0:
        warnings.append("rising issuance")
    status = "red" if excess >= b["red_excess"] else ("amber" if excess >= b["amber_excess"] else "green")
    return {
        "return_2y": r2, "market_2y": m2, "excess_2y": excess, "accel_share": accel,
        "vol_1y": vol_now, "vol_ratio": vol_ratio, "issuance_z": issuance_z,
        "warnings": warnings, "status": status,
    }


def bubble_watch(ret: pd.DataFrame, cfg: dict, bench: str = "SPY",
                 sector_issuance: dict | None = None) -> dict:
    rows = []
    if ret is None or bench not in ret:
        return {"status": "unavailable", "reason": "price data unavailable", "rows": []}
    for t, meta in cfg["etfs"]["list"].items():
        if meta.get("kind") not in ("sector", "industry") or t not in ret:
            continue
        iz = (sector_issuance or {}).get(meta.get("gics")) if meta.get("gics") else None
        m = bubble_metrics(ret[t], ret[bench], cfg, iz)
        m.update({"ticker": t, "name": meta["name"], "kind": meta["kind"]})
        rows.append(m)
    valid = [r for r in rows if r.get("status") in ("green", "amber", "red")]
    flagged = [r for r in valid if r["status"] in ("amber", "red")]
    share = len(flagged) / len(valid) if valid else None
    return {
        "rows": sorted(rows, key=lambda r: -(r.get("excess_2y") or -9)),
        "flagged_share": share, "n_valid": len(valid), "n_flagged": len(flagged),
        "stretched": share is not None and share >= cfg["bubble"]["stretched_share"],
        "threshold": f"2y return minus market >= {int(cfg['bubble']['amber_excess']*100)}% amber, "
                     f">= {int(cfg['bubble']['red_excess']*100)}% red",
    }


def group_bubbles(ret: pd.DataFrame, groups: pd.Series, bench: pd.Series, cfg: dict, min_members: int = 3) -> list:
    """Equal-weighted GICS sub-industry baskets from the stock universe."""
    out = []
    for g, members in groups.groupby(groups):
        cols = [c for c in members.index if c in ret]
        if len(cols) < min_members:
            continue
        r = ret[cols].mean(axis=1, skipna=True)
        m = bubble_metrics(r, bench, cfg)
        if m.get("status") in ("amber", "red"):
            m.update({"group": g, "members": len(cols)})
            out.append(m)
    return sorted(out, key=lambda r: -r["excess_2y"])


def crash_guard(mkt_daily: pd.Series, cfg: dict) -> dict:
    c = cfg["momentum_crash_guard"]
    x = mkt_daily.dropna()
    if len(x) < 600:
        return {"status": "unavailable", "on": False, "reason": "insufficient market history"}
    r24 = _cum(x.iloc[-int(c["lookback_months"] * 21):])
    vol = x.rolling(c["vol_window_days"]).std().dropna() * np.sqrt(252)
    vpct = float((vol <= vol.iloc[-1]).mean())
    on = bool(r24 < 0 and vpct >= c["vol_pct"])
    return {"on": on, "market_24m": r24, "vol_126d": float(vol.iloc[-1]), "vol_pct": vpct,
            "momentum_multiplier": c["momentum_multiplier"] if on else 1.0,
            "status": "red" if on else ("amber" if r24 < 0 or vpct >= c["vol_pct"] else "green"),
            "threshold": f"on when market 24m return < 0 AND 126d vol >= {int(c['vol_pct']*100)}th pct"}


def trend_signals(ret: pd.DataFrame, cfg: dict, rf_annual: float = 0.0) -> dict:
    t = cfg["trend"]
    out = {}
    for name, sym in t["assets"].items():
        if ret is None or sym not in ret:
            out[name] = {"symbol": sym, "status": "unavailable", "reason": "no prices"}
            continue
        r = ret[sym].dropna()
        if len(r) < 260:
            out[name] = {"symbol": sym, "status": "unavailable", "reason": "short history"}
            continue
        px = (1 + r).cumprod()
        m = px.resample("ME").last()
        r12 = float(m.iloc[-1] / m.iloc[-13] - 1) if len(m) > 13 else np.nan
        sma = float(m.iloc[-t["sma_months"]:].mean())
        above = bool(px.iloc[-1] > sma)
        tsmom = r12 - rf_annual > 0
        sig = "up" if (tsmom and above) else ("down" if (not tsmom and not above) else "mixed")
        out[name] = {"symbol": sym, "ret_12m": r12, "excess_12m": r12 - rf_annual, "above_10m_sma": above,
                     "signal": sig, "status": {"up": "green", "mixed": "amber", "down": "red"}[sig]}
    return out


def bubble_history(ind_monthly: pd.DataFrame, mkt_monthly: pd.Series, cfg: dict) -> pd.DataFrame:
    """Monthly 2-year excess returns for every industry (used by validation and the regime history)."""
    b = cfg["bubble"]
    n = b["window_months"]
    li = np.log1p(ind_monthly).rolling(n, min_periods=n).sum()
    lm = np.log1p(mkt_monthly).rolling(n, min_periods=n).sum()
    return np.expm1(li).sub(np.expm1(lm), axis=0)


def flagged_share_history(ind_monthly: pd.DataFrame, mkt_monthly: pd.Series, cfg: dict) -> pd.Series:
    ex = bubble_history(ind_monthly, mkt_monthly, cfg)
    flagged = (ex >= cfg["bubble"]["amber_excess"]).sum(axis=1)
    valid = ex.notna().sum(axis=1)
    return (flagged / valid.replace(0, np.nan)).dropna()


__all__ = ["bubble_watch", "crash_guard", "trend_signals", "bubble_metrics", "group_bubbles",
           "bubble_history", "flagged_share_history", "expanding_pct"]
