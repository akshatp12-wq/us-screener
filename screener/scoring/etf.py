"""E. ETF scoring: momentum and trend, bubble penalty, risk-adjusted momentum, holdings look-through."""
from __future__ import annotations

import numpy as np
import pandas as pd

CITE = {
    "etf_momentum": "Moskowitz & Grinblatt (1999) industry momentum; Moskowitz, Ooi & Pedersen (2012)",
    "lookthrough": "Holdings-weighted average of this screener's stock scores (SPDR daily holdings files)",
}


def etf_signals(ret: pd.DataFrame, close: pd.DataFrame | None, volume: pd.DataFrame | None,
                cfg: dict, asof=None) -> pd.DataFrame:
    rows = {}
    for t, meta in cfg["etfs"]["list"].items():
        if ret is None or t not in ret:
            rows[t] = {"name": meta["name"], "kind": meta["kind"], "er": meta.get("er"), "available": False}
            continue
        r = ret[t].loc[:asof].dropna() if asof is not None else ret[t].dropna()
        if len(r) < 270:
            rows[t] = {"name": meta["name"], "kind": meta["kind"], "er": meta.get("er"), "available": False}
            continue
        px = (1 + r).cumprod()
        m = px.resample("ME").last()
        mom = float(m.iloc[-2] / m.iloc[-13] - 1) if len(m) >= 13 else np.nan
        r12 = float(m.iloc[-1] / m.iloc[-13] - 1) if len(m) >= 13 else np.nan
        sma = float(m.iloc[-10:].mean())
        above = bool(px.iloc[-1] > sma)
        vol = float(r.iloc[-252:].std() * np.sqrt(252))
        trend = (1.0 if (r12 > 0 and above) else 0.0 if (r12 <= 0 and not above) else 0.5)
        dv = np.nan
        if close is not None and volume is not None and t in close and t in volume:
            dv = float((close[t] * volume[t]).iloc[-63:].median())
        rows[t] = {"name": meta["name"], "kind": meta["kind"], "er": meta.get("er"), "available": True,
                   "momentum": mom, "ret_12m": r12, "above_sma": above, "trend": trend, "vol": vol,
                   "risk_adj_momentum": mom / vol if vol > 0 else np.nan, "dollar_volume": dv}
    return pd.DataFrame.from_dict(rows, orient="index")


def lookthrough(holdings: dict[str, pd.DataFrame], stock_score: pd.Series) -> pd.DataFrame:
    out = {}
    for etf, h in holdings.items():
        x = h.copy()
        x["score"] = x["ticker"].map(stock_score)
        cov = x.loc[x["score"].notna(), "weight"].sum()
        if cov <= 0:
            continue
        lt = float((x["score"] * x["weight"]).sum() / cov)
        out[etf] = {"lookthrough": lt, "coverage": float(cov)}
    return pd.DataFrame.from_dict(out, orient="index")


def score_etfs(sig: pd.DataFrame, lt_by_mode: dict[str, pd.DataFrame], bubble_rows: list, cfg: dict) -> dict:
    """Mode -> DataFrame of ETF scores (0-1, higher better)."""
    bub = {r["ticker"]: r.get("status") for r in bubble_rows if "ticker" in r}
    pen = cfg["etf_scoring"]["bubble_penalty"]
    res = {}
    av = sig[sig["available"] == True].copy()  # noqa: E712
    for mode, w in cfg["etf_scoring"]["weights"].items():
        df = av.copy()
        parts = {}
        if "momentum" in w:
            parts["momentum"] = df["momentum"].rank(pct=True)
        if "risk_adj_momentum" in w:
            parts["risk_adj_momentum"] = df["risk_adj_momentum"].rank(pct=True)
        parts["trend"] = df["trend"].astype(float)
        lt = lt_by_mode.get(mode)
        if lt is not None and not lt.empty:
            parts["lookthrough"] = lt["lookthrough"].reindex(df.index)
            df["lookthrough"] = parts["lookthrough"]
            df["lookthrough_coverage"] = lt["coverage"].reindex(df.index)
        P = pd.DataFrame(parts)
        W = pd.Series({k: v for k, v in w.items() if k in P})
        num = P.fillna(0).mul(W, axis=1).sum(axis=1)
        den = P.notna().mul(W, axis=1).sum(axis=1)
        score = num / den.replace(0, np.nan)
        df["bubble"] = df.index.map(lambda t: bub.get(t, "n/a"))
        df["bubble_penalty"] = df["bubble"].map({"amber": pen["amber"], "red": pen["red"]}).fillna(0.0)
        df["score"] = (score - df["bubble_penalty"]).clip(lower=0)
        res[mode] = df.sort_values("score", ascending=False)
    return res
