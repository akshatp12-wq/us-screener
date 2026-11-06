"""Unit tests for every signal, using small hand-built fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from screener.config import load_config  # noqa: E402

CFG = load_config()


# ----------------------------------------------------------------------------- A. macro
def test_one_sided_hp_has_no_lookahead():
    from screener.signals.macro import one_sided_hp
    rng = np.random.default_rng(0)
    s = pd.Series(np.cumsum(rng.normal(0, 1, 80)) + 100, index=pd.date_range("2000", periods=80, freq="QE"))
    full = one_sided_hp(s, 400_000, 20)
    trunc = one_sided_hp(s.iloc[:50], 400_000, 20)
    assert abs(full.iloc[49] - trunc.iloc[-1]) < 1e-8


def test_credit_gap_thresholds():
    from screener.signals.macro import credit_gap
    idx = pd.date_range("1980", periods=120, freq="QE")
    flat = pd.Series(100.0, index=idx)
    assert credit_gap(flat, CFG)["status"] == "green"
    boom = flat.copy()
    boom.iloc[-8:] = np.linspace(105, 140, 8)
    res = credit_gap(boom, CFG)
    assert res["status"] == "red" and res["value"] > 10
    assert credit_gap(None, CFG)["status"] == "unavailable"


def test_red_zone_red_when_credit_and_prices_both_hot():
    from screener.signals.macro import red_zone
    idx = pd.date_range("1960", periods=240, freq="QE")
    credit = pd.Series(np.linspace(50, 60, 240), index=idx)
    credit.iloc[-12:] += np.linspace(1, 15, 12)       # credit surge
    price = pd.Series(np.exp(np.linspace(0, 2, 240)), index=idx)
    price.iloc[-12:] *= np.linspace(1.05, 2.0, 12)    # price boom
    assert red_zone(credit, price, CFG, "t")["status"] == "red"
    calm = red_zone(pd.Series(np.linspace(50, 60, 240), index=idx), pd.Series(np.exp(np.linspace(0, 2, 240)), index=idx), CFG, "t")
    assert calm["status"] in ("green", "amber")


def test_em_probit_matches_published_table():
    from scipy.stats import norm
    from screener.signals.macro import fit_em_probit
    a, b = fit_em_probit(CFG["macro"]["yield_curve"]["em_table"])
    for p, s in CFG["macro"]["yield_curve"]["em_table"]:
        assert abs(norm.cdf(a + b * s) * 100 - p) < 1.5


def test_yield_curve_inversion_is_red():
    from screener.signals.macro import yield_curve
    idx = pd.bdate_range("2020-01-01", periods=300)
    assert yield_curve(pd.Series(-1.5, index=idx), CFG)["status"] == "red"
    assert yield_curve(pd.Series(2.0, index=idx), CFG)["status"] == "green"


def test_ebp_status_and_fallback():
    from screener.signals.macro import excess_bond_premium
    idx = pd.date_range("2000", periods=100, freq="ME")
    e = pd.DataFrame({"ebp": np.r_[np.zeros(99), 0.8], "est_prob": 0.3}, index=idx)
    assert excess_bond_premium(e, None, CFG)["status"] == "red"
    hy = pd.Series(np.r_[np.linspace(3, 5, 99), 9.0], index=idx)
    r = excess_bond_premium(None, hy, CFG)
    assert r["status"] == "red" and "fallback" in r["unit"]


def test_cape_percentile_and_expected_return():
    from screener.signals.macro import cape_dial
    idx = pd.date_range("1900", periods=1200, freq="ME")
    rng = np.random.default_rng(1)
    cape = pd.Series(15 + 5 * np.sin(np.arange(1200) / 60) + rng.normal(0, 0.5, 1200), index=idx)
    tr = pd.Series(np.exp(np.cumsum(0.005 - 0.002 * (cape - 15) / 5 + rng.normal(0, 0.03, 1200))), index=idx)
    sh = pd.DataFrame({"cape": cape, "real_tr_price": tr, "price": tr})
    r = cape_dial(sh, CFG)
    assert 0 <= r["percentile"] <= 1 and r["timing_signal"] is False
    assert r["expected_10y_real"]["low"] < r["expected_10y_real"]["median"] < r["expected_10y_real"]["high"]


# ----------------------------------------------------------------------------- B. sentiment
def test_sentiment_composite_euphoric_when_vix_collapses():
    from screener.signals.sentiment import sentiment_composite
    idx = pd.bdate_range("2010-01-01", periods=2000)
    vix = pd.Series(20.0 + np.random.default_rng(2).normal(0, 1, 2000), index=idx)
    vix.iloc[-60:] = 9.0
    r = sentiment_composite(vix, None, CFG)
    assert r["label"] == "euphoric" and r["status"] == "red"


def test_vrp_positive_when_implied_above_realised():
    from screener.signals.sentiment import variance_risk_premium
    idx = pd.bdate_range("2010-01-01", periods=600)
    vix = pd.Series(20.0, index=idx)
    ret = pd.Series(np.random.default_rng(3).normal(0, 0.005, 600), index=idx)  # ~8% vol << 20%
    r = variance_risk_premium(vix, ret, CFG)
    assert r["value"] > 0


# ----------------------------------------------------------------------------- C. bubbles, crash guard, trend
def test_bubble_thresholds_and_acceleration():
    from screener.signals.bubble import bubble_metrics
    idx = pd.bdate_range("2020-01-01", periods=600)
    mkt = pd.Series(0.0, index=idx)
    r = pd.Series(0.0, index=idx)
    r.iloc[-126:] = 0.0075          # all the gain in the last 6 months: (1.0075^126 - 1) ~ 157%
    m = bubble_metrics(r, mkt, CFG)
    assert m["status"] == "red" and "price acceleration" in m["warnings"]
    r2 = pd.Series(0.0012, index=idx)
    assert bubble_metrics(r2, mkt, CFG)["status"] == "green"


def test_crash_guard_on_in_bear_high_vol():
    from screener.signals.bubble import crash_guard
    rng = np.random.default_rng(4)
    calm = rng.normal(0.0004, 0.005, 1500)
    crash = np.tile([0.03, -0.035], 150)  # deterministic bear market with high volatility
    s = pd.Series(np.r_[calm, crash], index=pd.bdate_range("2005-01-01", periods=1800))
    g = crash_guard(s, CFG)
    assert g["on"] and g["momentum_multiplier"] == CFG["momentum_crash_guard"]["momentum_multiplier"]
    assert not crash_guard(pd.Series(calm, index=pd.bdate_range("2005-01-01", periods=1500)), CFG)["on"]


def test_trend_signals_up_and_down():
    from screener.signals.bubble import trend_signals
    idx = pd.bdate_range("2015-01-01", periods=600)
    ret = pd.DataFrame({"SPY": 0.001, "IEF": -0.001, "GLD": 0.0, "DBC": 0.0}, index=idx)
    t = trend_signals(ret, CFG, 0.0)
    assert t["stocks"]["signal"] == "up" and t["bonds"]["signal"] == "down"


def test_regime_label_rules():
    from screener.regime.label import regime_label
    assert regime_label("green", ["green", "green"], "neutral", 0.05, CFG)["label"] == "Calm"
    assert regime_label("green", ["green"], "euphoric", 0.05, CFG)["label"] == "Stretched"
    assert regime_label("green", ["green"], "neutral", 0.25, CFG)["label"] == "Stretched"
    assert regime_label("red", ["green"], "euphoric", 0.0, CFG)["label"] == "Fragile"
    assert regime_label("green", ["red"], "neutral", 0.30, CFG)["label"] == "Fragile"
    assert regime_label("red", ["green"], "neutral", 0.0, CFG)["label"] == "Stretched"   # v2: credit red alone -> Stretched, not Fragile


# ----------------------------------------------------------------------------- D. stock signals
def test_momentum_skips_latest_month():
    from screener.signals.stock import momentum_12_1
    idx = pd.date_range("2020-01-31", periods=14, freq="ME")
    m = pd.DataFrame({"A": [0.01] * 13 + [0.5], "B": [0.0] * 14}, index=idx)
    mom = momentum_12_1(m, idx[-1])
    assert abs(mom["A"] - (1.01 ** 11 - 1)) < 1e-9 and mom["B"] == 0


def test_vol_beta():
    from screener.signals.stock import vol_beta
    idx = pd.bdate_range("2020-01-01", periods=600)
    rng = np.random.default_rng(5)
    mkt = pd.Series(rng.normal(0, 0.01, 600), index=idx)
    ret = pd.DataFrame({"HI": 2 * mkt + rng.normal(0, 0.001, 600), "LO": 0.5 * mkt}, index=idx)
    vb = vol_beta(ret, mkt, idx[-1])
    assert abs(vb.loc["HI", "beta"] - 2) < 0.1 and abs(vb.loc["LO", "beta"] - 0.5) < 0.05
    assert vb.loc["HI", "vol"] > vb.loc["LO", "vol"]


def _ins(rows):
    df = pd.DataFrame(rows, columns=["issuer_cik", "owner_cik", "trans_date", "side", "value"])
    df["trans_date"] = pd.to_datetime(df["trans_date"])
    df["filed"] = df["trans_date"] + pd.Timedelta(days=2)
    return df


def test_insider_routine_vs_opportunistic():
    from screener.signals.stock import classify_insiders, insider_scores
    rows = []
    for y in (2021, 2022, 2023):
        rows.append((1, 10, f"{y}-03-10", -1, 1e5))   # routine: March every year
        rows.append((1, 11, f"{y}-0{2 + y % 3}-10", 1, 1e5))  # opportunistic: different months
    rows.append((1, 11, "2024-05-02", 1, 5e5))         # opportunistic buy
    rows.append((1, 10, "2024-03-10", -1, 5e5))        # routine sale (ignored)
    ins = _ins(rows)
    k = classify_insiders(ins, pd.Timestamp("2024-06-30")).set_index("owner_cik")["kind"]
    assert k[10] == "routine" and k[11] == "opportunistic"
    s = insider_scores(ins, pd.Timestamp("2024-06-30"), 180)
    assert s[1] == 1


def test_fundamental_signals_and_split_adjusted_issuance():
    from screener.signals.stock import compute_stock_signals
    idx = pd.bdate_range("2019-01-01", "2024-12-31")
    ret = pd.DataFrame({"AAA": 0.0005, "BBB": 0.0003, "SPY": 0.0004}, index=idx)
    close = pd.DataFrame({"AAA": 50.0, "BBB": 20.0, "SPY": 400.0}, index=idx)
    vol = pd.DataFrame(1e6, index=idx, columns=close.columns)
    splits = pd.DataFrame(1.0, index=idx, columns=close.columns)
    splits.loc[pd.Timestamp("2024-06-03"), "AAA"] = 2.0
    univ = pd.DataFrame({"ticker": ["AAA", "BBB"], "name": ["A", "B"], "sector": ["X", "X"], "cik": [1, 2]})
    fund = pd.DataFrame({
        "cik": [1, 2], "assets": [1000.0, 1000.0], "assets_1y": [800.0, 1000.0], "equity": [400.0, -5.0],
        # AAA: pre-split share count 1y ago (100) and post-split now (200) -> no issuance after adjustment
        "shares": [200.0, 50.0], "shares_1y": [100.0, 40.0],
        "shares_end": [pd.Timestamp("2024-09-30"), pd.Timestamp("2024-09-30")],
        "gross_profit_ttm": [300.0, 100.0], "net_income_ttm": [50.0, -10.0], "sue": [1.5, None],
        "revenue_ttm": [900.0, 300.0]})
    df = compute_stock_signals(univ, fund, {"ret": ret, "close": close, "volume": vol, "splits": splits},
                               idx[-1], CFG)
    assert abs(df.loc["AAA", "net_issuance"]) < 1e-9
    assert abs(df.loc["BBB", "net_issuance"] - np.log(50 / 40)) < 1e-9
    assert abs(df.loc["AAA", "gpa"] - 0.3) < 1e-9
    assert abs(df.loc["AAA", "asset_growth"] - 0.25) < 1e-9
    assert np.isnan(df.loc["BBB", "bm"])               # negative book equity excluded
    assert abs(df.loc["AAA", "bm"] - 400 / (50 * 200)) < 1e-9


def test_sec_quarterly_flows_derive_q4_and_ttm():
    from screener.data.sec import quarterly_flows, sue_series, ttm_series
    rows = []
    for y in range(2015, 2024):
        for q, (s, e) in enumerate([("01-01", "03-31"), ("04-01", "06-30"), ("07-01", "09-30")], start=1):
            rows.append(("revenue", f"{y}-{s}", f"{y}-{e}", 100.0 + q, f"{y}-{e[:2]}-28"))
        rows.append(("revenue", f"{y}-01-01", f"{y}-09-30", 306.0, f"{y}-10-28"))
        rows.append(("revenue", f"{y}-01-01", f"{y}-12-31", 410.0 + y - 2015, f"{y + 1}-02-20"))
    df = pd.DataFrame(rows, columns=["item", "start", "end", "val", "filed"])
    for c in ("start", "end", "filed"):
        df[c] = pd.to_datetime(df[c])
    df["cik"] = 1
    q = quarterly_flows(df, "revenue")
    q4 = q[q["end"] == pd.Timestamp("2016-12-31")].iloc[0]
    assert abs(q4["val"] - (411 - 306)) < 1e-9 and q4["avail"] == pd.Timestamp("2017-02-20")
    t = ttm_series(q)
    assert abs(t[t["end"] == pd.Timestamp("2016-12-31")]["val"].iloc[0] - 411) < 1e-9
    assert len(sue_series(q.assign(val=q["val"] / 100), 8)) > 0


def test_sec_store_with_empty_company_stays_typed():
    from screener.data.sec import derive_all, extract_facts, snapshot
    full = extract_facts({"cik": 7, "facts": {"us-gaap": {"Assets": {"units": {"USD": [
        {"end": "2025-06-30", "val": 110, "form": "10-Q", "filed": "2025-08-01"}]}}}}})
    store = pd.concat([extract_facts({"cik": 8, "facts": {}}), full], ignore_index=True)
    snap = snapshot(derive_all(store), pd.Timestamp("2025-10-01"))
    assert snap.set_index("cik").loc[7, "assets"] == 110


def test_sec_extract_facts_keeps_first_report():
    from screener.data.sec import extract_facts
    doc = {"cik": 5, "facts": {"us-gaap": {"Assets": {"units": {"USD": [
        {"end": "2020-12-31", "val": 100, "form": "10-K", "filed": "2021-02-20"},
        {"end": "2020-12-31", "val": 120, "form": "10-K/A", "filed": "2021-08-01"}]}}}}}
    f = extract_facts(doc)
    assert len(f) == 1 and f["val"].iloc[0] == 100


# ----------------------------------------------------------------------------- scoring, portfolio, scenarios
def _toy_frame(n=40):
    rng = np.random.default_rng(6)
    t = [f"T{i}" for i in range(n)]
    return pd.DataFrame({"sector": ["A"] * (n // 2) + ["B"] * (n // 2), "bm": rng.random(n), "ep": rng.random(n),
                         "momentum": rng.normal(0, 0.2, n), "gpa": rng.random(n), "opa": rng.random(n), "asset_growth": rng.normal(0.05, 0.1, n),
                         "net_issuance": rng.normal(0, 0.05, n), "young": [False] * (n - 2) + [True] * 2,
                         "insider_net": rng.integers(-1, 2, n).astype(float), "si_ratio": rng.random(n) * 0.3,
                         "days_to_cover": rng.random(n) * 10, "sue": rng.normal(0, 1, n), "vol": rng.random(n),
                         "beta": 0.5 + rng.random(n), "mcap": rng.random(n) * 1e10, "net_income_ttm": rng.normal(1, 2, n),
                         "price": 20.0, "dollar_volume": 1e8}, index=t)


def test_scores_are_within_sector_and_bounded():
    from screener.scoring.composite import composite, signal_scores
    df = _toy_frame()
    sc = signal_scores(df, CFG)
    assert sc.drop(columns=["issuance"]).max().max() <= 1.0 and sc.min().min() >= 0
    assert abs(sc.loc[df["sector"] == "A", "momentum"].max() - 1.0) < 1e-9
    c = composite(sc, CFG["modes"]["long_term"]["weights"])
    assert c.notna().all()
    partial = sc.copy()
    partial.loc[:, ["value", "profitability", "investment"]] = np.nan
    assert composite(partial, CFG["modes"]["long_term"]["weights"]).isna().all()   # < 60% weight coverage


def test_euphoric_penalty_and_flags():
    from screener.scoring.composite import euphoric_penalty, flags
    df = _toy_frame()
    p = euphoric_penalty(df, CFG)
    assert (p >= 0).all() and p.max() > 0
    df.loc["T0", ["si_ratio", "days_to_cover"]] = [0.3, 12]
    assert "squeeze risk" in flags(df, CFG)["T0"]


def test_portfolio_sector_cap_and_beta_neutral():
    from screener.portfolio.construct import market_neutral, select
    df = _toy_frame(80)
    score = pd.Series(np.linspace(1, 0, 80), index=df.index)
    names = select(score, df, 20, CFG)
    assert df.loc[names, "sector"].value_counts().max() <= int(CFG["portfolio"]["sector_cap"] * 20)
    p = market_neutral(score, df, CFG, CFG["modes"]["market_neutral"], "neutral")
    assert abs((p["weight"] * p["beta"]).sum()) < 1e-9


def test_scenario_probabilities_sum_to_one():
    from screener.scenarios.engine import scenario_probs
    r = scenario_probs(CFG, {"yield_curve": {"value": 60.0}, "ebp": {"status": "red"}},
                       {"label": "Fragile", "euphoric": True})
    assert abs(sum(r["probs"].values()) - 1) < 1e-9 and r["probs"]["recession"] > CFG["scenarios"]["base_probs"]["recession"]


# ----------------------------------------------------------------------------- parsers and metrics
def test_french_parser_multiple_tables():
    from screener.data.macro_sources import parse_french_csv
    txt = ("header\n\n  Value Weight Returns -- Monthly\n,Lo 20,Hi 20\n202001,  1.00, -2.00\n202002, -99.99,  3.00\n\n"
           "  Equal Weight Returns -- Monthly\n,Lo 20,Hi 20\n202001,  5.00, 6.00\n")
    t = parse_french_csv(txt)
    vw = [v for k, v in t.items() if "Value Weight" in k][0]
    assert vw.loc["2020-01-31", "Lo 20"] == 1.0 and np.isnan(vw.loc["2020-02-29", "Lo 20"])


def test_finra_and_form_index_parsers():
    from screener.data.market_sources import parse_finra
    from screener.data.sec import parse_form_index
    f = parse_finra(b"symbolCode|currentShortPositionQuantity|daysToCoverQuantity\nBRK.B|100|2.5\nXYZ|50|1\n")
    assert set(f["ticker"]) == {"BRK-B", "XYZ"}
    idx = "Form Type   Company Name   CIK   Date Filed   File Name\n-----\n10-Q        Acme Corp     320193    20240101    x.txt\n8-K         Other  1  20240101  y\n"
    assert parse_form_index(idx) == {320193}


def test_metrics():
    from screener.backtest.metrics import auc, mp_split, newey_west_t
    rng = np.random.default_rng(7)
    x = pd.Series(rng.normal(0.01, 0.02, 400), index=pd.date_range("1990", periods=400, freq="ME"))
    assert newey_west_t(x) > 3
    lab = pd.Series([0, 0, 1, 1])
    assert auc(pd.Series([0.1, 0.2, 0.8, 0.9]), lab) == 1.0
    sp = mp_split(x, "2010-01-01")
    assert sp["pre"]["n"] + sp["post"]["n"] == 400


# ----------------------------------------------------------------------------- v2 additions
def test_regime_credit_alone_label():
    from screener.backtest.regime_bt import relabel
    from screener.regime.label import regime_label
    h = pd.DataFrame({"credit_red": [True, True, False, False], "stretched": [True, False, True, False]})
    v1 = dict(CFG, regime={**CFG["regime"], "credit_alone_label": "Calm"})
    assert relabel(h, v1)["label"].tolist() == ["Fragile", "Calm", "Stretched", "Calm"]
    assert relabel(h, CFG)["label"].tolist() == ["Fragile", "Stretched", "Stretched", "Calm"]
    assert regime_label("red", ["green"], "neutral", 0.0, CFG)["label"] == "Stretched"
    assert regime_label("red", ["green"], "neutral", 0.0, v1)["label"] == "Calm"


def test_trend_switch_multiplier():
    from screener.backtest.factor_bt import trend_switch_multiplier
    idx = pd.date_range("2000-01-31", periods=24, freq="ME")
    r = pd.Series([0.02] * 14 + [-0.08] * 4 + [0.06] * 6, index=idx)
    m = trend_switch_multiplier(r, CFG["modes"]["absolute_return"])
    assert m.iloc[12] == 1.0 and m.iloc[16] == 0.5
    off = trend_switch_multiplier(r, {"trend_switch": {"enabled": False}})
    assert (off == 1.0).all()


def test_release_dates_move_sue_earlier():
    from screener.data.sec import parse_submissions, release_avail
    doc = {"filings": {"recent": {"form": ["8-K", "8-K", "10-Q"], "filingDate": ["2024-04-25", "2024-05-20", "2024-05-08"],
                                  "items": ["2.02,9.01", "5.02", ""]}, "files": []}}
    dates, _ = parse_submissions(doc)
    assert dates == ["2024-04-25"]
    ends = np.array(["2024-03-31", "2024-06-30"], dtype="datetime64[ns]")
    av = np.array(["2024-05-08", "2024-08-05"], dtype="datetime64[ns]")
    out = release_avail(ends, av, np.array(dates, dtype="datetime64[ns]"))
    assert out[0] == np.datetime64("2024-04-25") and out[1] == av[1]


def test_cik_alias_merge():
    from screener.data.sec import apply_aliases
    df = pd.DataFrame({"cik": [34088, 2115436, 5]})
    assert apply_aliases(df, "cik", CFG)["cik"].tolist() == [2115436, 2115436, 5]


def test_pit_membership():
    from screener.data.membership import members_fn
    ch = pd.DataFrame({"date": pd.to_datetime(["2020-06-22", "2015-03-02"]), "added": ["NEW1", "NEW2"],
                       "removed": ["OLD1", "OLD2"]})
    f = members_fn({"NEW1", "NEW2", "KEEP"}, ch)
    assert f("2021-01-31") == {"NEW1", "NEW2", "KEEP"}
    assert f("2018-01-31") == {"OLD1", "NEW2", "KEEP"}
    assert f("2016-01-31") == {"OLD1", "NEW2", "KEEP"}
    assert f("2015-02-27") is None  # before the change log starts: no membership filter


def test_buy_watch_states():
    from screener.signals.buywatch import buy_watch
    idx = pd.bdate_range("2018-01-01", "2021-03-31")
    n = len(idx)
    # steady rise, a 30% fall in early 2020, then recovery
    px = np.linspace(100, 160, n)
    fall = (idx >= "2020-02-20") & (idx <= "2020-03-23")
    px[fall] = np.linspace(160, 110, fall.sum())
    after = idx > "2020-03-23"
    px[after] = np.linspace(112, 175, after.sum())
    r = pd.Series(px, index=idx).pct_change()
    cut = r.loc[:"2020-04-30"]
    assert buy_watch(cut, CFG)["state"] == "Watching"
    mid = r.loc[:"2020-07-31"]
    res = buy_watch(mid, CFG)
    assert res["state"] == "Buy signal", res
    assert buy_watch(r.loc[:"2019-12-31"], CFG)["state"] == "No setup"


# ----------------------------------------------------------------------------- lead indicators
def test_mispricing_flags_the_cheap_stock():
    from screener.signals.leads import mispricing
    rng = np.random.default_rng(1)
    n = 300
    a = rng.uniform(1e9, 5e10, n)
    eq = a * rng.uniform(0.2, 0.6, n)
    ni = a * rng.uniform(-0.02, 0.12, n)
    mcap = 1.5 * eq + 12 * ni.clip(0) + a * 0.1 * np.exp(rng.normal(0, 0.05, n))
    df = pd.DataFrame({"assets": a, "equity": eq, "net_income_ttm": ni, "revenue_ttm": a * 0.8, "gross_profit_ttm": a * 0.3,
                       "operating_income_ttm": a * 0.1, "mcap": mcap, "sector": ["A", "B", "C"] * 100},
                      index=[f"S{i}" for i in range(n)])
    df.loc["S7", "mcap"] *= 0.4          # priced at 40% of what fundamentals imply
    df.loc["S8", "mcap"] *= 2.5          # priced well above
    g = mispricing(df, 0.01, 100)
    assert g.idxmax() == "S7" and g["S7"] > 0.5
    assert g["S8"] < -0.4


def test_residual_momentum_ranks_idiosyncratic_winner_first():
    from screener.signals.leads import residual_momentum
    rng = np.random.default_rng(2)
    idx = pd.date_range("2015-01-31", periods=40, freq="ME")
    f = pd.DataFrame({"mkt": rng.normal(0.008, 0.04, 40), "smb": rng.normal(0, 0.02, 40),
                      "hml": rng.normal(0, 0.02, 40), "rf": 0.001}, index=idx)
    r = pd.DataFrame({f"S{i}": 0.001 + 1.0 * f["mkt"] + rng.normal(0, 0.03, 40) for i in range(30)})
    r.index = idx
    r.iloc[-12:-1, 3] += 0.03             # steady stock-specific gains in the formation window
    r.iloc[-12:-1, 4] += f["mkt"].iloc[-12:-1] * 2   # pure market beta, should not count as residual
    s = residual_momentum(r, f, idx[-1], 36, 12, 1)
    assert s.idxmax() == "S3"
    assert s.rank(pct=True)["S3"] > s.rank(pct=True)["S4"]


def test_insider_buying_flags_net_opportunistic_buyers():
    from screener.signals.leads import flags
    idx = [f"S{i}" for i in range(60)]
    L = pd.DataFrame({"insider_buying": [0.0] * 60}, index=idx)
    L.loc["S1", "insider_buying"] = 2.0
    L.loc["S2", "insider_buying"] = 1.0
    L.loc["S3", "insider_buying"] = -1.0
    F = flags(L, pd.Series(True, index=idx), CFG)
    assert set(F["insider_buying"]["bull"]) == {"S1", "S2"}


def test_etf_universe_and_ranking():
    from screener.signals.leads import etf_monthly, etf_universe, residual_momentum
    u = etf_universe(CFG)
    assert "XLK" in u and "SPY" not in u and "TLT" not in u
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2019-01-01", "2023-06-30")
    ret = pd.DataFrame({t: rng.normal(0.0003, 0.01, len(idx)) for t in u[:12]}, index=idx)
    ret.loc[ret.index[-260]:ret.index[-25], u[0]] += 0.002     # persistent industry-specific strength
    m = etf_monthly(ret, u[:12])
    f = pd.DataFrame({"mkt": m.mean(axis=1), "rf": 0.0}, index=m.index)
    s = residual_momentum(m, f, m.index[-1], 36, 12, 1)
    assert s.idxmax() == u[0]


def test_earnings_reaction_measures_the_announcement_move():
    from screener.signals.leads import earnings_reactions, latest_reaction
    idx = pd.bdate_range("2024-01-01", periods=120)
    ret = pd.DataFrame({"AAA": 0.0, "BBB": 0.0}, index=idx)
    mkt = pd.Series(0.0, index=idx)
    day0 = idx[60]
    ret.loc[day0, "AAA"] = 0.08
    ret.loc[day0, "BBB"] = -0.05
    tab = earnings_reactions(ret, mkt, {1: np.array([day0], dtype="datetime64[ns]"), 2: np.array([day0], dtype="datetime64[ns]")},
                             {"AAA": 1, "BBB": 2}, 1)
    lr = latest_reaction(tab, idx[70], 75)
    assert abs(lr.loc["AAA", "ear"] - 0.08) < 1e-9 and abs(lr.loc["BBB", "ear"] + 0.05) < 1e-9
    assert latest_reaction(tab, idx[60], 75).empty   # window not closed yet on the announcement day


def test_abnormal_volume_detects_spike():
    from screener.signals.leads import abnormal_volume
    idx = pd.bdate_range("2024-01-01", periods=80)
    v = pd.DataFrame({"AAA": 1e6, "BBB": 1e6}, index=idx)
    v.iloc[-5:, 0] = 3e6
    a = abnormal_volume(v, idx[-1], 5, 50)
    assert abs(a["AAA"] - np.log(3)) < 1e-9 and abs(a["BBB"]) < 1e-9


def test_lead_status_rules():
    from screener.backtest.leads_bt import lead_status
    mk = lambda pre, post, t, n=60: {"pre": {"n": n, "ann_mean": pre}, "post": {"n": 150, "ann_mean": post}, "full": {"n": 150 + n, "t": t}}
    assert lead_status(mk(0.04, 0.03, 3.4), CFG)["status"] == "Validated"
    assert lead_status(mk(0.04, 0.03, 2.3), CFG)["status"] == "Promising"
    assert lead_status(mk(-0.02, 0.05, 2.5), CFG)["status"] == "Unproven"
    assert lead_status(mk(0.0, 0.05, 3.5, n=0), CFG)["status"] == "Promising"   # no pre-2011 data caps it


def test_signal_start_dates():
    from screener.signals.leads import insider_signal_dates, month_end_streak, volume_streak_start
    # insider: an owner with 3 years of history in May (routine) and two opportunistic buyers
    rows = []
    for y, m10, m11 in ((2021, "02", "03"), (2022, "06", "07"), (2023, "09", "11")):
        rows += [(1, 9, f"{y}-05-10", f"{y}-05-12", -1, 1e5), (1, 10, f"{y}-{m10}-10", f"{y}-{m10}-12", -1, 1e5),
                 (1, 11, f"{y}-{m11}-10", f"{y}-{m11}-12", -1, 1e5)]
    rows += [(1, 10, "2024-04-01", "2024-04-03", 1, 2e5), (1, 11, "2024-05-20", "2024-05-22", 1, 1e5),
             (1, 9, "2024-05-10", "2024-05-12", 1, 9e5)]
    ins = pd.DataFrame(rows, columns=["issuer_cik", "owner_cik", "trans_date", "filed", "side", "value"])
    ins["trans_date"], ins["filed"] = pd.to_datetime(ins["trans_date"]), pd.to_datetime(ins["filed"])
    d = insider_signal_dates(ins, pd.Timestamp("2024-06-28"), [1], 180, 1)
    assert d[1] == pd.Timestamp("2024-04-03")   # owner 9 trades every May (routine) and is ignored
    # volume streak
    idx = pd.bdate_range("2024-01-01", periods=90)
    v = pd.DataFrame({"AAA": 1e6}, index=idx)
    v.iloc[-4:, 0] = 5e6
    s = volume_streak_start(v, ["AAA"], 0.3, idx[-1], 5, 50)
    assert s["AAA"] >= idx[-4] and s["AAA"] <= idx[-2]
    # month-end streak
    h = {"2024-03-31": {"resid_mom": ["X"]}, "2024-04-30": {"resid_mom": ["Y"]}, "2024-05-31": {"resid_mom": ["X", "Y"]},
         "2024-06-30": {"resid_mom": ["X", "Y"]}}
    assert month_end_streak(h, "resid_mom", "Y", list(h)) == pd.Timestamp("2024-04-30")
    assert month_end_streak(h, "resid_mom", "X", list(h)) == pd.Timestamp("2024-05-31")
    assert month_end_streak(h, "resid_mom", "Z", list(h)) is None


def test_insider_bearish_side_and_selling_dates():
    from screener.signals.leads import flags, insider_signal_dates
    idx = [f"S{i}" for i in range(40)]
    L = pd.DataFrame({"insider_buying": [0.0] * 40}, index=idx)
    L.loc["S1", "insider_buying"], L.loc["S2", "insider_buying"], L.loc["S3", "insider_buying"] = 1.0, -1.0, -3.0
    F = flags(L, pd.Series(True, index=idx), CFG)
    assert set(F["insider_buying"]["bull"]) == {"S1"} and set(F["insider_buying"]["bear"]) == {"S2", "S3"}
    rows = []
    for y, m in ((2021, "02"), (2022, "06"), (2023, "09")):
        rows.append((1, 10, f"{y}-{m}-10", f"{y}-{m}-12", 1, 1e5))
    rows.append((1, 10, "2024-05-01", "2024-05-03", -1, 4e5))
    ins = pd.DataFrame(rows, columns=["issuer_cik", "owner_cik", "trans_date", "filed", "side", "value"])
    ins["trans_date"], ins["filed"] = pd.to_datetime(ins["trans_date"]), pd.to_datetime(ins["filed"])
    assert insider_signal_dates(ins, pd.Timestamp("2024-06-28"), [1], 180, 1, -1)[1] == pd.Timestamp("2024-05-03")
    assert 1 not in insider_signal_dates(ins, pd.Timestamp("2024-06-28"), [1], 180, 1, 1)
