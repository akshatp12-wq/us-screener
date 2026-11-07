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
from ..signals.leads import insider_last_trade
from ..signals.leads import (ETF_LEAD, LEADS, compute_leads, earnings_reactions, etf_monthly, etf_universe,
                             ff3_monthly, flags, residual_momentum)
from ..signals.stock import compute_stock_signals, first_trade_dates, momentum_12_1
from ..util import ann_stats
from ..scoring.composite import signal_scores
from .metrics import newey_west_t, ols

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


FACTOR_NAMES = {"Mkt-RF": "market", "SMB": "size", "HML": "value", "RMW": "profitability", "CMA": "investment",
                "Mom": "momentum"}


def factor_attribution(y: pd.Series, ff5: pd.DataFrame | None, mom: pd.Series | None, ff3: pd.DataFrame | None = None) -> dict:
    """Regress a monthly long-minus-universe series on Fama-French five factors plus momentum. Alpha is the
    return left after known factor exposures (annualised, Newey-West t)."""
    base = ff5 if ff5 is not None else ff3
    if base is None or y is None or len(y.dropna()) < 36:
        return {"status": "unavailable"}
    X = base[[c for c in ("Mkt-RF", "SMB", "HML", "RMW", "CMA") if c in base]].copy()
    if mom is not None:
        X["Mom"] = mom
    X.index = X.index + pd.offsets.MonthEnd(0)
    res = ols(y, X)
    if "coef" not in res:
        return {"status": "unavailable", "n": res.get("n")}
    return {"n": res["n"], "alpha_ann": res["coef"]["const"] * 12, "alpha_t": res["t"]["const"], "r2": res["r2"],
            "raw_ann": float(y.dropna().mean() * 12),
            "loadings": {FACTOR_NAMES.get(k, k): {"beta": v, "t": res["t"][k]} for k, v in res["coef"].items() if k != "const"}}


def _summarise_diag(rows: list[dict], split: pd.Timestamp) -> dict:
    if not rows:
        return {"status": "unavailable"}
    d = pd.DataFrame(rows).set_index("date").sort_index()
    out = {"months": int(len(d)), "start": d.index[0].strftime("%Y-%m"), "returns": {}, "composition": {}, "counts": {}}
    for c in ("spread", "spread_demeaned", "buyers", "sellers", "neutral", "buyers_minus_sellers",
              "buyers_minus_sellers_sector_neutral", "top_neutral_minus_bottom", "top_buyers_minus_bottom"):
        if c in d:
            out["returns"][c] = _period_stats(d[c], split)
    for c in d.columns:
        if c.startswith("top_share") or c.startswith("bot_share"):
            out["composition"][c] = float(d[c].mean())
        if c.startswith("n_"):
            out["counts"][c] = float(d[c].mean())
    return out


def run_leads_backtest(univ: pd.DataFrame, panels: dict, derived: dict, cfg: dict, insiders: pd.DataFrame | None = None,
                       releases: dict | None = None, ff3: pd.DataFrame | None = None, ind49: pd.DataFrame | None = None,
                       bench: str = "SPY", ff5: pd.DataFrame | None = None, mom: pd.Series | None = None) -> dict:
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
    idiag: list[dict] = []
    AGE_BUCKETS = [(0, 30), (31, 60), (61, 90), (91, 180)]
    age_rec: dict = {b: [] for b in AGE_BUCKETS}
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
        if "insider_buying" in L and L["insider_buying"].notna().sum() > 100:
            idiag_row = _insider_diag(df, L, liq, nxt, cfg, nd)
            if idiag_row:
                idiag.append(idiag_row)
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
            if k == "insider_buying" and len(bear) >= 10 and "cik" in df:
                last = insider_last_trade(insiders, t, df.loc[bear, "cik"].tolist(), lc["insider_buying"]["window_days"], -1)
                ages = pd.Series({tk: (t - last[int(df.at[tk, "cik"])]).days for tk in bear
                                  if pd.notna(df.at[tk, "cik"]) and int(df.at[tk, "cik"]) in last})
                for lo_, hi_ in AGE_BUCKETS:
                    names = ages.index[(ages >= lo_) & (ages <= hi_)]
                    if len(names) >= 5:
                        age_rec[(lo_, hi_)].append((nd, float(nxt.reindex(names).mean() - um)))
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
        res["attribution"] = factor_attribution(fx, ff5, mom, ff3)
        if rec[k]["bear"]:
            bs = ser(rec[k]["bear"])
            res["bear_attribution"] = factor_attribution(bs, ff5, mom, ff3)
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
    out["insider_diagnostic"] = _summarise_diag(idiag, split)
    out["insider_sell_decay"] = {f"{a}-{b} days": _period_stats(ser(v), split) if v else {"full": {"n": 0}} for (a, b), v in age_rec.items()}
    out["insider_sell_decay_note"] = ("Net opportunistic sellers' next-month return vs the liquid universe, grouped by days since "
                                     "the most recent opportunistic sale filing. Negative = underperformed.")
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


def _insider_diag(df: pd.DataFrame, L: pd.DataFrame, liq: pd.Series, nxt: pd.Series, cfg: dict, nd) -> dict | None:
    """One month of the insider ranking-vs-flags diagnosis: the sector-percentile spread used by the
    stock-level backtest, rebuilt alongside explicit buyer / neutral / seller groups."""
    d2 = df.copy()
    d2["insider_net"] = L["insider_buying"]
    x = signal_scores(d2, cfg)["insiders"]
    li = liq[liq].index
    net, xx, r = L.loc[li, "insider_buying"], x.reindex(li), nxt.reindex(li)
    sec = df.loc[li, "sector"].fillna("Unknown") if "sector" in df else pd.Series("All", index=li)
    ok = net.notna() & xx.notna() & r.notna()
    net, xx, r, sec = net[ok], xx[ok], r[ok], sec[ok]
    if len(r) < 100:
        return None
    um = r.mean()
    top, bot = xx >= 0.7, xx <= 0.3
    buy, sell, zero = net > 0, net < 0, net == 0
    rd = r - r.groupby(sec).transform("mean")
    bs = [r[buy & (sec == g)].mean() - r[sell & (sec == g)].mean() for g in sec.unique()
          if (buy & (sec == g)).any() and (sell & (sec == g)).any()]
    mean = lambda m: float(r[m].mean()) if m.sum() >= 3 else np.nan  # noqa: E731
    return {
        "date": nd,
        "spread": mean(top) - mean(bot), "spread_demeaned": float(rd[top].mean() - rd[bot].mean()) if top.any() and bot.any() else np.nan,
        "buyers": mean(buy) - um, "sellers": mean(sell) - um, "neutral": mean(zero) - um,
        "buyers_minus_sellers": mean(buy) - mean(sell),
        "buyers_minus_sellers_sector_neutral": float(np.mean(bs)) if bs else np.nan,
        "top_neutral_minus_bottom": mean(top & zero) - mean(bot) if (top & zero).sum() >= 5 else np.nan,
        "top_buyers_minus_bottom": mean(top & buy) - mean(bot) if (top & buy).sum() >= 3 else np.nan,
        "top_share_buyers": float(buy[top].mean()), "top_share_neutral": float(zero[top].mean()), "top_share_sellers": float(sell[top].mean()),
        "bot_share_buyers": float(buy[bot].mean()), "bot_share_neutral": float(zero[bot].mean()), "bot_share_sellers": float(sell[bot].mean()),
        "n_top": int(top.sum()), "n_bot": int(bot.sum()), "n_buy": int(buy.sum()), "n_sell": int(sell.sum()), "n_zero": int(zero.sum()),
    }
