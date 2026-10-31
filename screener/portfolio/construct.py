"""Turn mode scores into model portfolios: liquidity floor, position limits, sector caps,
turnover budget and estimated trading costs."""
from __future__ import annotations

import numpy as np
import pandas as pd


def liquid_mask(df: pd.DataFrame, cfg: dict) -> pd.Series:
    u = cfg["universe"]
    return (df["price"] >= u["min_price"]) & (df["dollar_volume"] >= u["min_dollar_volume"])


def cost_bps(dv: pd.Series, cfg: dict) -> pd.Series:
    c = cfg["portfolio"]["costs_bps"]
    return pd.Series(np.where(dv > 100e6, c["large"], np.where(dv > 20e6, c["mid"], c["small"])), index=dv.index)


def select(score: pd.Series, df: pd.DataFrame, n: int, cfg: dict, prev: list[str] | None = None,
           ascending: bool = False) -> list[str]:
    """Pick n names by score with sector caps and a turnover budget (keeps prior holdings ranked within a buffer)."""
    p = cfg["portfolio"]
    elig = score[liquid_mask(df, cfg).reindex(score.index).fillna(False)].dropna()
    ranked = elig.sort_values(ascending=ascending)
    ranks = pd.Series(np.arange(1, len(ranked) + 1), index=ranked.index)
    cap = max(1, int(np.floor(p["sector_cap"] * n)))
    sectors = df["sector"]
    chosen: list[str] = []
    counts: dict[str, int] = {}

    def add(t):
        s = sectors.get(t, "Unknown")
        if counts.get(s, 0) >= cap:
            return False
        chosen.append(t)
        counts[s] = counts.get(s, 0) + 1
        return True

    if prev:
        # turnover budget: keep prior holdings still ranked inside the buffer, then
        # replace at most `turnover_budget * n` names with the best new candidates
        keep_rank = n * p["keep_buffer"]
        held = [t for t in prev if t in ranks.index]
        held.sort(key=lambda t: ranks[t])
        max_new = max(1, int(np.ceil(p["turnover_budget"] * n)))
        keepable = [t for t in held if ranks[t] <= keep_rank]
        for t in keepable:
            if len(chosen) < n:
                add(t)
        budget = max(max_new, n - len(chosen))
        for t in ranked.index:
            if len(chosen) >= n or budget <= 0:
                break
            if t not in chosen and add(t):
                budget -= 1
    for t in ranked.index:
        if len(chosen) >= n:
            break
        if t not in chosen:
            add(t)
    return chosen


def _frame(names, w, df, score, why=None):
    out = pd.DataFrame({"ticker": names, "weight": w})
    out["sector"] = out["ticker"].map(df["sector"])
    out["name"] = out["ticker"].map(df.get("name", pd.Series(dtype=str)))
    out["score"] = out["ticker"].map(score)
    out["beta"] = out["ticker"].map(df["beta"])
    out["dollar_volume"] = out["ticker"].map(df["dollar_volume"])
    if why is not None:
        out["why"] = out["ticker"].map(why)
    return out


def long_only(score, df, cfg, mode_cfg, regime_label, prev=None, why=None):
    pc = mode_cfg["portfolio"]
    n = pc["n_by_regime"][regime_label] if "n_by_regime" in pc else pc["n"]
    gross = pc.get("gross_by_regime", {}).get(regime_label, 1.0)
    names = select(score, df, n, cfg, prev)
    if not names:
        return pd.DataFrame()
    w = min(1.0 / len(names), cfg["portfolio"]["max_position"]) * gross
    return _frame(names, [w] * len(names), df, score, why)


def market_neutral(score, df, cfg, mode_cfg, sentiment_label, prev_long=None, prev_short=None, why=None):
    pc = mode_cfg["portfolio"]
    longs = select(score, df, pc["n_long"], cfg, prev_long)
    shorts = select(score.drop(longs, errors="ignore"), df, pc["n_short"], cfg, prev_short, ascending=True)
    if not longs or not shorts:
        return pd.DataFrame()
    scale = mode_cfg["short_scale_by_sentiment"].get(sentiment_label or "neutral", 1.0)
    bl = df["beta"].reindex(longs).fillna(1.0).clip(0.2, 3).mean()
    bs = df["beta"].reindex(shorts).fillna(1.0).clip(0.2, 3).mean()
    gl = 1.0 * scale
    gs = gl * bl / bs                          # beta-neutral: gl*bl == gs*bs
    lw = [gl / len(longs)] * len(longs)
    sw = [-gs / len(shorts)] * len(shorts)
    out = pd.concat([_frame(longs, lw, df, score, why).assign(side="long"),
                     _frame(shorts, sw, df, score, why).assign(side="short")], ignore_index=True)
    return out


def absolute_return(score, df, cfg, mode_cfg, regime, basket_vol, trend, prev=None, why=None):
    pc = mode_cfg["portfolio"]
    names = select(score, df, pc["n"], cfg, prev)
    if not names:
        return pd.DataFrame(), {}
    expo = min(mode_cfg["max_leverage"], mode_cfg["target_vol"] / basket_vol) if basket_vol and basket_vol > 0 else 1.0
    expo *= regime["multiplier"]
    sleeve = mode_cfg["trend_sleeve_share"]
    eq_w = expo * (1 - sleeve)
    w = min(eq_w / len(names), cfg["portfolio"]["max_position"] * max(1.0, expo))
    out = _frame(names, [w] * len(names), df, score, why).assign(side="long")
    ups = [v["symbol"] for v in trend.values() if v.get("signal") == "up"]
    trend_rows = []
    if ups:
        for s in ups:
            trend_rows.append({"ticker": s, "weight": sleeve / len(ups), "sector": "Trend sleeve", "side": "trend"})
    meta = {"vol_target_exposure": expo, "basket_vol": basket_vol, "equity_weight": eq_w,
            "trend_sleeve": sleeve if ups else 0.0, "cash": max(0.0, 1 - eq_w - (sleeve if ups else 0.0)),
            "trend_assets_up": ups}
    if trend_rows:
        out = pd.concat([out, pd.DataFrame(trend_rows)], ignore_index=True)
    return out, meta


def turnover_and_cost(new: pd.DataFrame, prev_w: dict | None, df: pd.DataFrame, cfg: dict) -> dict:
    if new is None or new.empty:
        return {"turnover": None, "cost_bps": None}
    nw = new.set_index("ticker")["weight"]
    pw = pd.Series(prev_w or {}, dtype=float)
    allt = nw.index.union(pw.index)
    d = (nw.reindex(allt).fillna(0) - pw.reindex(allt).fillna(0)).abs()
    dv = df["dollar_volume"].reindex(allt).fillna(50e6)
    bps = cost_bps(dv, cfg)
    one_way = float(d.sum() / 2)
    return {"turnover": one_way, "cost_bps": float((d * bps).sum()),
            "first_build": not bool(prev_w)}


def summarize(port: pd.DataFrame) -> dict:
    if port is None or port.empty:
        return {}
    eq = port[port.get("side", "long") != "trend"] if "side" in port else port
    gross = float(port["weight"].abs().sum())
    net = float(port["weight"].sum())
    beta = float((eq["weight"] * eq["beta"].fillna(1.0)).sum()) if "beta" in eq else None
    sect = eq.groupby("sector")["weight"].sum().sort_values(ascending=False).to_dict()
    return {"gross": gross, "net": net, "beta": beta, "n": int(len(eq)), "sector_weights": sect}
