"""Lead indicators: stock-level signals meant to surface names worth researching, each with its own
backtest (screener/backtest/leads_bt.py). None of them feeds the mode scores.

  mispricing         fundamental value vs market value, a scaled version of Bartram & Grinblatt (2018)
  resid_mom          residual momentum: 12-1 month return net of market/size/value exposure (Blitz,
                     Huij & Martens 2011)
  insider_buying     more opportunistic insiders buying than selling over 180 days, routine traders
                     excluded (Cohen, Malloy & Pomorski 2012)
  earnings_reaction  market-adjusted return over the 3 trading days around the latest earnings release
                     (Chan, Jegadeesh & Lakonishok 1996)
  abnormal_volume    last week's trading volume vs the prior 50 days (Gervais, Kaniel & Mingelgrin 2001)
  etf_resid_mom      sector and industry ETFs ranked by residual momentum (separate list; evidence from
                     Ken French's 49 industries since 1963)

All functions use only information available at `asof`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LEADS = {
    "mispricing": {
        "label": "Fundamental mispricing",
        "short": "Fair value from fundamentals vs market value",
        "unit": "fair value vs price",
        "cite": "Bartram & Grinblatt (2018), Agnostic Fundamental Analysis Works, Journal of Financial Economics",
        "bearish": True,
    },
    "resid_mom": {
        "label": "Residual momentum",
        "short": "12-1 month return after removing market, size and value exposure",
        "unit": "residual z",
        "cite": "Blitz, Huij & Martens (2011), Residual Momentum, Journal of Empirical Finance",
        "bearish": False,
    },
    "insider_buying": {
        "label": "Opportunistic insider buying",
        "short": "More opportunistic insiders buying than selling over 6 months (routine traders excluded); "
                 "the bearish side is net opportunistic selling",
        "unit": "net insiders",
        "cite": "Cohen, Malloy & Pomorski (2012), Decoding Inside Information, Journal of Finance",
        "bearish": True,
    },
    "earnings_reaction": {
        "label": "Earnings reaction",
        "short": "Market-adjusted 3-day return around the latest earnings release",
        "unit": "3-day excess return",
        "cite": "Chan, Jegadeesh & Lakonishok (1996), Momentum Strategies, Journal of Finance",
        "bearish": True,
    },
    "abnormal_volume": {
        "label": "Abnormal volume",
        "short": "Last week's trading volume vs the prior 50 days",
        "unit": "volume vs normal",
        "cite": "Gervais, Kaniel & Mingelgrin (2001), The High-Volume Return Premium, Journal of Finance",
        "bearish": False,
    },
}

ETF_LEAD = {
    "label": "Sector ETF residual momentum",
    "short": "Sector and industry ETFs whose past-year return, net of market, size and value, ranks highest",
    "unit": "residual z",
    "cite": "Blitz, Huij & Martens (2011), Residual Momentum, Journal of Empirical Finance; Moskowitz & Grinblatt (1999)",
}


# ----------------------------------------------------------------------------- 1. fundamental mispricing
def _winsor(s: pd.Series, p: float) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().sum() < 10:
        return s
    lo, hi = s.quantile(p), s.quantile(1 - p)
    return s.clip(lo, hi)


def mispricing(df: pd.DataFrame, winsor: float = 0.01, min_names: int = 100) -> pd.Series:
    """Fair-value gap: fitted / actual - 1, from a cross-sectional regression of market value on
    accounting items with sector effects.

    Bartram & Grinblatt regress market value on accounting levels each month. Here every item is
    divided by total assets before the regression (market-to-assets on items-to-assets), which keeps
    the largest companies from dominating the fit. Positive = fundamentals imply a higher value than
    the market price (cheap); negative = expensive.
    """
    need = ["mcap", "assets", "equity", "net_income_ttm"]
    if any(c not in df for c in need):
        return pd.Series(np.nan, index=df.index)
    a = pd.to_numeric(df["assets"], errors="coerce")
    ok = (a > 0) & df["mcap"].gt(0) & df["equity"].notna() & df["net_income_ttm"].notna()
    d = df.loc[ok]
    if len(d) < min_names:
        return pd.Series(np.nan, index=df.index)
    A = a[ok]
    ni = d["net_income_ttm"].astype(float)
    X = pd.DataFrame({
        "equity": d["equity"].astype(float) / A,
        "liabilities": (A - d["equity"].astype(float)) / A,
        "revenue": d.get("revenue_ttm", pd.Series(np.nan, index=d.index)).astype(float).fillna(0) / A,
        "gross_profit": d.get("gross_profit_ttm", pd.Series(np.nan, index=d.index)).astype(float).fillna(0) / A,
        "operating_income": d.get("operating_income_ttm", pd.Series(np.nan, index=d.index)).astype(float).fillna(0) / A,
        "ni_pos": ni.clip(lower=0) / A,
        "ni_neg": ni.clip(upper=0) / A,
        "gp_missing": d.get("gross_profit_ttm", pd.Series(np.nan, index=d.index)).isna().astype(float),
        "oi_missing": d.get("operating_income_ttm", pd.Series(np.nan, index=d.index)).isna().astype(float),
    })
    X = X.apply(lambda c: _winsor(c, winsor) if c.name not in ("gp_missing", "oi_missing") else c)
    y = _winsor(d["mcap"].astype(float) / A, winsor)
    sect = pd.get_dummies(d.get("sector", pd.Series("Unknown", index=d.index)).fillna("Unknown"), drop_first=False, dtype=float)
    M = pd.concat([X, sect], axis=1).replace([np.inf, -np.inf], np.nan)
    good = M.notna().all(axis=1) & y.notna()
    if good.sum() < min_names:
        return pd.Series(np.nan, index=df.index)
    Mg, yg = M[good].to_numpy(float), y[good].to_numpy(float)
    beta, *_ = np.linalg.lstsq(Mg, yg, rcond=None)
    # one robust refit: drop the 2% largest relative misses, which are usually data errors
    fit = Mg @ beta
    rel = np.abs(np.log(np.clip(fit, 1e-6, None) / np.clip(yg, 1e-6, None)))
    keep = rel <= np.nanquantile(rel, 0.98)
    beta, *_ = np.linalg.lstsq(Mg[keep], yg[keep], rcond=None)
    fitted = pd.Series(M[good].to_numpy(float) @ beta, index=M[good].index)
    actual = d["mcap"].astype(float)[good] / A[good]
    gap = fitted.clip(lower=0.01) / actual - 1
    return gap.reindex(df.index)


# ----------------------------------------------------------------------------- 2. residual momentum
def residual_momentum(ret_m: pd.DataFrame, factors: pd.DataFrame | None, asof, window: int = 36,
                      formation: int = 12, skip: int = 1) -> pd.Series:
    """Blitz-Huij-Martens residual momentum at `asof`.

    Regress each stock's monthly excess return on the factors over the last `window` months, then
    score = sum of residuals over months t-formation..t-skip-1 divided by their standard deviation.
    `factors`: monthly columns among mkt, smb, hml, rf (decimal). Stocks need a complete window.
    """
    asof = pd.Timestamp(asof)
    r = ret_m.loc[:asof]
    if factors is None or factors.empty:
        return pd.Series(np.nan, index=ret_m.columns)
    f = factors.loc[:asof].dropna(how="any")
    idx = r.index.intersection(f.index)
    if len(idx) < window:
        return pd.Series(np.nan, index=ret_m.columns)
    idx = idx[-window:]
    Y = r.loc[idx]
    rf = f.loc[idx, "rf"] if "rf" in f else 0.0
    Y = Y.sub(rf, axis=0)
    cols = [c for c in ("mkt", "smb", "hml") if c in f]
    X = np.column_stack([np.ones(len(idx))] + [f.loc[idx, c].to_numpy(float) for c in cols])
    complete = Y.notna().all()
    out = pd.Series(np.nan, index=ret_m.columns)
    if complete.sum() == 0:
        return out
    Yc = Y.loc[:, complete].to_numpy(float)
    B, *_ = np.linalg.lstsq(X, Yc, rcond=None)
    E = Yc - X @ B
    form = E[-formation:len(E) - skip] if skip else E[-formation:]
    sd = form.std(axis=0, ddof=1)
    score = np.where(sd > 0, form.sum(axis=0) / sd, np.nan)
    out[Y.columns[complete]] = score
    return out


def ff3_monthly(ff3: pd.DataFrame | None) -> pd.DataFrame | None:
    """Ken French monthly factors (decimal) -> columns mkt, smb, hml, rf."""
    if ff3 is None or ff3.empty:
        return None
    out = pd.DataFrame(index=ff3.index)
    for src, dst in (("Mkt-RF", "mkt"), ("SMB", "smb"), ("HML", "hml"), ("RF", "rf")):
        if src in ff3:
            out[dst] = ff3[src]
    out.index = out.index + pd.offsets.MonthEnd(0)
    return out


# ----------------------------------------------------------------------------- 3. opportunistic insider buying
# Uses insider_scores() from screener/signals/stock.py (Cohen-Malloy-Pomorski classification).


# ----------------------------------------------------------------------------- 4. earnings reaction
def earnings_reactions(ret_d: pd.DataFrame, mkt: pd.Series, releases: dict, cik_by_ticker: dict,
                       half_window: int = 1) -> pd.DataFrame:
    """Every earnings release's market-adjusted return over trading days [day0 - h, day0 + h], where
    day0 is the first trading day on or after the 8-K filing date. Columns: ticker, release,
    known (date the full window has closed), ear."""
    rows = []
    dates = ret_d.index
    lr = np.log1p(ret_d.fillna(0.0))
    lm = np.log1p(mkt.reindex(dates).fillna(0.0))
    ex = lr.sub(lm, axis=0)
    cum = ex.cumsum()
    seen = ret_d.notna()
    for t, cik in cik_by_ticker.items():
        if t not in ret_d.columns or cik is None or pd.isna(cik):
            continue
        rel = releases.get(int(cik))
        if rel is None or len(rel) == 0:
            continue
        rel = pd.DatetimeIndex(rel)
        rel = rel[(rel >= dates[0]) & (rel <= dates[-1])]
        if len(rel) == 0:
            continue
        pos = dates.searchsorted(rel, side="left")
        c = cum[t].to_numpy()
        s = seen[t].to_numpy()
        for d, p in zip(rel, pos):
            a, b = p - half_window - 1, p + half_window
            if a < 0 or b >= len(dates) or not s[p - half_window:b + 1].all():
                continue
            rows.append((t, d, dates[b], float(np.expm1(c[b] - c[a]))))
    return pd.DataFrame(rows, columns=["ticker", "release", "known", "ear"])


def latest_reaction(table: pd.DataFrame, asof, max_age_days: int = 75) -> pd.DataFrame:
    """Most recent reaction whose window has closed by `asof` and whose release is within `max_age_days`."""
    if table is None or table.empty:
        return pd.DataFrame(columns=["ear", "release", "known"])
    asof = pd.Timestamp(asof)
    x = table[(table["known"] <= asof) & (table["release"] > asof - pd.Timedelta(days=max_age_days))]
    if x.empty:
        return pd.DataFrame(columns=["ear", "release", "known"])
    x = x.sort_values("release").groupby("ticker").tail(1).set_index("ticker")
    return x[["ear", "release", "known"]]


# ----------------------------------------------------------------------------- 5. abnormal volume
def abnormal_volume(volume: pd.DataFrame | None, asof, recent: int = 5, base: int = 50) -> pd.Series:
    """log(mean volume over the last `recent` trading days / mean over the `base` days before them)."""
    if volume is None or volume.empty:
        return pd.Series(dtype=float)
    v = volume.loc[:pd.Timestamp(asof)].replace(0, np.nan)
    if len(v) < recent + base:
        return pd.Series(np.nan, index=volume.columns)
    r = v.iloc[-recent:]
    b = v.iloc[-(recent + base):-recent]
    ok = (r.notna().sum() >= recent - 1) & (b.notna().sum() >= base * 0.8)
    out = np.log(r.mean() / b.mean())
    return out.where(ok)


def recent_return(ret_d: pd.DataFrame, asof, days: int = 5) -> pd.Series:
    r = ret_d.loc[:pd.Timestamp(asof)].iloc[-days:]
    return np.expm1(np.log1p(r.fillna(0)).sum()).where(r.notna().sum() >= days - 1)


# ----------------------------------------------------------------------------- all leads at one date
def compute_leads(df: pd.DataFrame, panels: dict, asof, cfg: dict, ret_m: pd.DataFrame | None = None,
                  factors: pd.DataFrame | None = None, insiders: pd.DataFrame | None = None,
                  reactions: pd.DataFrame | None = None, max_age_days: int | None = None) -> pd.DataFrame:
    """Raw lead values for the stocks in `df` (output of compute_stock_signals) as of `asof`."""
    lc = cfg["leads"]
    asof = pd.Timestamp(asof)
    ret_d = panels["ret"]
    out = pd.DataFrame(index=df.index)
    out["mispricing"] = mispricing(df, lc["mispricing"]["winsor"], lc["mispricing"]["min_names"])
    if ret_m is None:
        rd = ret_d.loc[:asof, [c for c in df.index if c in ret_d.columns]]
        ret_m = (1 + rd.fillna(0)).resample("ME").prod().sub(1).where(rd.notna().resample("ME").sum() > 0)
    rmc = lc["residual_momentum"]
    rm = residual_momentum(ret_m.reindex(columns=df.index), factors, asof, rmc["window_months"],
                           rmc["formation_months"], rmc["skip_months"])
    out["resid_mom"] = rm.reindex(df.index)
    if insiders is not None and not insiders.empty and "cik" in df:
        from .stock import insider_scores
        sc = insider_scores(insiders, asof, lc["insider_buying"]["window_days"])
        out["insider_buying"] = df["cik"].map(sc).astype(float) if len(sc) else np.nan
    else:
        out["insider_buying"] = np.nan
    er = latest_reaction(reactions, asof, max_age_days or lc["earnings_reaction"]["max_age_days"])
    out["earnings_reaction"] = er["ear"].reindex(df.index) if not er.empty else np.nan
    out["release"] = er["release"].reindex(df.index) if not er.empty else pd.NaT
    out["known"] = er["known"].reindex(df.index) if not er.empty else pd.NaT
    av = lc["abnormal_volume"]
    out["abnormal_volume"] = abnormal_volume(panels.get("volume"), asof, av["recent_days"], av["base_days"]).reindex(df.index)
    out["ret_5d"] = recent_return(ret_d.reindex(columns=df.index), asof, av["recent_days"]).reindex(df.index)
    return out


def flags(leads: pd.DataFrame, liquid: pd.Series, cfg: dict) -> dict[str, dict[str, pd.Index]]:
    """Bullish (and bearish, where the evidence is two-sided) flagged names per lead."""
    lc = cfg["leads"]
    share = lc["flag_share"]
    res = {}
    liq = liquid.reindex(leads.index).fillna(False).astype(bool)
    for k in LEADS:
        if k not in leads:
            continue
        s = leads.loc[liq, k].dropna()
        if k == "insider_buying":
            mn = lc["insider_buying"]["min_net"]
            res[k] = {"bull": s.index[s >= mn], "bear": s.index[s <= -mn]}
            continue
        if len(s) < 20 or s.nunique() < 5:
            res[k] = {"bull": pd.Index([]), "bear": pd.Index([])}
            continue
        hi, lo = s.quantile(1 - share), s.quantile(share)
        res[k] = {"bull": s.index[s >= hi], "bear": s.index[s <= lo] if LEADS[k]["bearish"] else pd.Index([])}
    return res


# ----------------------------------------------------------------------------- sector ETF residual momentum
def etf_universe(cfg: dict) -> list[str]:
    kinds = set(cfg["leads"]["etf"]["kinds"])
    return [t for t, m in cfg["etfs"]["list"].items() if m.get("kind") in kinds]


def etf_monthly(ret_d: pd.DataFrame, tickers: list[str], asof=None) -> pd.DataFrame:
    cols = [t for t in tickers if t in ret_d.columns]
    r = ret_d.loc[:pd.Timestamp(asof) if asof is not None else None, cols]
    m = (1 + r.fillna(0)).resample("ME").prod() - 1
    return m.where(r.notna().resample("ME").sum() >= 15)


# ----------------------------------------------------------------------------- when did each flag start?
def insider_signal_dates(ins: pd.DataFrame | None, asof, ciks, window_days: int = 180, min_net: int = 1,
                         direction: int = 1) -> dict:
    """Per issuer CIK: the filing date on which net opportunistic insider buying (within the window) last
    rose to `min_net` or more, i.e. the start of the current insider signal. direction=-1 does the same
    for net selling (net count at or below -min_net)."""
    from .stock import classify_insiders
    if ins is None or ins.empty:
        return {}
    asof = pd.Timestamp(asof)
    want = set(int(c) for c in ciks if pd.notna(c))
    x = ins[ins["issuer_cik"].isin(want)]
    kinds = classify_insiders(x, asof)
    opp = kinds[kinds["kind"] == "opportunistic"][["issuer_cik", "owner_cik"]]
    rec = x[(x["filed"] <= asof) & (x["trans_date"] > asof - pd.Timedelta(days=window_days))]
    rec = rec.merge(opp, on=["issuer_cik", "owner_cik"], how="inner")
    out = {}
    for cik, g in rec.groupby("issuer_cik"):
        cum: dict = {}
        start = None
        for d, gd in g.sort_values("filed").groupby("filed"):
            for o, sd, v in zip(gd["owner_cik"], gd["side"], gd["value"]):
                cum[o] = cum.get(o, 0.0) + sd * (v if pd.notna(v) else 1.0)
            net = sum(1 if v > 0 else -1 if v < 0 else 0 for v in cum.values())
            if net * direction >= min_net:
                start = start or d
            else:
                start = None
        if start is not None:
            out[int(cik)] = pd.Timestamp(start)
    return out


def volume_streak_start(volume: pd.DataFrame | None, tickers, cutoff: float, asof, recent: int = 5,
                        base: int = 50, lookback: int = 30) -> dict:
    """First trading day of the current run in which abnormal volume stayed at or above `cutoff`."""
    if volume is None or volume.empty or not len(tickers):
        return {}
    cols = [t for t in tickers if t in volume.columns]
    v = volume.loc[:pd.Timestamp(asof), cols].replace(0, np.nan).iloc[-(lookback + recent + base):]
    abn = np.log(v.rolling(recent, min_periods=recent - 1).mean() / v.shift(recent).rolling(base, min_periods=int(base * 0.8)).mean())
    abn = abn.iloc[-lookback:]
    out = {}
    for t in cols:
        a = abn[t]
        on = (a >= cutoff - 1e-12).to_numpy()
        if not len(on) or not on[-1]:
            continue
        i = len(on) - 1
        while i > 0 and on[i - 1]:
            i -= 1
        out[t] = a.index[i]
    return out


def month_end_streak(history: dict, key: str, ticker: str, month_ends: list) -> pd.Timestamp | None:
    """Earliest month end in the unbroken run (counting back from the latest) where `ticker` was in
    history[month_end][key]."""
    since = None
    for m in sorted(month_ends, reverse=True):
        if ticker in set(history.get(m, {}).get(key, [])):
            since = pd.Timestamp(m)
        else:
            break
    return since
