"""Live (intraday / nightly) pipeline: regime gauge, stock and ETF scoring, portfolios, scenarios."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import card, load_macro, spliced_stock_price, stage
from screener.data import macro_sources as ms
from screener.data import market_sources as mk
from screener.data import prices as px
from screener.data import sec
from screener.data.universe import load_universe, sec_cik_map
from screener.regime.label import regime_label
from screener.signals import bubble as bub
from screener.signals import macro, sentiment
from screener.signals import stock as stk
from screener.util import clean, utcnow, write_json

log = logging.getLogger("pipeline")

# ============================================================================= market-level
@stage("regime")
def build_regime(cfg, f, panels, macro_d, sh, ebp_df, bw, sector_issuance=None) -> dict:
    ret = panels.get("ret") if panels else None
    spy = ret["SPY"] if ret is not None and "SPY" in ret else None
    vix = None
    if ret is not None and "^VIX" in panels.get("close", pd.DataFrame()):
        vix = panels["close"]["^VIX"].dropna()
    if (vix is None or vix.empty) and macro_d.get("VIXCLS") is not None:
        vix = macro_d["VIXCLS"]
    elif macro_d.get("VIXCLS") is not None:
        vix = vix.combine_first(macro_d["VIXCLS"])
    fr_mkt = None
    if sh is None or sh.empty or sh["price"].dropna().empty:
        t = ms.safe(ms.french, f, cfg, cfg["backtest"]["french_files"]["ff3"], source="French:" + cfg["backtest"]["french_files"]["ff3"])
        if t:
            base = ms.french_table(t, "", "monthly")
            fr_mkt = base["Mkt-RF"] + base["RF"]
    stock_price = spliced_stock_price(sh, spy, fr_mkt)

    d = {}
    cg = macro.credit_gap(macro_d.get(cfg["macro"]["credit_gap"]["series"]), cfg)
    d["credit_gap"] = card("credit_gap", "Credit-to-GDP gap", cg, macro.CITE["credit_gap"],
                           "BIS via FRED QUSPAM770A (% of GDP)", "quarterly",
                           "Total private credit/GDP minus one-sided HP trend (lambda 400,000)")
    rzb = macro.red_zone(macro_d.get("QUSNAM770A"), stock_price, cfg, "business")
    d["red_zone_business"] = card("red_zone_business", "Red zone: business", rzb, macro.CITE["red_zones"],
                                  "BIS via FRED QUSNAM770A; S&P composite (Shiller, else Ken French market) + SPY", "quarterly",
                                  "3y change in business credit/GDP with 3y stock price growth, vs history")
    rzh = macro.red_zone(macro_d.get("QUSHAM770A"), macro_d.get("CSUSHPINSA"), cfg, "household")
    d["red_zone_household"] = card("red_zone_household", "Red zone: household", rzh, macro.CITE["red_zones"],
                                   "BIS via FRED QUSHAM770A; Case-Shiller CSUSHPINSA", "quarterly",
                                   "3y change in household credit/GDP with 3y home price growth, vs history")
    yc = macro.yield_curve(macro_d.get("T10Y3M"), cfg)
    d["yield_curve"] = card("yield_curve", "Recession odds (yield curve)", yc, macro.CITE["yield_curve"],
                            "FRED T10Y3M", "daily", "Phi(a + b x quarterly-average 10y-3m spread), "
                            "probit fitted to Estrella-Mishkin's published table")
    eb = macro.excess_bond_premium(ebp_df, macro_d.get("BAMLH0A0HYM2"), cfg)
    d["ebp"] = card("ebp", "Excess bond premium", eb, macro.CITE["ebp"],
                    eb.get("source_used", "Federal Reserve EBP CSV"), "monthly",
                    "Corporate bond spread not explained by default risk (Gilchrist-Zakrajsek)")
    spot = None
    if sh is not None and spy is not None and stock_price is not None:
        last_sh = sh["price"].dropna()
        if len(last_sh):
            spot = float(stock_price.iloc[-1] / last_sh.iloc[-1])
    cp = macro.cape_dial(sh, cfg, spot)
    d["cape"] = card("cape", "Valuation (CAPE)", cp, macro.CITE["cape"], "Shiller ie_data.xls (+ SPY since)",
                     "monthly", "Price / 10-year average real earnings; regression of 10y real returns on log CAPE")
    se = sentiment.sentiment_composite(vix, ret, cfg, bw)
    d["sentiment"] = card("sentiment", "Sentiment composite", se, sentiment.CITE["sentiment"],
                          "VIX (Cboe via Yahoo/FRED), SPHB vs SPLV, IPO ETF vs SPY; Baker-Wurgler (lagged)",
                          "daily", "Mean of rolling 5y z-scores: -VIX, 6m high-beta minus low-vol, 6m IPO ETF minus SPY")
    vr = sentiment.variance_risk_premium(vix, spy, cfg)
    d["vrp"] = card("vrp", "Variance risk premium", vr, sentiment.CITE["vrp"], "VIX and SPY daily returns",
                    "daily", "VIX^2/12 minus 21-day realised variance (%^2), percentile vs history")
    bw_res = bub.bubble_watch(ret, cfg, "SPY", sector_issuance) if ret is not None else {"rows": []}
    cgd = bub.crash_guard(spy, cfg) if spy is not None else {"status": "unavailable", "on": False}
    d["crash_guard"] = card("crash_guard", "Momentum crash guard", cgd, bub.CITE["crash_guard"], "SPY daily",
                            "daily", "Guard on when market 24m return < 0 and 126d volatility is high")
    rf = None
    if macro_d.get("DTB3") is not None and len(macro_d["DTB3"]):
        rf = float(macro_d["DTB3"].dropna().iloc[-1]) / 100
    tr = bub.trend_signals(ret, cfg, rf or 0.0) if ret is not None else {}

    from screener.signals.buywatch import CITE as BW_CITE, buy_watch
    bwt = buy_watch(spy, cfg, vix, se.get("value"))
    d["buy_watch"] = card("buy_watch", "Buy watch", bwt, BW_CITE, "SPY daily; VIX; sentiment composite", "daily",
                          bwt.get("rule", ""))
    # v2: these dials are shown for context but do not feed the regime label or position sizing
    for k in ("yield_curve", "ebp", "vrp"):
        if isinstance(d.get(k), dict):
            d[k]["info_only"] = True
    reg = regime_label(d["credit_gap"].get("status", "unavailable"),
                       [d["red_zone_business"].get("status"), d["red_zone_household"].get("status")],
                       se.get("label"), bw_res.get("flagged_share"), cfg)
    return {"dials": d, "regime": reg, "bubble": bw_res, "crash": cgd, "trend": tr, "rf": rf}


# ============================================================================= stock-level
@stage("fundamentals")
def build_fundamentals(cfg, f, cache, univ):
    if univ is None or "cik" not in univ:
        return None, None
    store = sec.load_fundamentals(f, cfg, cache, univ["cik"].dropna().astype(int).tolist())
    if store is None or store.empty:
        return None, None
    sue_cfg = cfg["signals"]["sue"]
    releases = None
    if sue_cfg.get("release_dates"):
        releases = ms.safe(sec.load_earnings_releases, f, cfg, cache, univ["cik"].dropna().astype(int).tolist(),
                           source="SEC earnings releases (8-K 2.02)")
    derived = sec.derive_all(store, sue_cfg["min_quarters"], releases, cfg, sue_cfg.get("release_max_days", 100))
    snap = sec.snapshot(derived, pd.Timestamp.today().normalize())
    cov = {c: int(snap[c].notna().sum()) for c in ("assets", "equity", "shares", "gross_profit_ttm", "net_income_ttm", "sue")} \
        if len(snap) else {}
    matched = int(univ["cik"].dropna().astype(int).isin(set(snap["cik"])).sum()) if len(snap) else 0
    log.info("fundamentals snapshot: %d companies, %d universe matches, non-null counts %s", len(snap), matched, cov)
    from screener.data.cache import record
    record("SEC point-in-time", snapshot_companies=int(len(snap)), universe_matches=matched, non_null=cov)
    try:
        from screener.config import out_dir
        sample = {t: int(c) for t, c in zip(univ["ticker"], univ["cik"]) if t in
                  ("XOM", "CAT", "ACN", "F", "CMCSA", "META", "BRK-B", "AAPL", "GOOGL", "BKNG") and pd.notna(c)}
        write_json(out_dir(cfg) / "diagnostics.json", {"generated": utcnow(), "sec": sec.diagnose(store, sample)})
    except Exception as e:  # noqa: BLE001
        log.warning("diagnostics failed: %s", e)
    return derived, snap


# ============================================================================= lead indicators
@stage("lead indicators")
def build_leads(cfg, f, cache, out, df, panels, insiders, univ=None, derived=None):
    """Today's flagged names for each lead indicator -> leads.json (evidence from leads_backtest.json)."""
    from screener.portfolio.construct import liquid_mask
    from screener.signals import leads as ld
    ret_d = panels["ret"]
    asof = ret_d.index[-1]
    lc = cfg["leads"]
    factors = None
    t = ms.safe(ms.french, f, cfg, cfg["backtest"]["french_files"]["ff3"], source="French:" + cfg["backtest"]["french_files"]["ff3"])
    if t:
        factors = ld.ff3_monthly(ms.french_table(t, "", "monthly"))
    if factors is None and "SPY" in ret_d:   # fallback: market model on SPY
        spy_m = (1 + ret_d["SPY"].fillna(0)).resample("ME").prod() - 1
        factors = pd.DataFrame({"mkt": spy_m.iloc[:-1]})
    ciks = df["cik"].dropna().astype(int).tolist() if "cik" in df else []
    rel = ms.safe(sec.load_earnings_releases, f, cfg, cache, ciks, source="SEC earnings releases (8-K 2.02)") or {}
    tick = [x for x in df.index if x in ret_d.columns]
    recent = ret_d.iloc[-300:]
    mkt = recent["SPY"] if "SPY" in recent else recent[tick].mean(axis=1)
    reactions = ld.earnings_reactions(recent[tick], mkt, rel, df["cik"].to_dict() if "cik" in df else {})
    L = ld.compute_leads(df, panels, asof, cfg, factors=factors, insiders=insiders, reactions=reactions,
                         max_age_days=lc["earnings_reaction"]["live_max_age_days"])
    liq = liquid_mask(df, cfg).fillna(False)
    F = ld.flags(L, liq, cfg)
    ev = (_read(out, "leads_backtest.json") or {}).get("leads", {})
    n = int(lc["max_list"])
    since = lead_since(cfg, cache, L, F, df, panels, insiders, factors, liq, univ, derived, asof)
    state, live = lead_state(out, F, since, panels, asof)

    def row(tk, k):
        v = L.at[tk, k]
        col = L.loc[liq[liq].index, k].dropna()
        r = {"t": tk, "n": df.at[tk, "name"] if "name" in df else "", "s": df.at[tk, "sector"] if "sector" in df else "",
             "v": None if pd.isna(v) else float(v), "p": float((col <= v).mean()) if len(col) and pd.notna(v) else None,
             "px": None if pd.isna(df.at[tk, "price"]) else float(df.at[tk, "price"]),
             "mc": None if pd.isna(df.at[tk, "mcap"]) else float(df.at[tk, "mcap"])}
        if k == "earnings_reaction" and pd.notna(L.at[tk, "release"]):
            r["release"] = pd.Timestamp(L.at[tk, "release"]).strftime("%Y-%m-%d")
        if k == "abnormal_volume":
            r["ret_5d"] = None if pd.isna(L.at[tk, "ret_5d"]) else float(L.at[tk, "ret_5d"])
        return r

    def with_since(r, k, side):
        e = state["active"].get(f"{k}|{side}|{r['t']}")
        if e:
            r.update({"since": e["since"], "px_since": e.get("px"), "ret_since": e.get("ret"), "spy_since": e.get("spy"),
                      "backfilled": e["since"] < state["started"], "capped": bool(e.get("capped"))})
        return r

    payload = {"generated": utcnow(), "asof": asof, "universe": int(liq.sum()), "leads": {}, "convergence": [],
               "convergence_rule": "Counts only indicators whose flagged names beat the universe before costs over the full backtest."}
    hits: dict[str, list[str]] = {}
    for k, meta in ld.LEADS.items():
        fl = F.get(k, {"bull": pd.Index([]), "bear": pd.Index([])})
        bull = L.loc[fl["bull"], k].sort_values(ascending=False)
        bear = L.loc[fl["bear"], k].sort_values() if len(fl["bear"]) else pd.Series(dtype=float)
        e = ev.get(k, {})
        full = ((e.get("flagged_minus_universe") or {}).get("full") or {})
        counts = not (isinstance(full.get("ann_mean"), (int, float)) and full["ann_mean"] <= 0)
        if counts:
            for tk in bull.index:
                hits.setdefault(tk, []).append(k)
        payload["leads"][k] = {
            **meta, "counts": counts, "status": e.get("status", "Not yet tested"), "reason": e.get("reason", "The weekly backtest has not run yet."),
            "bear_status": (e.get("bear_status") or {}).get("status"), "bear_reason": (e.get("bear_status") or {}).get("reason"),
            "coverage": float(L.loc[liq[liq].index, k].notna().mean()) if k in L else 0.0,
            "n_bull": int(len(bull)), "n_bear": int(len(bear)),
            "bull": [with_since(row(tk, k), k, "bull") for tk in bull.index[:n]],
            "bear": [with_since(row(tk, k), k, "bear") for tk in bear.index[:n]],
            "live": live.get(k),
        }
    conv = sorted(((tk, ks) for tk, ks in hits.items() if len(ks) >= 2), key=lambda x: -len(x[1]))
    payload["convergence"] = [{"t": tk, "n": df.at[tk, "name"] if "name" in df else "", "s": df.at[tk, "sector"] if "sector" in df else "",
                               "leads": ks, "since": {k: (state["active"].get(f"{k}|bull|{tk}") or {}).get("since") for k in ks},
                               "px": None if pd.isna(df.at[tk, "price"]) else float(df.at[tk, "price"])} for tk, ks in conv[:40]]
    payload["tracking_started"] = state["started"]
    # sector / industry ETFs ranked by residual momentum
    eb = (_read(out, "leads_backtest.json") or {}).get("etf_lead", {})
    etfs = ld.etf_universe(cfg)
    em = ld.etf_monthly(ret_d, etfs, asof)
    erm = ld.residual_momentum(em, factors, asof, lc["residual_momentum"]["window_months"],
                               lc["residual_momentum"]["formation_months"], lc["residual_momentum"]["skip_months"]).dropna()
    r12 = (1 + em.iloc[-13:-1]).prod() - 1 if len(em) > 13 else pd.Series(dtype=float)
    ranked = erm.sort_values(ascending=False)
    top = int(lc["etf"]["top"])
    payload["etf_lead"] = {
        **ld.ETF_LEAD, "status": eb.get("status", "Not yet tested"), "reason": eb.get("reason", "The weekly backtest has not run yet."),
        "eligible": int(len(ranked)), "top": top,
        "rows": [{"t": tk, "n": cfg["etfs"]["list"][tk]["name"], "kind": cfg["etfs"]["list"][tk]["kind"], "v": float(v),
                  "rank": i + 1, "ret_12m": None if tk not in r12 or pd.isna(r12[tk]) else float(r12[tk]), "flag": i < top}
                 for i, (tk, v) in enumerate(ranked.items())],
    }
    write_json(out / "leads.json", payload)
    return payload


def _px_on(close: pd.DataFrame | None, t: str, d) -> float | None:
    if close is None or t not in close:
        return None
    s = close[t].loc[:pd.Timestamp(d)].dropna()
    return float(s.iloc[-1]) if len(s) else None


def _ret_between(ret: pd.DataFrame, t: str, a, b) -> float | None:
    if t not in ret:
        return None
    r = ret[t].loc[(ret.index > pd.Timestamp(a)) & (ret.index <= pd.Timestamp(b))]
    return float((1 + r.fillna(0)).prod() - 1) if len(r) else 0.0


def lead_since(cfg, cache, L, F, df, panels, insiders, factors, liq, univ, derived, asof) -> dict:
    """{(lead, side, ticker): date the current flag started}. Event leads use the event date; slow leads
    (mispricing, residual momentum) use month-end history, cached in .cache/leads_monthly.pkl."""
    from screener.data.sec import snapshot
    from screener.portfolio.construct import liquid_mask
    from screener.signals import leads as ld
    from screener.signals.stock import compute_stock_signals, first_trade_dates
    lc = cfg["leads"]
    ret_d = panels["ret"]
    out: dict = {}
    # month-end history for the slow leads
    hp = cache / "leads_monthly.pkl"
    hist = pd.read_pickle(hp) if hp.exists() else {}
    tick = [t for t in df.index if t in ret_d.columns]
    ret_m = (1 + ret_d[tick].fillna(0)).resample("ME").prod() - 1
    ret_m = ret_m.where(ret_d[tick].notna().resample("ME").sum() > 0)
    first_of_month = pd.Timestamp(asof).normalize().replace(day=1)
    mes = [m for m in ret_m.index if m < first_of_month][-12:]   # complete month ends only
    share = lc["flag_share"]
    rm_cfg = lc["residual_momentum"]
    fd = first_trade_dates(ret_d[tick])
    blank = pd.DataFrame(np.nan, index=tick, columns=["vol", "beta"])
    changed = False
    for m in mes:
        key = m.strftime("%Y-%m-%d")
        if key in hist:
            continue
        rec = {}
        rm = ld.residual_momentum(ret_m, factors, m, rm_cfg["window_months"], rm_cfg["formation_months"], rm_cfg["skip_months"])
        rm = rm.reindex(liq[liq].index).dropna()
        rec["resid_mom"] = list(rm.index[rm >= rm.quantile(1 - share)]) if len(rm) >= 20 else []
        if derived and univ is not None:
            try:
                dfm = compute_stock_signals(univ, snapshot(derived, m), panels, m, cfg, insiders=None, short=None,
                                            ret_m=ret_m.loc[:m], vb=blank, first_dates=fd)
                g = ld.mispricing(dfm, lc["mispricing"]["winsor"], lc["mispricing"]["min_names"])
                g = g.reindex(dfm.index[liquid_mask(dfm, cfg).fillna(False)]).dropna()
                if len(g) >= 20:
                    rec["mispricing"] = list(g.index[g >= g.quantile(1 - share)])
                    rec["mispricing_bear"] = list(g.index[g <= g.quantile(share)])
            except Exception as e:  # noqa: BLE001
                log.warning("month-end mispricing %s failed: %s", key, e)
        hist[key] = rec
        changed = True
    if changed:
        hist = {k: hist[k] for k in sorted(hist)[-24:]}
        pd.to_pickle(hist, hp)
    keys = [k for k in sorted(hist) if k in {m.strftime("%Y-%m-%d") for m in mes}]
    first = pd.Timestamp(keys[0]) if keys else None
    for side in ("bull", "bear"):
        for t in F.get("mispricing", {}).get(side, []):
            d = ld.month_end_streak(hist, "mispricing" if side == "bull" else "mispricing_bear", t, keys)
            if d is not None:
                out[("mispricing", side, t)] = d
                if d == first:
                    out[("_capped", "mispricing", side, t)] = True
    for t in F.get("resid_mom", {}).get("bull", []):
        d = ld.month_end_streak(hist, "resid_mom", t, keys)
        if d is not None:
            out[("resid_mom", "bull", t)] = d
            if d == first:
                out[("_capped", "resid_mom", "bull", t)] = True
    for side in ("bull", "bear"):
        for t in F.get("earnings_reaction", {}).get(side, []):
            if pd.notna(L.at[t, "known"]):
                out[("earnings_reaction", side, t)] = pd.Timestamp(L.at[t, "known"])
    for side, direction in (("bull", 1), ("bear", -1)):
        ib = list(F.get("insider_buying", {}).get(side, []))
        if ib and insiders is not None and "cik" in df:
            dates = ld.insider_signal_dates(insiders, asof, df.loc[ib, "cik"].tolist(), lc["insider_buying"]["window_days"],
                                            lc["insider_buying"]["min_net"], direction)
            for t in ib:
                c = df.at[t, "cik"]
                if pd.notna(c) and int(c) in dates:
                    out[("insider_buying", side, t)] = dates[int(c)]
    av = list(F.get("abnormal_volume", {}).get("bull", []))
    if av:
        cut = float(L.loc[av, "abnormal_volume"].min())
        for t, d in ld.volume_streak_start(panels.get("volume"), av, cut, asof, lc["abnormal_volume"]["recent_days"],
                                           lc["abnormal_volume"]["base_days"]).items():
            out[("abnormal_volume", "bull", t)] = d
    return out


def lead_state(out, F, since, panels, asof):
    """Track every flag from the day it starts (docs/data/leads_state.json): signal date, price, return
    since, and closed signals for a live record. Returns (state, per-lead live summary)."""
    ret, close = panels["ret"], panels.get("close")
    spy = "SPY" if "SPY" in ret else None
    today = pd.Timestamp(asof).strftime("%Y-%m-%d")
    old = _read(out, "leads_state.json") or {}
    state = {"started": old.get("started", today), "active": {}, "closed": list(old.get("closed", []))}
    prev = old.get("active", {})
    for k, sides in F.items():
        for side, names in sides.items():
            for t in names:
                key = f"{k}|{side}|{t}"
                if key in prev:
                    e = dict(prev[key])
                else:
                    d = since.get((k, side, t), pd.Timestamp(asof))
                    e = {"lead": k, "side": side, "t": t, "since": pd.Timestamp(d).strftime("%Y-%m-%d"), "px": _px_on(close, t, d),
                         "capped": bool(since.get(("_capped", k, side, t)))}
                e["ret"] = _ret_between(ret, t, e["since"], asof)
                e["spy"] = _ret_between(ret, spy, e["since"], asof) if spy else None
                e["last"] = today
                state["active"][key] = e
    for key, e in prev.items():
        if key not in state["active"]:
            e = dict(e)
            e.update({"until": today, "px_out": _px_on(close, e["t"], asof), "ret": _ret_between(ret, e["t"], e["since"], asof),
                      "spy": _ret_between(ret, spy, e["since"], asof) if spy else None})
            state["closed"].append(e)
    state["closed"] = state["closed"][-3000:]
    live: dict = {}
    for e in list(state["active"].values()) + state["closed"]:
        if e["side"] != "bull" or e["since"] < state["started"] or e["since"] >= today:
            continue
        x = live.setdefault(e["lead"], {"n": 0, "ret": 0.0, "spy": 0.0})
        x["n"] += 1
        x["ret"] += e.get("ret") or 0.0
        x["spy"] += e.get("spy") or 0.0
    for k, x in live.items():
        x["avg_ret"], x["avg_spy"] = x["ret"] / x["n"], x["spy"] / x["n"]
        x["started"] = state["started"]
        del x["ret"], x["spy"]
    write_json(out / "leads_state.json", {"generated": utcnow(), **state})
    return state, live


# ============================================================================= orchestration
@stage("stock signals")
def raw_stock_signals(cfg, univ, snap, panels, insiders, short):
    asof = panels["ret"].index[-1]
    df = stk.compute_stock_signals(univ, snap, panels, asof, cfg, insiders=insiders, short=short)
    return yahoo_mcap_fallback(df, cfg)


def yahoo_mcap_fallback(df: pd.DataFrame | None, cfg: dict) -> pd.DataFrame | None:
    """Fill market cap (and the ratios that use it) from Yahoo where SEC share counts are missing or
    stale, typically multi-class companies such as Berkshire Hathaway."""
    df_ = df
    if df_ is None or not cfg["data"].get("yahoo_mcap_fallback"):
        return df_
    need = df_.index[df_["mcap"].isna() & df_["price"].notna()]
    need = df_.loc[need].sort_values("dollar_volume", ascending=False).index[:int(cfg["data"].get("yahoo_mcap_max", 80))]
    if len(need) == 0:
        return df_
    try:
        caps = px.yahoo_market_caps(list(need))
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo market cap fallback failed: %s", e)
        return df_
    df_ = df_.copy()
    df_["mcap_source"] = df_.get("mcap_source", pd.Series("SEC shares x price", index=df_.index))
    for t, v in caps.items():
        df_.at[t, "mcap"] = v
        df_.at[t, "mcap_source"] = "Yahoo (all share classes)"
        if pd.notna(df_.at[t, "price"]) and df_.at[t, "price"] > 0:
            df_.at[t, "shares_adj"] = v / df_.at[t, "price"]
        eq, ni = df_.at[t, "equity"], df_.at[t, "net_income_ttm"]
        df_.at[t, "bm"] = eq / v if pd.notna(eq) and eq > 0 else np.nan
        df_.at[t, "ep"] = ni / v if pd.notna(ni) else np.nan
        if "short_shares" in df_ and pd.notna(df_.at[t, "short_shares"]) and pd.notna(df_.at[t, "shares_adj"]):
            df_.at[t, "si_ratio"] = df_.at[t, "short_shares"] / df_.at[t, "shares_adj"]
    log.info("Yahoo market-cap fallback filled %d of %d", len(caps), len(need))
    return df_


def sector_issuance_z(df: pd.DataFrame | None) -> dict:
    if df is None or df.empty or "net_issuance" not in df:
        return {}
    med = df.groupby("sector")["net_issuance"].median().dropna()
    if len(med) < 3 or med.std() == 0:
        return {}
    return ((med - med.mean()) / med.std()).to_dict()


@stage("scoring")
def score_stocks(cfg, df, regime_out, earnings=None):
    from screener.scoring import composite as comp
    res = comp.score_all_modes(df, cfg, regime_out["regime"], regime_out["crash"])
    bubble_sectors = set()
    for row in regime_out["bubble"].get("rows", []):
        meta = cfg["etfs"]["list"].get(row.get("ticker"), {})
        if row.get("status") in ("amber", "red") and meta.get("gics"):
            bubble_sectors.add(meta["gics"])
    fl = comp.flags(df, cfg, bubble_sectors, earnings)
    whys = {m: comp.why_ranked(res["scores"], cfg["modes"][m]["weights"], df["sector"]) for m in cfg["modes"]}
    return res, fl, whys


def stocks_payload(df, res, fl, whys, cfg, asof) -> dict:
    sc = res["scores"]
    rows = []
    for t in df.index:
        r = df.loc[t]
        rows.append({
            "t": t, "n": r.get("name"), "s": r.get("sector"), "si": r.get("sub_industry"),
            "px": r.get("price"), "dv": r.get("dollar_volume"), "mc": r.get("mcap"),
            "f": {k: sc.at[t, k] for k in sc.columns},
            "raw": {"bm": r.get("bm"), "ep": r.get("ep"), "mom": r.get("momentum"), "gpa": r.get("gpa"),
                    "opa": r.get("opa"), "ag": r.get("asset_growth"), "iss": r.get("net_issuance"), "ins": r.get("insider_net"),
                    "si": r.get("si_ratio"), "dtc": r.get("days_to_cover"), "sue": r.get("sue"),
                    "vol": r.get("vol"), "beta": r.get("beta")},
            "m": {m: res[m].get(t) for m in cfg["modes"]},
            "why": {m: whys[m].get(t) for m in cfg["modes"]},
            "fl": fl.get(t, []),
        })
    cov = {k: float(sc[k].notna().mean()) for k in sc.columns}
    return {"asof": asof, "generated": utcnow(), "rows": rows, "coverage": cov,
            "penalty_active": bool(res["penalty"].sum() > 0),
            "modes": {m: {"label": c["label"], "weights": c["weights"]} for m, c in cfg["modes"].items()},
            "definitions": stk.DEFINITIONS, "cites": stk.CITE}


@stage("etf scoring")
def build_etfs(cfg, panels, holdings, mode_scores, bubble_rows):
    from screener.scoring import etf as etfs
    sig = etfs.etf_signals(panels["ret"], panels.get("close"), panels.get("volume"), cfg)
    lt = {}
    for mode, s in (mode_scores or {}).items():
        if holdings:
            lt[mode] = etfs.lookthrough(holdings, s)
    scored = etfs.score_etfs(sig, lt, bubble_rows, cfg)
    out = {}
    for mode, d in scored.items():
        cols = [c for c in ("name", "kind", "er", "score", "momentum", "ret_12m", "above_sma", "trend", "vol",
                            "risk_adj_momentum", "dollar_volume", "lookthrough", "lookthrough_coverage",
                            "bubble", "bubble_penalty") if c in d]
        out[mode] = [{"t": t, **{c: d.at[t, c] for c in cols}} for t in d.index]
    unavailable = sig.index[sig["available"] != True].tolist()  # noqa: E712
    return {"modes": out, "unavailable": unavailable, "cites": etfs.CITE, "generated": utcnow()}


@stage("portfolios")
def build_portfolios(cfg, df, res, whys, regime_out, panels, prev_payload):
    from screener.portfolio import construct as pc
    from screener.scenarios import engine as se
    reg = regime_out["regime"]
    sent_label = regime_out["dials"]["sentiment"].get("label")
    ret_d = panels["ret"]
    rate_beta = None
    if "IEF" in ret_d:
        r = ret_d.iloc[-504:]
        ief = r["IEF"]
        icen = ief - ief.mean()
        rate_beta = r.sub(r.mean()).mul(icen, axis=0).mean() / (icen ** 2).mean()
    probs = se.scenario_probs(cfg, regime_out["dials"], reg)
    out = {"regime": reg, "scenario_probs": probs, "modes": {}, "generated": utcnow()}
    prev = (prev_payload or {}).get("modes", {})
    for mode, mc in cfg["modes"].items():
        score = res[mode]
        pv = prev.get(mode, {})
        prev_names = [h["ticker"] for h in pv.get("holdings", []) if h.get("side", "long") == "long"]
        prev_short = [h["ticker"] for h in pv.get("holdings", []) if h.get("side") == "short"]
        prev_w = {h["ticker"]: h["weight"] for h in pv.get("holdings", [])}
        meta = {}
        if mode == "long_term":
            port = pc.long_only(score, df, cfg, mc, reg["label"], prev_names, whys[mode]).assign(side="long")
        elif mode == "market_neutral":
            port = pc.market_neutral(score, df, cfg, mc, sent_label, prev_names, prev_short, whys[mode])
        else:
            names = pc.select(score, df, mc["portfolio"]["n"], cfg, prev_names)
            bvol = float(ret_d[names].iloc[-63:].mean(axis=1).std() * np.sqrt(252)) if names else None
            port, meta = pc.absolute_return(score, df, cfg, mc, reg, bvol, regime_out["trend"], prev_names, whys[mode])
        if port is None or port.empty:
            out["modes"][mode] = {"status": "unavailable", "reason": "no eligible names"}
            continue
        tc = pc.turnover_and_cost(port, prev_w, df, cfg)
        trend_rows = port[port["side"] == "trend"] if "side" in port else None
        scen = se.scenario_returns(port, res["scores"], cfg, probs, rate_beta, trend_rows)
        stress = se.stress_tests(port, ret_d, cfg)
        basket = se.basket_returns(port, ret_d)
        dvol = float(basket.std()) if len(basket) > 60 else 0.01
        if mode == "market_neutral":
            mu = scen["expected"] + (regime_out.get("rf") or 0.0)
        else:
            mu = scen["expected"]
        mc_res = se.monte_carlo(dvol, mu, cfg)
        out["modes"][mode] = {
            "label": mc["label"], "research_only": bool(mc.get("research_only")),
            "summary": pc.summarize(port), "meta": meta, "turnover": tc,
            "holdings": clean(port.to_dict("records")), "scenarios": scen, "stress": stress, "monte_carlo": mc_res,
        }
    return out


def methodology(cfg) -> dict:
    from screener.scoring import etf as etfs
    sigs = []
    m = cfg["macro"]
    sigs += [
        {"id": 1, "name": "Credit-to-GDP gap", "source": "BIS via FRED QUSPAM770A", "freq": "quarterly",
         "formula": f"ratio minus one-sided HP trend, lambda {m['credit_gap']['hp_lambda']:,}",
         "rule": f"green < {m['credit_gap']['green_below']} pp, amber to {m['credit_gap']['red_above']}, red above",
         "cite": macro.CITE["credit_gap"]},
        {"id": 2, "name": "Red zones", "source": "FRED QUSNAM770A, QUSHAM770A, CSUSHPINSA; Shiller S&P price",
         "freq": "quarterly", "formula": "3y change in credit/GDP and 3y asset price growth, expanding percentiles",
         "rule": "red when credit >= 80th pct and prices >= 66.7th pct", "cite": macro.CITE["red_zones"]},
        {"id": 3, "name": "Yield-curve recession odds", "source": "FRED T10Y3M", "freq": "daily",
         "formula": "probit fitted to Estrella-Mishkin's table on the trailing-quarter average spread",
         "rule": f"green < {m['yield_curve']['green_below']}%, red > {m['yield_curve']['red_above']}%",
         "cite": macro.CITE["yield_curve"]},
        {"id": 4, "name": "Excess bond premium", "source": "Federal Reserve ebp_csv.csv (fallback FRED BAMLH0A0HYM2)",
         "freq": "monthly", "formula": "published EBP", "rule": "amber > 0, red > 0.5", "cite": macro.CITE["ebp"]},
        {"id": 5, "name": "Valuation (CAPE)", "source": "Shiller ie_data.xls", "freq": "monthly",
         "formula": "CAPE percentile; 10y expected real return from log-CAPE regression",
         "rule": "context only, never a timing signal", "cite": macro.CITE["cape"]},
        {"id": 6, "name": "Sentiment composite", "source": "VIX, SPHB/SPLV, IPO/SPY; Baker-Wurgler (lagged)",
         "freq": "daily", "formula": "mean of rolling 5y z-scores, re-standardised",
         "rule": "euphoric > +1 sd, fearful < -1 sd", "cite": sentiment.CITE["sentiment"]},
        {"id": 7, "name": "Variance risk premium", "source": "VIX, SPY", "freq": "daily",
         "formula": "VIX^2/12 - 21d realised variance", "rule": "percentile; high = better next-quarter odds",
         "cite": sentiment.CITE["vrp"]},
        {"id": 8, "name": "Bubble watch", "source": "Yahoo ETF prices", "freq": "intraday",
         "formula": "2y return minus SPY 2y return; acceleration, volatility, issuance warnings",
         "rule": ">= 100% amber, >= 150% red", "cite": bub.CITE["bubble"]},
        {"id": 9, "name": "Momentum crash guard", "source": "SPY", "freq": "daily",
         "formula": "24m market return < 0 and 126d vol >= 80th pct", "rule": "momentum weight x 0.25 when on",
         "cite": bub.CITE["crash_guard"]},
        {"id": 10, "name": "Market trend", "source": "SPY, IEF, GLD, DBC", "freq": "daily",
         "formula": "12m excess return sign and price vs 10-month SMA", "rule": "up / mixed / down",
         "cite": bub.CITE["trend"]},
    ]
    sid = 11
    for k in ("value", "momentum", "profitability", "investment", "issuance", "insiders", "short_interest", "sue", "lowvol"):
        src = {"insiders": "SEC Form 3/4/5 quarterly data sets", "short_interest": "FINRA consolidated short interest",
               "momentum": "Yahoo daily prices", "lowvol": "Yahoo daily prices"}.get(k, "SEC companyfacts (point-in-time) + Yahoo prices")
        sigs.append({"id": sid, "name": k.replace("_", " ").title(), "source": src,
                     "freq": "twice monthly" if k == "short_interest" else ("quarterly" if k == "insiders" else "daily/quarterly"),
                     "formula": stk.DEFINITIONS[k], "rule": "within-GICS-sector percentile (higher = better)",
                     "cite": stk.CITE[k]})
        sid += 1
    sigs += [{"id": 20, "name": "ETF momentum and trend", "source": "Yahoo", "freq": "intraday",
              "formula": "12-1 momentum, trend (12m return and 10m SMA), bubble penalty, risk-adjusted momentum",
              "rule": "mode-weighted percentile", "cite": etfs.CITE["etf_momentum"]},
             {"id": 21, "name": "ETF look-through", "source": "SSGA daily holdings", "freq": "daily",
              "formula": "holdings-weighted stock score", "rule": "mode composite", "cite": etfs.CITE["lookthrough"]},
             {"id": 22, "name": "Event calendar", "source": "federalreserve.gov, bls.gov, Yahoo earnings",
              "freq": "daily", "formula": "FOMC, CPI, jobs and earnings dates", "rule": "flag events within 7 days",
              "cite": "Savor & Wilson (2013); Lucca & Moench (2015)"}]
    return {"signals": sigs, "excluded": cfg["signals"]["excluded_failed_replication"],
            "excluded_cite": "Hou, Xue & Zhang (2020), 'Replicating Anomalies', RFS",
            "modes": {k: {"label": v["label"], "weights": v["weights"]} for k, v in cfg["modes"].items()},
            "regime_rule": "Fragile = (credit gap or a red zone is red) AND Stretched; Stretched = euphoric sentiment "
                           "or >= 20% of sector/industry ETFs bubble-flagged; otherwise Calm",
            "generated": utcnow()}


def events_payload(cfg, earnings: dict, today=None) -> dict:
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    ev = []
    for kind, label, cite in (("fomc", "FOMC decision", "Lucca & Moench (2015)"),
                              ("cpi", "CPI release", "Savor & Wilson (2013)"),
                              ("jobs", "Jobs report", "Savor & Wilson (2013)")):
        for d in cfg["events"][kind]:
            dt = pd.Timestamp(d)
            if dt >= today - pd.Timedelta(days=1):
                ev.append({"date": d, "type": label, "cite": cite})
    for t, d in (earnings or {}).items():
        ev.append({"date": d, "type": "Earnings", "ticker": t, "cite": "Bernard & Thomas (1989)"})
    ev.sort(key=lambda x: x["date"])
    week_end = today + pd.Timedelta(days=7)
    this_week = [e for e in ev if today <= pd.Timestamp(e["date"]) <= week_end]
    return {"events": ev[:120], "this_week": this_week, "event_this_week": bool(this_week), "generated": utcnow(),
            "sources": {"fomc": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                        "bls": "https://www.bls.gov/schedule/"}}


def _read(out: Path, name: str):
    p = out / name
    if p.exists():
        try:
            import json
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            return None
    return None


def run_live(cfg, f, cache, out, full: bool) -> list[str]:
    written = []
    macro_d = load_macro(f, cfg)
    sh = ms.safe(ms.shiller, f, cfg, source="Shiller CAPE")
    ebp_df = ms.safe(ms.ebp, f, cfg, source="Fed EBP")
    bw = ms.safe(ms.baker_wurgler, f, cfg, source="Baker-Wurgler")
    etf_list = list(cfg["etfs"]["list"].keys()) + ["^VIX"]
    univ = None
    if full:
        univ = ms.safe(load_universe, f, cfg, cache, source="Universe")
        cmap = ms.safe(sec_cik_map, f, cfg, default={}, source="SEC tickers")
        if univ is not None:
            univ["cik"] = univ["ticker"].map(cmap)
            tickers = univ["ticker"].tolist() + etf_list
        else:
            tickers = etf_list
        panels = px.update_prices(f, cfg, cache, tickers, intraday=False)
    else:
        panels = px.update_prices(f, cfg, cache, etf_list, intraday=True)
    if panels.get("ret") is None:
        record_fail = "no price data"
        raise RuntimeError(record_fail)

    df = snap = insiders = short = holdings = None
    if full and univ is not None:
        r = build_fundamentals(cfg, f, cache, univ)
        derived, snap = r if r else (None, None)
        insiders = sec.apply_aliases(ms.safe(sec.load_insiders, f, cfg, cache, source="SEC Form 3/4/5"),
                                     "issuer_cik", cfg)
        short_res = ms.safe(mk.load_short_interest, f, cfg, source="FINRA short interest")
        short = short_res[0] if short_res else None
        holdings = ms.safe(mk.load_holdings, f, cfg, default={}, source="SSGA ETF holdings")
        df = raw_stock_signals(cfg, univ, snap, panels, insiders, short)

    prev_regime = _read(out, "regime.json") or {}
    s_iss = sector_issuance_z(df) if df is not None else prev_regime.get("sector_issuance_z", {})
    reg = build_regime(cfg, f, panels, macro_d, sh, ebp_df, bw, s_iss)
    if reg is None:
        return written
    write_json(out / "regime.json", {"generated": utcnow(), "asof": panels["ret"].index[-1],
                                     "regime": reg["regime"], "dials": reg["dials"], "trend": reg["trend"],
                                     "sector_issuance_z": s_iss})
    write_json(out / "bubble.json", {"generated": utcnow(), **{k: v for k, v in reg["bubble"].items()}})
    written += ["regime.json", "bubble.json"]

    mode_scores = None
    if full and df is not None:
        sc = score_stocks(cfg, df, reg)
        if sc:
            res, fl, whys = sc
            top = res["long_term"].sort_values(ascending=False).head(cfg["events"]["earnings_top_n"]).index.tolist()
            top += res["market_neutral"].sort_values(ascending=False).head(20).index.tolist()
            earnings = mk.earnings_dates(sorted(set(top)))
            sc = score_stocks(cfg, df, reg, earnings)
            res, fl, whys = sc
            write_json(out / "stocks.json", stocks_payload(df, res, fl, whys, cfg, panels["ret"].index[-1]))
            write_json(out / "events.json", events_payload(cfg, earnings))
            written += ["stocks.json", "events.json"]
            if build_leads(cfg, f, cache, out, df, panels, insiders, univ, derived):
                written.append("leads.json")
            mode_scores = {m: res[m] for m in cfg["modes"]}
            port = build_portfolios(cfg, df, res, whys, reg, panels, _read(out, "portfolio.json"))
            if port:
                write_json(out / "portfolio.json", port)
                written.append("portfolio.json")
                from screener.backtest.track import evaluate, save_snapshot
                picks = {m: [h["ticker"] for h in v.get("holdings", []) if h.get("side") in ("long", None)][:20]
                         for m, v in port["modes"].items() if isinstance(v, dict)}
                d0 = panels["ret"].index[-1].strftime("%Y-%m-%d")
                prices = {t: float(df.at[t, "price"]) for ts in picks.values() for t in ts if t in df.index}
                save_snapshot(out / "track", d0, picks, prices)
            write_json(out / "methodology.json", methodology(cfg))
            written.append("methodology.json")
    else:
        prev_st = _read(out, "stocks.json")
        if prev_st:
            mode_scores = {m: pd.Series({r["t"]: r["m"].get(m) for r in prev_st["rows"]}) for m in cfg["modes"]}
        prev_ev = _read(out, "events.json")
        earn = {e["ticker"]: e["date"] for e in (prev_ev or {}).get("events", []) if e.get("ticker")}
        write_json(out / "events.json", events_payload(cfg, earn))
        written.append("events.json")

    if holdings is None:
        holdings = ms.safe(mk.load_holdings, f, cfg, default={}, source="SSGA ETF holdings")
    et = build_etfs(cfg, panels, holdings, mode_scores, reg["bubble"].get("rows", []))
    if et:
        write_json(out / "etfs.json", et)
        written.append("etfs.json")

    from screener.backtest.track import evaluate
    tr = evaluate(out / "track", panels["ret"])
    write_json(out / "track.json", {"generated": utcnow(), **tr})
    written.append("track.json")
    return written
