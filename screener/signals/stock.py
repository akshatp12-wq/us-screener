"""D. Stock-level signals (raw values). Ranking/scoring happens in screener.scoring."""
from __future__ import annotations

import numpy as np
import pandas as pd

CITE = {
    "value": "Fama & French (1992, 1993): book-to-market and earnings yield",
    "momentum": "Jegadeesh & Titman (1993): 12-month return skipping the latest month",
    "profitability": "Novy-Marx (2013), 'The Other Side of Value': gross profit / assets",
    "investment": "Cooper, Gulen & Schill (2008), 'Asset Growth and the Cross-Section of Stock Returns'",
    "issuance": "Loughran & Ritter (1995), 'The New Issues Puzzle'; Pontiff & Woodgate (2008)",
    "insiders": "Cohen, Malloy & Pomorski (2012), 'Decoding Inside Information': opportunistic trades",
    "short_interest": "Boehmer, Huszar & Jordan (2010); Asquith, Pathak & Ritter (2005)",
    "sue": "Bernard & Thomas (1989), post-earnings-announcement drift (SUE)",
    "lowvol": "Frazzini & Pedersen (2014), 'Betting Against Beta'; Ang et al. (2006)",
}

DEFINITIONS = {
    "value": "Average within-sector percentile of book equity / market cap and TTM net income / market cap",
    "momentum": "Total return from t-12 to t-1 months",
    "profitability": "TTM gross profit / total assets (SEC companyfacts, point-in-time)",
    "investment": "Total asset growth over 1 year (lower ranks higher)",
    "issuance": "1-year log change in split-adjusted shares outstanding (lower ranks higher), "
                "penalty for listings younger than 5 years",
    "insiders": "Net opportunistic insider buys (count of insiders) over 180 days; routine traders "
                "(same calendar month 3 years running) excluded",
    "short_interest": "Short shares / shares outstanding (FINRA, twice monthly); lower ranks higher",
    "sue": "(EPS_q - EPS_q-4) / std of the prior 8 such changes; only within ~95 days of the filing",
    "lowvol": "Average within-sector percentile of 1-year daily volatility and 2-year beta (lower ranks higher)",
}


def _split_factor_per_ticker(splits: pd.DataFrame | None, tickers, starts: pd.Series, today) -> pd.Series:
    out = pd.Series(1.0, index=tickers)
    if splits is None or splits.empty:
        return out
    sp = splits.reindex(columns=tickers)
    sp = sp.loc[sp.index <= pd.Timestamp(today)]
    nontrivial = sp.columns[(sp.fillna(1.0) != 1.0).any()]
    for t in nontrivial:
        st = starts.get(t)
        if st is None or pd.isna(st):
            continue
        col = sp[t]
        out[t] = float(col[col.index > pd.Timestamp(st)].fillna(1.0).prod())
    return out


def momentum_12_1(ret_m: pd.DataFrame, asof: pd.Timestamp, lookback: int = 12, skip: int = 1) -> pd.Series:
    m = ret_m.loc[:asof]
    if len(m) < lookback + 1:
        return pd.Series(np.nan, index=ret_m.columns)
    window = m.iloc[-(lookback):len(m) - skip] if skip else m.iloc[-lookback:]
    valid = window.notna().sum() >= (lookback - skip) - 1
    out = np.expm1(np.log1p(window).sum(min_count=1))
    return out.where(valid)


def vol_beta(ret_d: pd.DataFrame, mkt: pd.Series, asof, vol_win: int = 252, beta_win: int = 504) -> pd.DataFrame:
    r = ret_d.loc[:asof]
    m = mkt.loc[:asof]
    rv = r.iloc[-vol_win:]
    vol = rv.std() * np.sqrt(252)
    vol = vol.where(rv.notna().sum() >= vol_win * 0.8)
    rb = r.iloc[-beta_win:]
    mb = m.reindex(rb.index)
    mc = mb - mb.mean()
    var = (mc ** 2).mean()
    cov = rb.sub(rb.mean()).mul(mc, axis=0).mean()
    beta = (cov / var).where(rb.notna().sum() >= beta_win * 0.6)
    return pd.DataFrame({"vol": vol, "beta": beta})


def first_trade_dates(ret_d: pd.DataFrame) -> pd.Series:
    return ret_d.apply(lambda c: c.first_valid_index())


def classify_insiders(ins: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Cohen-Malloy-Pomorski classification as of `asof` using trades filed before then."""
    x = ins[(ins["filed"] <= asof) & (ins["trans_date"] <= asof)].copy()
    if x.empty:
        return pd.DataFrame(columns=["issuer_cik", "owner_cik", "kind"])
    y = asof.year
    x["year"] = x["trans_date"].dt.year
    x["month"] = x["trans_date"].dt.month
    hist = x[x["year"].between(y - 3, y - 1)]
    if hist.empty:
        return pd.DataFrame(columns=["issuer_cik", "owner_cik", "kind"])
    yrs = hist.groupby(["issuer_cik", "owner_cik"])["year"].nunique()
    eligible = yrs[yrs == 3].index
    months = hist.groupby(["issuer_cik", "owner_cik", "month"])["year"].nunique()
    routine_pairs = set(months[months == 3].reset_index().set_index(["issuer_cik", "owner_cik"]).index)
    kinds = pd.DataFrame(index=eligible).reset_index()
    kinds["kind"] = [("routine" if (a, b) in routine_pairs else "opportunistic") for a, b in eligible]
    return kinds


def insider_scores(ins: pd.DataFrame | None, asof: pd.Timestamp, window_days: int = 180) -> pd.Series:
    """Net opportunistic insiders buying minus selling (per issuer CIK) over the last `window_days`."""
    if ins is None or ins.empty:
        return pd.Series(dtype=float)
    kinds = classify_insiders(ins, asof)
    opp = kinds[kinds["kind"] == "opportunistic"][["issuer_cik", "owner_cik"]]
    recent = ins[(ins["filed"] <= asof) & (ins["trans_date"] > asof - pd.Timedelta(days=window_days))]
    recent = recent.merge(opp, on=["issuer_cik", "owner_cik"], how="inner")
    if recent.empty:
        # issuers with insider data but no opportunistic trades score neutral (0)
        return pd.Series(0.0, index=pd.Index(ins["issuer_cik"].dropna().unique()).astype(int))
    per_owner = recent.groupby(["issuer_cik", "owner_cik"]).apply(
        lambda g: np.sign((g["side"] * g["value"].fillna(1.0)).sum()), include_groups=False)
    net = per_owner.groupby(level=0).sum()
    base = pd.Series(0.0, index=pd.Index(ins["issuer_cik"].dropna().unique()).astype(int))
    base.update(net)
    return base


def compute_stock_signals(univ: pd.DataFrame, fund: pd.DataFrame | None, panels: dict, asof,
                          cfg: dict, insiders: pd.DataFrame | None = None, short: pd.DataFrame | None = None,
                          mkt_symbol: str = "SPY", ret_m: pd.DataFrame | None = None,
                          vb: pd.DataFrame | None = None, first_dates: pd.Series | None = None) -> pd.DataFrame:
    """Raw signal values for every stock in `univ` as of `asof` (no look-ahead)."""
    asof = pd.Timestamp(asof)
    ret_d = panels["ret"]
    tickers = [t for t in univ["ticker"] if t in ret_d.columns]
    df = univ.set_index("ticker").loc[tickers].copy()
    rd = ret_d[tickers].loc[:asof]
    close = panels.get("close")
    vol_ = panels.get("volume")
    if close is not None:
        c = close.reindex(columns=tickers).loc[:asof]
        df["price"] = c.ffill().iloc[-1] if len(c) else np.nan
        if vol_ is not None:
            dv = (c * vol_.reindex(columns=tickers).loc[:asof]).iloc[-63:]
            df["dollar_volume"] = dv.median()
    else:
        df["price"], df["dollar_volume"] = np.nan, np.nan

    # fundamentals (point-in-time snapshot keyed by CIK)
    if fund is not None and not fund.empty and "cik" in df:
        f = fund.set_index("cik")
        for col in ("assets", "assets_1y", "equity", "shares", "shares_1y", "gross_profit_ttm",
                    "net_income_ttm", "sue", "shares_end", "revenue_ttm"):
            df[col] = df["cik"].map(f[col]) if col in f else np.nan
    for col in ("assets", "assets_1y", "equity", "shares", "shares_1y", "gross_profit_ttm", "net_income_ttm",
                "sue", "revenue_ttm"):
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "shares_end" not in df:
        df["shares_end"] = pd.NaT

    # market cap with split handling: mcap = adj_close(asof) * shares_sec * F(shares_end -> today)
    splits = panels.get("splits")
    today = ret_d.index[-1]
    f_now = _split_factor_per_ticker(splits, df.index, df["shares_end"], today)
    f_1y = _split_factor_per_ticker(splits, df.index, pd.to_datetime(df["shares_end"]) - pd.Timedelta(days=365), today)
    df["shares_adj"] = df["shares"] * f_now
    df["mcap"] = df["price"] * df["shares_adj"]
    df["bm"] = df["equity"] / df["mcap"]
    df.loc[df["equity"] <= 0, "bm"] = np.nan
    df["ep"] = df["net_income_ttm"] / df["mcap"]
    df["gpa"] = df["gross_profit_ttm"] / df["assets"]
    df["asset_growth"] = df["assets"] / df["assets_1y"] - 1
    ratio = (df["shares"] * f_now) / (df["shares_1y"] * f_1y)
    df["net_issuance"] = np.log(ratio.where(ratio > 0))
    df.loc[df["net_issuance"].abs() > 1.0, "net_issuance"] = np.nan  # likely unhandled split/merger

    # listing age from first price
    fd = first_dates if first_dates is not None else first_trade_dates(ret_d[tickers])
    start = pd.Timestamp(cfg["data"]["price_history_start"])
    yrs = cfg["universe"]["young_listing_years"]
    first = pd.to_datetime(fd.reindex(df.index))
    df["first_trade"] = first
    df["young"] = (first > start + pd.Timedelta(days=45)) & (first > asof - pd.DateOffset(years=yrs))

    # prices: momentum, volatility, beta
    rm = ret_m if ret_m is not None else (1 + rd.fillna(0)).resample("ME").prod().sub(1).where(
        rd.notna().resample("ME").sum() > 0)
    mc = cfg["signals"]["momentum"]
    df["momentum"] = momentum_12_1(rm.reindex(columns=df.index), asof, mc["lookback_months"], mc["skip_months"])
    if vb is None:
        mkt = ret_d[mkt_symbol] if mkt_symbol in ret_d else rd.mean(axis=1)
        lv = cfg["signals"]["lowvol"]
        vb = vol_beta(rd, mkt, asof, lv["window_days"], lv["beta_window_days"])
    df["vol"] = vb["vol"].reindex(df.index)
    df["beta"] = vb["beta"].reindex(df.index)

    # insiders (Cohen-Malloy-Pomorski)
    if insiders is not None and not insiders.empty and "cik" in df:
        sc = insider_scores(insiders, asof, cfg["signals"]["insiders"]["score_window_days"])
        df["insider_net"] = df["cik"].map(sc)
    else:
        df["insider_net"] = np.nan

    # short interest (FINRA)
    if short is not None and not short.empty:
        s = short.set_index("ticker")
        df["short_shares"] = s["short_shares"].reindex(df.index)
        df["days_to_cover"] = s["days_to_cover"].reindex(df.index) if "days_to_cover" in s else np.nan
        df["si_ratio"] = df["short_shares"] / df["shares_adj"]
        df.loc[(df["si_ratio"] > 1.5) | (df["si_ratio"] < 0), "si_ratio"] = np.nan
    else:
        df["si_ratio"], df["days_to_cover"] = np.nan, np.nan
    return df
