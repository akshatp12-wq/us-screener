"""Backtest of the lead indicators (screener/signals/leads.py).

Each month, every indicator flags names (top decile, or two-plus insider buyers) among liquid stocks in
today's S&P 1500. We record the flagged names' next-month return minus the equal-weighted liquid
universe, the top-30% minus bottom-30% spread, and the rank IC. Results are split at 2011 where the
data allow. Because both the flagged names and the universe come from today's index, the survivorship
bias largely nets out of "flagged minus universe", though not entirely.

Residual momentum gets an extra survivorship-free test on Ken French's 49 industry portfolios since 1963.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..data.sec import snapshot
from ..portfolio.construct import liquid_mask
from ..signals.leads import (ETF_LEAD, LEADS, compute_leads, earnings_reactions, etf_monthly, etf_universe,
                             ff3_monthly, flags, residual_momentum)
from ..signals.stock import compute_stock_signals, first_trade_dates, momentum_12_1
from ..util import ann_stats
from .metrics import newey_west_t

log = logging.getLogger("screener.backtest")


def _period_stats(s: pd.Series, split: pd.Timestamp) -> dict:
    s = s.dropna()
    out = {}
    for name, part in (("pre", s[s.index < split]), ("post", s[s.index >= split]), ("full", s)):
        if len(part) < 6:
            out[name] = {"n": int(len(part))}
            continue
        out[name] = {"n": int(len(part)), "start": part.index[0].strftime("%Y-%m"), "end": part.index[-1].strftime("%Y-%m"),
                     "ann_mean": float(part.mean() * 12), "t": float(newey_west_t(part)) if len(part) > 12 else None,
                     "hit_rate": float((part > 0).mean())}
    return out


def lead_status(p: dict, cfg: dict) -> dict:
    st = cfg["leads"]["status"]
    full, pre, post = p.get("full", {}), p.get("pre", {}), p.get("post", {})
    t = full.get("t")
    has_pre = pre.get("n", 0) >= st["min_pre_months"]
    if t is None:
        return {"status": "Unproven", "reason": "not enough history"}
    pos_post = (post.get("ann_mean") or 0) > 0
    pos_pre = (pre.get("ann_mean") or 0) > 0
    both = pos_post and (pos_pre if has_pre else True)
    if has_pre and t >= st["validated_t"] and both:
        return {"status": "Validated", "reason": f"t = {t:.1f} and positive before and after 2011"}
    if t >= st["promising_t"] and both:
        return {"status": "Promising", "reason": f"t = {t:.1f}" + ("" if has_pre else "; no pre-2011 data to confirm it")}
    if t <= -st["promising_t"]:
        return {"status": "Unproven", "reason": f"t = {t:.1f}: flagged names did worse than the universe"}
    if has_pre and not both:
        return {"status": "Unproven", "reason": f"t = {t:.1f}; sign differs before and after 2011"}
    return {"status": "Unproven", "reason": f"t = {t:.1f}, below {st['promising_t']:.0f}"}


def industry_residual_momentum(ind49: pd.DataFrame | None, ff3: pd.DataFrame | None, split: pd.Timestamp,
                               start: str = "1963-07-31", top: int = 10) -> dict:
    """Long the `top` industries by residual (and, for comparison, raw) momentum, minus all 49 equal-weighted."""
    factors = ff3_monthly(ff3)
    if ind49 is None or factors is None:
        return {"status": "unavailable"}
    ind = ind49.copy()
    ind.index = ind.index + pd.offsets.MonthEnd(0)
    dates = ind.index[(ind.index >= pd.Timestamp(start))]
    res, raw = [], []
    for i, t in enumerate(dates[:-1]):
        nxt = ind.loc[dates[i + 1]]
        ew = nxt.mean()
        rm = residual_momentum(ind, factors, t, 36, 12, 1).dropna()
        mm = momentum_12_1(ind, t, 12, 1).dropna()
        if len(rm) >= 30:
            res.append((dates[i + 1], float(nxt.reindex(rm.nlargest(top).index).mean() - ew)))
        if len(mm) >= 30:
            raw.append((dates[i + 1], float(nxt.reindex(mm.nlargest(top).index).mean() - ew)))
    rs = pd.Series(dict(res)).sort_index()
    ms = pd.Series(dict(raw)).sort_index()
    corr = float(rs.corr(ms)) if len(rs) > 24 else None
    return {"residual": _period_stats(rs, split), "raw": _period_stats(ms, split),
            "residual_worst_month": float(rs.min()) if len(rs) else None, "raw_worst_month": float(ms.min()) if len(ms) else None,
            "correlation": corr,
            "note": f"Top {top} of Ken French's 49 value-weighted industries by momentum, minus all 49 equal-weighted; "
                    "residuals from a 36-month Fama-French three-factor regression. Survivorship-free. Gross of costs."}


def etf_residual_momentum(ret_d: pd.DataFrame, cfg: dict, ff3: pd.DataFrame | None, split: pd.Timestamp) -> dict:
    """Top-N sector/industry ETFs by residual (and raw) momentum minus all eligible ETFs, equal-weighted.
    Today's ETF list only, so funds that closed are missing; gross of costs."""
    factors = ff3_monthly(ff3)
    if factors is None:
        return {"status": "unavailable"}
    top = int(cfg["leads"]["etf"]["top"])
    m = etf_monthly(ret_d, etf_universe(cfg))
    res, raw = [], []
    for i, t in enumerate(m.index[:-1]):
        nxt = m.iloc[i + 1]
        rm = residual_momentum(m, factors, t, 36, 12, 1).dropna()
        rm = rm[nxt.reindex(rm.index).notna()]
        if len(rm) < 2 * top:
            continue
        ew = nxt.reindex(rm.index).mean()
        res.append((m.index[i + 1], float(nxt.reindex(rm.nlargest(top).index).mean() - ew)))
        mm = momentum_12_1(m, t, 12, 1).reindex(rm.index).dropna()
        if len(mm) >= 2 * top:
            raw.append((m.index[i + 1], float(nxt.reindex(mm.nlargest(top).index).mean() - ew)))
    rs, ms = pd.Series(dict(res)).sort_index(), pd.Series(dict(raw)).sort_index()
    return {"residual": _period_stats(rs, split), "raw": _period_stats(ms, split),
            "note": f"Top {top} of today's sector and industry ETFs by residual momentum vs all eligible ETFs, "
                    "equal-weighted, next month. Short history and today's ETF list only; gross of costs."}


def run_leads_backtest(univ: pd.DataFrame, panels: dict, derived: dict, cfg: dict, insiders: pd.DataFrame | None = None,
                       releases: dict | None = None, ff3: pd.DataFrame | None = None, ind49: pd.DataFrame | None = None,
                       bench: str = "SPY") -> dict:
    lc = cfg["leads"]
    split = pd.Timestamp(lc["status"]["split"])
    cost = float(lc["cost_round_trip"])
    ret_d = panels["ret"]
    tickers = [t for t in univ["ticker"] if t in ret_d.columns]
    ret_m = (1 + ret_d[tickers].fillna(0)).resample("ME").prod() - 1
    ret_m = ret_m.where(ret_d[tickers].notna().resample("ME").sum() > 0)
    last_complete = ret_d.index[-1] + pd.offsets.MonthEnd(0)
    if ret_d.index[-1] < last_complete:
        last_complete = last_complete - pd.offsets.MonthEnd(1)
    dates = ret_m.index[(ret_m.index >= ret_m.index[0] + pd.DateOffset(months=13)) & (ret_m.index < last_complete)]
    mkt = ret_d[bench] if bench in ret_d else ret_d[tickers].mean(axis=1)
    factors = ff3_monthly(ff3)
    cik_by_ticker = dict(zip(univ["ticker"], univ["cik"])) if "cik" in univ else {}
    reactions = earnings_reactions(ret_d[tickers], mkt, releases, cik_by_ticker) if releases else None
    if insiders is not None and not insiders.empty and "cik" in univ:
        insiders = insiders[insiders["issuer_cik"].isin(set(univ["cik"].dropna().astype(int)))]
    fd = first_trade_dates(ret_d[tickers])
    blank_vb = pd.DataFrame(np.nan, index=tickers, columns=["vol", "beta"])

    rec = {k: {"fx": [], "bear": [], "spread": [], "ic": [], "n": [], "new": []} for k in LEADS}
    prev = {k: set() for k in LEADS}
    for t in dates:
        i = ret_m.index.searchsorted(t, side="right")
        if i >= len(ret_m):
            break
        nd, nxt = ret_m.index[i], ret_m.iloc[i]
        fund = snapshot(derived, t) if derived else None
        df = compute_stock_signals(univ, fund, panels, t, cfg, insiders=None, short=None, mkt_symbol=bench,
                                   ret_m=ret_m.loc[:t], vb=blank_vb, first_dates=fd)
        alive = ret_d[df.index].loc[:t].iloc[-5:].notna().any()
        df = df[alive.reindex(df.index).fillna(False)]
        if len(df) < 100:
            continue
        liq = liquid_mask(df, cfg).fillna(False)
        L = compute_leads(df, panels, t, cfg, ret_m=ret_m.loc[:t], factors=factors, insiders=insiders, reactions=reactions)
        F = flags(L, liq, cfg)
        uni = nxt.reindex(df.index[liq])
        um = float(uni.mean())
        for k in LEADS:
            s = L.loc[liq[liq].index, k].dropna() if k in L else pd.Series(dtype=float)
            r = nxt.reindex(s.index)
            bull = F.get(k, {}).get("bull", pd.Index([]))
            minimum = 3 if k == "insider_buying" else 10
            if len(bull) >= minimum:
                fx = float(nxt.reindex(bull).mean() - um)
                rec[k]["fx"].append((nd, fx))
                rec[k]["n"].append((nd, len(bull)))
                newshare = len(set(bull) - prev[k]) / len(bull)
                rec[k]["new"].append((nd, newshare))
                prev[k] = set(bull)
            bear = F.get(k, {}).get("bear", pd.Index([]))
            if len(bear) >= 10:
                rec[k]["bear"].append((nd, float(nxt.reindex(bear).mean() - um)))
            if k != "insider_buying" and len(s) >= 100 and s.nunique() >= 10:
                hi, lo = s.quantile(0.7), s.quantile(0.3)
                rec[k]["spread"].append((nd, float(r[s >= hi].mean() - r[s <= lo].mean())))
                ok = r.notna()
                if ok.sum() > 50:
                    rec[k]["ic"].append((nd, float(s[ok].rank().corr(r[ok].rank()))))

    def ser(lst):
        return pd.Series(dict(lst)).sort_index() if lst else pd.Series(dtype=float)

    out = {"split": split.strftime("%Y-%m"), "flag_share": lc["flag_share"], "cost_round_trip": cost, "leads": {}}
    for k, meta in LEADS.items():
        fx, new = ser(rec[k]["fx"]), ser(rec[k]["new"])
        if fx.empty:
            out["leads"][k] = {"label": meta["label"], "status": "Unproven", "reason": "no months with enough flagged names"}
            continue
        net = fx - new.reindex(fx.index).fillna(1.0) * cost
        p = _period_stats(fx, split)
        st = lead_status(p, cfg)
        res = {"label": meta["label"], **st, "flagged_minus_universe": p, "net_of_costs": _period_stats(net, split),
               "avg_flagged": float(ser(rec[k]["n"]).mean()), "avg_new_share": float(new.mean()) if len(new) else None,
               "first_month": fx.index[0].strftime("%Y-%m")}
        if rec[k]["bear"]:
            bs = ser(rec[k]["bear"])
            res["bearish_minus_universe"] = _period_stats(bs, split)
            # bearish side "works" when its names lag the universe: judge it on the negated series
            bst = lead_status(_period_stats(-bs, split), cfg)
            bst["reason"] = bst["reason"].replace("flagged names did worse than the universe", "bearish names did better than the universe")
            res["bear_status"] = bst
        if rec[k]["spread"]:
            res["top30_minus_bottom30"] = _period_stats(ser(rec[k]["spread"]), split)
        ic = ser(rec[k]["ic"])
        if len(ic):
            res["ic_mean"] = float(ic.mean())
            res["ic_t"] = float(newey_west_t(ic)) if len(ic) > 12 else None
        curve = (1 + fx).cumprod()
        res["curve"] = [[d.strftime("%Y-%m"), round(float(v), 4)] for d, v in curve.items()]
        out["leads"][k] = res
    ind = industry_residual_momentum(ind49, ff3, split)
    out["industry_residual_momentum"] = ind
    etf_lead = {"label": ETF_LEAD["label"], "industry": ind, "etfs": etf_residual_momentum(ret_d, cfg, ff3, split)}
    if isinstance(ind.get("residual"), dict):
        etf_lead.update(lead_status(ind["residual"], cfg))
        etf_lead["evidence_basis"] = "Ken French 49 industries, 1963 on (survivorship-free)"
    else:
        etf_lead.update({"status": "Unproven", "reason": "industry data unavailable"})
    out["etf_lead"] = etf_lead
    out["notes"] = [
        "Flagged = top 10% of liquid stocks on the indicator (net opportunistic insider buying of 1 or more for insiders). Returns are the "
        "next month's, equal-weighted, minus the equal-weighted liquid universe.",
        f"Net of costs assumes {cost:.1%} round trip on every newly flagged name each month.",
        "Universe is today's S&P 1500 (survivorship-biased); flagged-minus-universe nets out much of that bias.",
        "Status: Validated = t >= 3 over the full period and positive both before and after 2011; Promising = t >= 2 "
        "(and positive in both periods where there is pre-2011 data); otherwise Unproven.",
        "Bearish sides (bottom 10%, or net opportunistic insider selling) get their own status, judged on how much "
        "their names lagged the universe.",
        "Opportunistic insider buying needs three years of each insider's history to separate routine from "
        "opportunistic traders; free filings start in 2008, so it can only be tested from 2011.",
        "The sector-ETF lead's status comes from the 49-industry test since 1963; the test on today's ETFs "
        "since 2007 is shown as a check.",
    ]
    return out
