"""Weekly job: backtests and validation (factor-level, regime dials, bubbles, stock-level)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import load_macro, spliced_stock_price, stage
from screener.backtest import factor_bt, regime_bt
from screener.data import macro_sources as ms
from screener.data import prices as px
from screener.data import sec
from screener.data.universe import load_universe, sec_cik_map
from screener.util import utcnow, write_json

log = logging.getLogger("pipeline")


@stage("factor backtests")
def factor_stage(cfg, bundle, regime_hist, extra):
    sent = regime_hist["sentiment"] if regime_hist is not None else None
    return factor_bt.run_factor_backtests(bundle, cfg, regime_hist, sent, extra)


@stage("regime history")
def regime_stage(cfg, macro_d, bundle, mkt, bw, vix):
    return regime_bt.regime_history(macro_d, bundle.get("ind49"), mkt, cfg, bw, vix)


@stage("dial validation")
def validation_stage(cfg, macro_d, bundle, h, mkt, rf, sh, ebp_df, vix, spy, bonds):
    out = {"generated": utcnow()}
    out["regime"] = regime_bt.validate_regime(h, mkt, rf) if h is not None else {"status": "unavailable"}
    out["yield_curve"] = regime_bt.validate_yield_curve(macro_d, cfg)
    out["credit"] = regime_bt.validate_credit_dials(h, mkt) if h is not None else {"status": "unavailable"}
    out["ebp"] = regime_bt.validate_ebp(ebp_df, macro_d.get("USREC"), mkt)
    out["cape"] = regime_bt.validate_cape(sh, cfg)
    out["vrp"] = regime_bt.validate_vrp(vix, spy)
    out["sentiment"] = regime_bt.validate_sentiment(h, bundle.get("sorts", {}), mkt) if h is not None else {}
    out["bubbles"] = regime_bt.validate_bubbles(bundle.get("ind49"), mkt, cfg)
    out["trend"] = regime_bt.validate_trend(mkt, rf, bonds)
    return out


@stage("stock-level backtest")
def stock_stage(cfg, f, cache, panels, regime_hist):
    from screener.backtest.stock_bt import run_stock_backtest
    univ = ms.safe(load_universe, f, cfg, cache, source="Universe")
    if univ is None:
        return {"status": "unavailable", "reason": "universe unavailable"}
    cmap = ms.safe(sec_cik_map, f, cfg, default={}, source="SEC tickers")
    univ["cik"] = univ["ticker"].map(cmap)
    store_p = cache / "sec_facts.pkl"
    derived = None
    if store_p.exists():
        store = pd.read_pickle(store_p)
        derived = sec.derive_all(store, cfg["signals"]["sue"]["min_quarters"])
    insiders = ms.safe(sec.load_insiders, f, cfg, cache, cfg["backtest"]["stock_bt_insiders_since"],
                       source="SEC Form 3/4/5 (history)")
    return run_stock_backtest(univ, panels, derived or {}, cfg, insiders, regime_hist)


@stage("research panel")
def research_panel(cfg, macro_d, h, series, sh, ebp_df, vix, spy):
    """Monthly panel of every dial (as known at each month end) and strategy returns, for crash and
    buy-timing studies. Written to docs/data/research_panel.csv."""
    from screener.signals.macro import fit_em_probit, recession_prob
    if series is None:
        return None
    p = pd.DataFrame({k: v for k, v in series.items()})
    p = p[p.index >= "1926-01-01"]
    if h is not None:
        for c in ("label", "multiplier", "credit_gap", "business_red_zone", "household_red_zone", "sentiment_z",
                  "sentiment", "bubble_share", "credit_red", "stretched"):
            if c in h:
                p[c] = h[c].reindex(p.index)
    gs10, tb3, t10y3m = macro_d.get("GS10"), macro_d.get("TB3MS"), macro_d.get("T10Y3M")
    if gs10 is not None and tb3 is not None:
        sp = (gs10.resample("ME").mean() - tb3.resample("ME").mean())
        if t10y3m is not None:
            sp = t10y3m.resample("ME").mean().combine_first(sp)
        p["term_spread"] = sp.reindex(p.index)
        a, b = fit_em_probit(cfg["macro"]["yield_curve"]["em_table"])
        q = p["term_spread"].rolling(3).mean()
        p["recession_prob"] = recession_prob(q.dropna(), a, b).reindex(p.index)
    baa = macro_d.get("BAA")
    if baa is not None and gs10 is not None:
        p["baa_spread"] = (baa.resample("ME").last() - gs10.resample("ME").last()).reindex(p.index)
    if ebp_df is not None and "ebp" in ebp_df:
        p["ebp"] = ebp_df["ebp"].reindex(p.index)
    if vix is not None:
        p["vix"] = vix.resample("ME").last().reindex(p.index)
    if spy is not None and vix is not None:
        iv = vix.dropna() ** 2 / 12
        rv = ((np.log1p(spy.dropna()) * 100) ** 2).rolling(21).sum()
        p["vrp"] = (iv - rv).dropna().resample("ME").last().reindex(p.index)
    if sh is not None and not sh.empty:
        p["cape"] = sh["cape"].reindex(p.index)
    rec = macro_d.get("USREC")
    if rec is not None:
        p["usrec"] = rec.resample("ME").max().reindex(p.index)
    p.index.name = "date"
    return p


def _monthly(ret: pd.Series | None) -> pd.Series | None:
    if ret is None:
        return None
    r = ret.dropna()
    if r.empty:
        return None
    return (1 + r).resample("ME").prod() - 1


def run_weekly(cfg, f, cache, out) -> list[str]:
    written = []
    bundle = factor_bt.load_french_bundle(f, cfg)
    macro_d = load_macro(f, cfg)
    sh = ms.safe(ms.shiller, f, cfg, source="Shiller CAPE")
    ebp_df = ms.safe(ms.ebp, f, cfg, source="Fed EBP")
    bw = ms.safe(ms.baker_wurgler, f, cfg, source="Baker-Wurgler")
    panels = px._load(cache)
    ret = panels.get("ret")
    spy = ret["SPY"] if ret is not None and "SPY" in ret else None
    vix = macro_d.get("VIXCLS")
    if ret is not None and panels.get("close") is not None and "^VIX" in panels["close"]:
        vix = panels["close"]["^VIX"].dropna().combine_first(vix) if vix is not None else panels["close"]["^VIX"].dropna()
    if "ff3" not in bundle and "ff5" not in bundle:
        write_json(out / "backtest.json", {"generated": utcnow(), "status": "unavailable",
                                           "reason": "Ken French data library unreachable"})
        return ["backtest.json"]
    base = bundle.get("ff3", bundle.get("ff5"))
    mkt = base["Mkt-RF"] + base["RF"]
    rf = base["RF"]
    macro_d["_stock_price"] = spliced_stock_price(sh, spy, mkt)
    bonds = factor_bt.bond_returns_from_yields(macro_d.get("GS10"), cfg["backtest"]["bond_duration"])
    extra = {"bonds": bonds}
    for k, sym in cfg["backtest"]["trend_assets_monthly"].items():
        if ret is not None and sym in ret:
            extra[k] = _monthly(ret[sym])

    h = regime_stage(cfg, macro_d, bundle, mkt, bw, vix)
    fb = factor_stage(cfg, bundle, h, extra)
    series = fb.pop("_series", None) if fb else None
    panel = research_panel(cfg, macro_d, h, series, sh, ebp_df, vix, spy)
    if panel is not None:
        panel.to_csv(out / "research_panel.csv", float_format="%.6g")
        written.append("research_panel.csv")
    val = validation_stage(cfg, macro_d, bundle, h, mkt, rf, sh, ebp_df, vix, spy, bonds)
    payload = {"generated": utcnow(), "factor": fb, "notes": {
        "survivorship": "Factor-level results use Ken French CRSP portfolios (survivorship-free, delisting returns "
                        "included). Stock-level results use today's S&P 1500 and are survivorship-biased.",
        "look_ahead": "Credit data lagged 2 quarters; house prices 2 months; Baker-Wurgler 1 month (its PCA is "
                      "estimated on the full sample). Fundamentals enter only after their SEC filing date.",
    }}
    if h is not None:
        payload["regime_history"] = {
            "label_counts": h["label"].value_counts().to_dict(),
            "since": h.index[0].strftime("%Y-%m"),
            "series": [[d.strftime("%Y-%m"), v, (None if pd.isna(z) else round(float(z), 2)),
                        (None if pd.isna(b) else round(float(b), 3))]
                       for d, v, z, b in zip(h.index, h["label"], h["sentiment_z"], h["bubble_share"])],
        }
    write_json(out / "backtest.json", payload)
    write_json(out / "validation.json", val or {"status": "unavailable"})
    written += ["backtest.json", "validation.json"]

    if ret is not None and ret.shape[1] > 200:
        sb = stock_stage(cfg, f, cache, panels, h)
        if sb:
            write_json(out / "stock_backtest.json", {"generated": utcnow(), **sb})
            written.append("stock_backtest.json")
    else:
        write_json(out / "stock_backtest.json", {"generated": utcnow(), "status": "unavailable",
                                                 "reason": "stock price panel not cached yet (run nightly first)"})
        written.append("stock_backtest.json")
    return written
