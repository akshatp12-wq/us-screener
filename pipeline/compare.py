"""v1 vs v2, side by side, with each change tested on its own.

Guardrail: a change is kept only if it helps in 1963-2010 (factor level, survivorship-free), not just
since 2011. Each change is tested two ways: added alone to v1 ("v1 + change") and removed alone from v2
("v2 - change"). Changes with no pre-2011 data (earnings-release timing, T-bill cash, insider weight)
are judged on the stock-level test since 2011 and flagged as such.

Writes docs/data/compare.json.
"""
from __future__ import annotations

import copy
import logging
import math
import time

import numpy as np
import pandas as pd

from pipeline.common import load_macro, spliced_stock_price, stage
from pipeline.weekly import _monthly, stock_inputs
from screener.backtest import factor_bt, regime_bt
from screener.backtest.crashes import bear_markets
from screener.data import macro_sources as ms
from screener.data import prices as px
from screener.util import ann_stats, utcnow, write_json

log = logging.getLogger("pipeline")

V1_LT = {"value": 0.25, "profitability": 0.25, "investment": 0.20, "issuance": 0.20, "insiders": 0.10}
V1_AR = {"value": 0.20, "profitability": 0.20, "investment": 0.15, "issuance": 0.15, "insiders": 0.10, "lowvol": 0.20}

CHANGES = {
    "reweight": {
        "label": "Reweighted scores: insiders 10%->20%, low volatility 20%->5%, investment and issuance 20%->15% (long-term)",
        "v1": {("modes", "long_term", "weights"): V1_LT, ("modes", "absolute_return", "weights"): V1_AR},
        "note": "At factor level insiders have no history, so this tests the rest of the reweighting.",
    },
    "trend_switch": {
        "label": "Absolute return: halve the stock sleeve when the market is below its 10-month average",
        "v1": {("modes", "absolute_return", "trend_switch", "enabled"): False},
    },
    "credit_alone": {
        "label": "Regime: a red credit dial on its own raises the label to Stretched",
        "v1": {("regime", "credit_alone_label"): "Calm"},
    },
    "sue_release": {
        "label": "Earnings surprise timed to the press release (8-K 2.02) instead of the 10-Q",
        "v1": {("signals", "sue", "release_dates"): False}, "stock_only": True,
    },
    "cash_tbill": {
        "label": "Stock-level test pays T-bill interest on uninvested cash",
        "v1": {("backtest", "cash_earns_tbill"): False}, "stock_only": True, "accounting": True,
    },
}
PERIODS = {"1963-2010": ("1963-07-01", "2010-12-31"), "2011-now": ("2011-01-01", None), "full": ("1963-07-01", None)}
MODES = ("long_term", "absolute_return")


def with_overrides(cfg: dict, ov: dict) -> dict:
    c = copy.deepcopy(cfg)
    for path, v in ov.items():
        d = c
        for k in path[:-1]:
            d = d.setdefault(k, {})
        d[path[-1]] = copy.deepcopy(v)
    return c


def v1_config(cfg: dict) -> dict:
    ov = {}
    for ch in CHANGES.values():
        ov.update(ch["v1"])
    return with_overrides(cfg, ov)


def _f(x):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), 4)


def period_stats(r: pd.Series, mkt: pd.Series, rf: pd.Series, bears: list[dict]) -> dict:
    out = {}
    for name, (a, b) in PERIODS.items():
        sl = slice(pd.Timestamp(a), pd.Timestamp(b) if b else None)
        rr = r.loc[sl].dropna()
        if len(rr) < 24:
            continue
        mm, ff = mkt.reindex(rr.index), rf.reindex(rr.index).fillna(0)
        st, mk = ann_stats(rr), ann_stats(mm)
        ex = rr - ff
        bear_r = []
        for e in bears:
            if not (rr.index[0] <= e["peak"] <= rr.index[-1]):
                continue
            seg = r.loc[e["peak"] + pd.offsets.MonthEnd(1):e["trough"]].dropna()
            mseg = mkt.loc[e["peak"] + pd.offsets.MonthEnd(1):e["trough"]].dropna()
            if len(seg):
                bear_r.append((float((1 + seg).prod() - 1), float((1 + mseg).prod() - 1)))
        out[name] = {
            "cagr": _f(st["cagr"]), "vol": _f(st["ann_vol"]),
            "sharpe": _f(ex.mean() / ex.std() * np.sqrt(12)) if ex.std() > 0 else None,
            "max_dd": _f(st["max_dd"]), "market_cagr": _f(mk["cagr"]),
            "excess_cagr": _f(st["cagr"] - mk["cagr"]),
            "bears": len(bear_r),
            "avg_bear": _f(np.mean([x for x, _ in bear_r])) if bear_r else None,
            "worst_bear": _f(min(x for x, _ in bear_r)) if bear_r else None,
            "market_avg_bear": _f(np.mean([y for _, y in bear_r])) if bear_r else None,
        }
    return out


def _better(a: dict, b: dict) -> float | None:
    """Sharpe difference a - b (None if missing)."""
    if not a or not b or a.get("sharpe") is None or b.get("sharpe") is None:
        return None
    return a["sharpe"] - b["sharpe"]


@stage("compare: factor level")
def factor_compare(cfg, bundle, h, extra) -> dict:
    v1 = v1_config(cfg)
    variants = {"v1": v1, "v2": cfg}
    for k, ch in CHANGES.items():
        if ch.get("stock_only"):
            continue
        variants[f"v1 + {k}"] = with_overrides(v1, {p: _get(cfg, p) for p in ch["v1"]})
        variants[f"v2 - {k}"] = with_overrides(cfg, ch["v1"])
    res, mkt_stats = {}, None
    for name, c in variants.items():
        hv = regime_bt.relabel(h, c) if h is not None else None
        fb = factor_bt.run_factor_backtests(bundle, c, hv, hv["sentiment"] if hv is not None else None, extra)
        s = fb["_series"]
        bears = bear_markets(s["market"], 0.15, "1963-07-01")
        res[name] = {m: period_stats(s[m], s["market"], s["rf"], bears) for m in MODES}
        if hv is not None:
            res[name]["regime_months"] = hv.loc["1963-07-01":, "label"].value_counts().to_dict()
        if mkt_stats is None:
            mkt_stats = period_stats(s["market"], s["market"], s["rf"], bears)
    verdicts = {}
    for k, ch in CHANGES.items():
        if ch.get("stock_only"):
            continue
        v = {"label": ch["label"], "note": ch.get("note")}
        for m in MODES:
            add = {p: _better(res[f"v1 + {k}"][m].get(p), res["v1"][m].get(p)) for p in PERIODS}
            drop = {p: _better(res["v2"][m].get(p), res[f"v2 - {k}"][m].get(p)) for p in PERIODS}
            dd_add = _dd(res[f"v1 + {k}"][m], res["v1"][m])
            v[m] = {"sharpe_gain_added_to_v1": add, "sharpe_gain_kept_in_v2": drop, "max_dd_gain_1963_2010": dd_add,
                    "changed": any(abs(x or 0) > 1e-4 for x in list(add.values()) + list(drop.values()))}
        early = [v[m]["sharpe_gain_added_to_v1"]["1963-2010"] for m in MODES if v[m]["changed"]] + \
                [v[m]["sharpe_gain_kept_in_v2"]["1963-2010"] for m in MODES if v[m]["changed"]]
        early = [x for x in early if x is not None]
        dd_ok = [v[m]["max_dd_gain_1963_2010"] for m in MODES if v[m]["changed"] and v[m]["max_dd_gain_1963_2010"] is not None]
        # keep if risk-adjusted return does not get worse before 2011, or it cuts drawdowns materially for
        # a small Sharpe cost (<= 0.02)
        keep = bool(early) and (min(early) >= -0.005 or (min(early) >= -0.02 and dd_ok and min(dd_ok) >= 0.02))
        v["holds_up_1963_2010"] = keep
        verdicts[k] = v
    return {"variants": res, "market": mkt_stats, "verdicts": verdicts}


def _get(cfg, path):
    d = cfg
    for k in path:
        d = d[k]
    return d


def _dd(a: dict, b: dict):
    a, b = a.get("1963-2010"), b.get("1963-2010")
    if not a or not b or a.get("max_dd") is None or b.get("max_dd") is None:
        return None
    return round(a["max_dd"] - b["max_dd"], 4)


def _stock_summary(sb: dict) -> dict:
    out = {"modes": {}, "signals": {}}
    for m, x in (sb.get("modes") or {}).items():
        st, spy = x.get("stats") or {}, x.get("spy") or {}
        uni = x.get("excess_vs_universe_ew") or {}
        out["modes"][m] = {"cagr": _f(st.get("cagr")), "vol": _f(st.get("ann_vol")), "sharpe": _f(st.get("sharpe")),
                           "max_dd": _f(x.get("max_dd")), "spy_cagr": _f(spy.get("cagr")),
                           "excess_vs_spy": _f((st.get("cagr") or 0) - (spy.get("cagr") or 0)) if st and spy else None,
                           "excess_vs_universe": _f(uni.get("ann_mean")), "turnover": _f(x.get("avg_turnover"))}
    for s, x in (sb.get("signals") or {}).items():
        ls = x.get("top30_minus_bottom30") or {}
        out["signals"][s] = {"spread": _f(ls.get("ann_mean")), "t": _f(x.get("t_nw")), "ic": _f(x.get("ic_mean"))}
    if "membership_coverage" in sb:
        out["membership_coverage"] = _f(sb["membership_coverage"])
    if sb.get("universe"):
        out["universe"] = {k: {"cagr": _f(v.get("cagr"))} for k, v in sb["universe"].items() if isinstance(v, dict)}
    return out


@stage("compare: stock level")
def stock_compare(cfg, f, cache, panels, h, rf) -> dict:
    from screener.backtest.stock_bt import run_stock_backtest
    v1 = v1_config(cfg)
    univ, der_v2, ins = stock_inputs(cfg, f, cache, use_releases=True)
    if univ is None:
        return {"status": "unavailable", "reason": "universe unavailable"}
    _, der_v1, _ = stock_inputs(v1, f, cache, use_releases=False)
    runs = {
        "v1": (v1, der_v1, None),
        "v2": (cfg, der_v2, None),
        "v2 - reweight": (with_overrides(cfg, CHANGES["reweight"]["v1"]), der_v2, None),
        "v2 - trend_switch": (with_overrides(cfg, CHANGES["trend_switch"]["v1"]), der_v2, None),
        "v2 - credit_alone": (with_overrides(cfg, CHANGES["credit_alone"]["v1"]), der_v2, None),
        "v2 - cash_tbill": (with_overrides(cfg, CHANGES["cash_tbill"]["v1"]), der_v2, None),
    }
    if cfg["backtest"].get("pit_membership"):
        try:
            from screener.data.membership import load_changes, members_fn
            ch = load_changes(f, cfg)
            cur = set(univ.loc[univ["index"] == "sp500", "ticker"])
            runs["v2, S&P 500 as of today"] = (cfg, der_v2, lambda t, cur=cur: cur)
            runs["v2, S&P 500 point-in-time"] = (cfg, der_v2, members_fn(cur, ch))
        except Exception as e:  # noqa: BLE001
            log.warning("membership sensitivity skipped: %s", e)
    out = {}
    for name, (c, der, mfn) in runs.items():
        t0 = time.time()
        hv = regime_bt.relabel(h, c) if h is not None else None
        sb = run_stock_backtest(univ, panels, der or {}, c, ins, hv, rf_m=rf, members_fn=mfn)
        out[name] = _stock_summary(sb)
        log.info("stock compare %s done in %.0fs", name, time.time() - t0)
    return {"runs": out, "since": cfg["backtest"]["stock_level_start"]}


def run_compare(cfg, f, cache, out) -> list[str]:
    bundle = factor_bt.load_french_bundle(f, cfg)
    if "ff3" not in bundle and "ff5" not in bundle:
        write_json(out / "compare.json", {"generated": utcnow(), "status": "unavailable",
                                          "reason": "Ken French data library unreachable"})
        return ["compare.json"]
    macro_d = load_macro(f, cfg)
    sh = ms.safe(ms.shiller, f, cfg, source="Shiller CAPE")
    bw = ms.safe(ms.baker_wurgler, f, cfg, source="Baker-Wurgler")
    panels = px._load(cache)
    ret = panels.get("ret")
    spy = ret["SPY"] if ret is not None and "SPY" in ret else None
    vix = macro_d.get("VIXCLS")
    if ret is not None and panels.get("close") is not None and "^VIX" in panels["close"]:
        vix = panels["close"]["^VIX"].dropna().combine_first(vix) if vix is not None else panels["close"]["^VIX"].dropna()
    base = bundle.get("ff3", bundle.get("ff5"))
    mkt, rf = base["Mkt-RF"] + base["RF"], base["RF"]
    macro_d["_stock_price"] = spliced_stock_price(sh, spy, mkt)
    extra = {"bonds": factor_bt.bond_returns_from_yields(macro_d.get("GS10"), cfg["backtest"]["bond_duration"])}
    for k, sym in cfg["backtest"]["trend_assets_monthly"].items():
        if ret is not None and sym in ret:
            extra[k] = _monthly(ret[sym])
    h = regime_bt.regime_history(macro_d, bundle.get("ind49"), mkt, cfg, bw, vix)
    payload = {"generated": utcnow(), "changes": {k: {"label": v["label"], "stock_only": bool(v.get("stock_only")),
                                                      "accounting": bool(v.get("accounting"))} for k, v in CHANGES.items()},
               "periods": {k: [a, b] for k, (a, b) in PERIODS.items()},
               "rule": "Keep a change only if it does not lower the factor-level Sharpe ratio in 1963-2010, when added to "
                       "v1 and when removed from v2 (or lowers it by at most 0.02 while cutting the worst drawdown by 2+ "
                       "points). Changes with no pre-2011 data are judged on the stock-level test and flagged."}
    payload["factor"] = factor_compare(cfg, bundle, h, extra)
    if ret is not None and ret.shape[1] > 200:
        payload["stock"] = stock_compare(cfg, f, cache, panels, h, rf)
    else:
        payload["stock"] = {"status": "unavailable", "reason": "stock price panel not cached"}
    write_json(out / "compare.json", payload)
    return ["compare.json"]
