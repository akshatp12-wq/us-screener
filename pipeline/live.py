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
    if sh is None or sh.empty:
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
    derived = sec.derive_all(store, cfg["signals"]["sue"]["min_quarters"])
    snap = sec.snapshot(derived, pd.Timestamp.today().normalize())
    cov = {c: int(snap[c].notna().sum()) for c in ("assets", "equity", "shares", "gross_profit_ttm", "net_income_ttm", "sue")} \
        if len(snap) else {}
    matched = int(univ["cik"].dropna().astype(int).isin(set(snap["cik"])).sum()) if len(snap) else 0
    log.info("fundamentals snapshot: %d companies, %d universe matches, non-null counts %s", len(snap), matched, cov)
    from screener.data.cache import record
    record("SEC point-in-time", snapshot_companies=int(len(snap)), universe_matches=matched, non_null=cov)
    return derived, snap


# ============================================================================= orchestration
@stage("stock signals")
def raw_stock_signals(cfg, univ, snap, panels, insiders, short):
    asof = panels["ret"].index[-1]
    return stk.compute_stock_signals(univ, snap, panels, asof, cfg, insiders=insiders, short=short)


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
                    "ag": r.get("asset_growth"), "iss": r.get("net_issuance"), "ins": r.get("insider_net"),
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
            "label": mc["label"], "summary": pc.summarize(port), "meta": meta, "turnover": tc,
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
        insiders = ms.safe(sec.load_insiders, f, cfg, cache, source="SEC Form 3/4/5")
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
