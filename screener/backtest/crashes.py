"""Crash replay and buy-timing study on the monthly research panel.

1. Bear-market replay: find every month-end drawdown of the US market >= threshold since 1960 and show
   what the dials said 12 and 6 months before the peak, at the peak and at the trough, how much warning
   each dial gave, and how each strategy mode did through the episode.
2. Buy timing: test simple, pre-registered buy rules and a composite "buy score". Parameters are chosen
   on 1960-1999 only and judged on 2000 onward (out of sample). Every number uses information
   available at the month end (dials are already lagged for publication in the panel).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..util import expanding_pct
from .metrics import newey_west_t

SPLIT = pd.Timestamp("1999-12-31")
MODES = ("long_term", "market_neutral", "absolute_return")


# ----------------------------------------------------------------------------- helpers
def _index(r: pd.Series) -> pd.Series:
    return (1 + r.fillna(0)).cumprod()


def _cum(r: pd.Series) -> float:
    r = r.dropna()
    return float(np.expm1(np.log1p(r).sum())) if len(r) else float("nan")


def features(p: pd.DataFrame) -> pd.DataFrame:
    """Real-time features at each month end."""
    f = pd.DataFrame(index=p.index)
    px = _index(p["market"])
    f["px"] = px
    f["dd_ath"] = px / px.cummax() - 1
    f["dd_12m"] = px / px.rolling(12, min_periods=1).max() - 1
    f["ret_12m"] = px / px.shift(12) - 1
    sma = px.rolling(10).mean()
    f["trend_up"] = (px > sma).astype(float)
    f["trend_turn"] = ((px > sma) & (px.shift(1) <= sma.shift(1))).astype(float)
    if "baa_spread" in p:
        f["baa_spread"] = p["baa_spread"]
        f["baa_chg3"] = p["baa_spread"] - p["baa_spread"].shift(3)
        f["baa_pct"] = expanding_pct(p["baa_spread"], 60)
    if "sentiment_z" in p:
        f["sent_z"] = p["sentiment_z"]
    if "vix" in p:
        f["vix"] = p["vix"]
    if "cape" in p:
        f["cape_pct"] = expanding_pct(p["cape"].dropna(), 120).reindex(p.index)
    if "recession_prob" in p:
        f["rec_prob"] = p["recession_prob"]
    if "credit_gap" in p:
        f["credit_gap"] = p["credit_gap"]
    for c in ("label", "credit_red", "bubble_share", "sentiment", "vrp", "ebp"):
        if c in p:
            f[c] = p[c]
    # forward outcomes (used only as targets)
    ex = np.log1p(p["market"]) - np.log1p(p["rf"].fillna(0))
    f["fwd12"] = np.expm1(ex[::-1].rolling(12).sum()[::-1].shift(-1))
    f["fwd24"] = np.expm1(ex[::-1].rolling(24).sum()[::-1].shift(-1))
    return f


# ----------------------------------------------------------------------------- 1. bear markets
def bear_markets(market: pd.Series, threshold: float = 0.15, start: str = "1960-01-01") -> list[dict]:
    px = _index(market[market.index >= pd.Timestamp(start) - pd.DateOffset(years=2)])
    eps, peak_d, peak_v, i = [], px.index[0], px.iloc[0], 0
    dates = px.index
    while i < len(px):
        d, v = dates[i], px.iloc[i]
        if v >= peak_v:
            peak_d, peak_v = d, v
            i += 1
            continue
        if v / peak_v - 1 <= -threshold:
            # find trough and recovery
            j = i
            while j < len(px) and px.iloc[j] < peak_v:
                j += 1
            seg = px.loc[peak_d:dates[j - 1]]
            trough_d = seg.idxmin()
            rec_d = dates[j] if j < len(px) else None
            if peak_d >= pd.Timestamp(start):
                eps.append({"peak": peak_d, "trough": trough_d, "recovery": rec_d,
                            "depth": float(seg.min() / peak_v - 1),
                            "months_down": int(len(px.loc[peak_d:trough_d]) - 1),
                            "months_to_recover": int(len(px.loc[trough_d:rec_d]) - 1) if rec_d is not None else None})
            if j >= len(px):
                break
            peak_d, peak_v = dates[j], px.iloc[j]
            i = j + 1
            continue
        i += 1
    return eps


def _snap(f: pd.DataFrame, d: pd.Timestamp) -> dict:
    if d not in f.index:
        prior = f.index[f.index <= d]
        if not len(prior):
            return {}
        d = prior[-1]
    r = f.loc[d]
    keys = ("label", "credit_gap", "credit_red", "rec_prob", "baa_spread", "ebp", "sent_z", "sentiment",
            "cape_pct", "bubble_share", "vix", "trend_up", "dd_ath")
    return {k: (r[k] if not (isinstance(r[k], float) and np.isnan(r[k])) else None) for k in keys if k in r.index}


WARNINGS = {
    "Regime not Calm": lambda f: f["label"].isin(["Stretched", "Fragile"]),
    "Regime Fragile": lambda f: f["label"].eq("Fragile"),
    "Yield curve > 40% recession odds": lambda f: f["rec_prob"] > 0.40,
    "Credit dial red": lambda f: f["credit_red"].fillna(False).astype(bool),
    "Sentiment euphoric": lambda f: f["sent_z"] > 1.0,
    "CAPE >= 90th pct": lambda f: f["cape_pct"] >= 0.90,
    "Credit spreads widening (Baa +0.5pp in 3m)": lambda f: f["baa_chg3"] >= 0.5,
}


def warning_lead(f: pd.DataFrame, peak: pd.Timestamp, lookback: int = 24, active_within: int = 3) -> dict:
    """For each warning still active going into the peak (on at some point in the last `active_within`
    months up to the peak): months between the start of that run and the peak. None = no live warning."""
    out = {}
    win = f.loc[peak - pd.DateOffset(months=lookback):peak]
    for name, fn in WARNINGS.items():
        try:
            on = fn(win).fillna(False).astype(bool)
        except KeyError:
            continue
        recent = on[on.index >= peak - pd.DateOffset(months=active_within)]
        if not recent.any():
            out[name] = None
            continue
        last_on = recent[recent].index[-1]
        run_start = last_on
        for d in reversed(on.index[on.index <= last_on]):
            if not on[d]:
                break
            run_start = d
        out[name] = int(round((peak - run_start).days / 30.44))
    return out


def trend_exits(f: pd.DataFrame, eps: list[dict]) -> list[dict]:
    """Trend rule as an exit/re-entry signal: when it would have sold after each peak and bought back
    after each trough, and what that saved or cost versus holding."""
    out = []
    for e in eps:
        pk, tr = e["peak"], e["trough"]
        after = f.loc[pk:]
        exit_d = after.index[(after["trend_up"] == 0)]
        exit_d = exit_d[0] if len(exit_d) else None
        post = f.loc[tr:]
        re_d = post.index[(post["trend_up"] == 1)]
        re_d = re_d[0] if len(re_d) else None
        row = {"peak": pk.strftime("%Y-%m")}
        if exit_d is not None:
            row["exit_months_after_peak"] = int(round((exit_d - pk).days / 30.44))
            row["drawdown_at_exit"] = float(f.loc[exit_d, "px"] / f.loc[pk, "px"] - 1)
        if re_d is not None:
            row["reentry_months_after_trough"] = int(round((re_d - tr).days / 30.44))
            row["above_trough_at_reentry"] = float(f.loc[re_d, "px"] / f.loc[tr, "px"] - 1)
        if exit_d is not None and re_d is not None and re_d > exit_d:
            row["loss_avoided_vs_hold"] = float(1 - f.loc[re_d, "px"] / f.loc[exit_d, "px"])
        out.append(row)
    return out


def false_alarm_rate(f: pd.DataFrame, eps: list[dict], lookahead: int = 24) -> dict:
    """Share of months a warning was on that were NOT followed by a bear-market peak within `lookahead` months."""
    peaks = [e["peak"] for e in eps]
    allm = f.index[(f.index >= "1960-01-01") & (f.index <= f.index[-1] - pd.DateOffset(months=lookahead))]
    base = float(np.mean([any(0 <= (pk - d).days / 30.44 <= lookahead for pk in peaks) for d in allm]))
    out = {"_base_rate": base}
    for name, fn in WARNINGS.items():
        try:
            on = fn(f).fillna(False)
        except KeyError:
            continue
        on = on[(on.index >= "1960-01-01") & (on.index <= f.index[-1] - pd.DateOffset(months=lookahead))]
        if not on.any():
            continue
        hits = [any(0 <= (pk - d).days / 30.44 <= lookahead for pk in peaks) for d in on[on].index]
        out[name] = {"months_on": int(on.sum()), "share_on": float(on.mean()),
                     "followed_by_bear_within_24m": float(np.mean(hits))}
    return out


def crash_replay(p: pd.DataFrame, threshold: float = 0.15) -> dict:
    f = features(p)
    eps = bear_markets(p["market"], threshold)
    rows = []
    for e in eps:
        pk, tr, rc = e["peak"], e["trough"], e["recovery"]
        perf = {}
        for m in ("market",) + MODES:
            if m not in p:
                continue
            r = p[m]
            down = _cum(r.loc[pk + pd.offsets.MonthEnd(1):tr])
            full = _cum(r.loc[pk + pd.offsets.MonthEnd(1):rc]) if rc is not None else None
            seg = _index(r.loc[pk + pd.offsets.MonthEnd(1):(rc or r.index[-1])])
            perf[m] = {"peak_to_trough": down, "peak_to_market_recovery": full,
                       "max_dd_in_window": float((seg / seg.cummax().clip(lower=1.0) - 1).min()) if len(seg) else None}
        after = {m: _cum(p[m].loc[tr + pd.offsets.MonthEnd(1):tr + pd.DateOffset(months=12)]) for m in ("market",) + MODES if m in p}
        rows.append({**{k: (v.strftime("%Y-%m") if isinstance(v, pd.Timestamp) else v) for k, v in e.items()},
                     "dials_12m_before": _snap(f, pk - pd.DateOffset(months=12)),
                     "dials_6m_before": _snap(f, pk - pd.DateOffset(months=6)),
                     "dials_at_peak": _snap(f, pk), "dials_at_trough": _snap(f, tr),
                     "warning_lead_months": warning_lead(f, pk), "performance": perf, "first_12m_after_trough": after})
    return {"threshold": threshold, "episodes": rows, "false_alarms": false_alarm_rate(f, eps),
            "trend_exits": trend_exits(f, eps),
            "note": "Month-end total-return data (Ken French CRSP market), so intra-month extremes such as Oct 1987 "
                    "and Mar 2020 are understated. Dials are as known at each month end."}


# ----------------------------------------------------------------------------- 2. buy timing
def _dedupe(sig: pd.Series, gap: int = 12) -> pd.Series:
    """Keep the first trigger, then ignore new triggers for `gap` months (independent episodes)."""
    out = pd.Series(False, index=sig.index)
    last = None
    for d in sig.index[sig.fillna(False).astype(bool)]:
        if last is None or (d - last).days / 30.44 >= gap:
            out[d] = True
            last = d
    return out


def _trough_distance(f: pd.DataFrame, d: pd.Timestamp, eps: list[dict]) -> tuple[float | None, float | None]:
    for e in eps:
        end = e["recovery"] or f.index[-1]
        if e["peak"] <= d <= end:
            months = (d - e["trough"]).days / 30.44
            above = f.loc[d, "px"] / f.loc[e["trough"], "px"] - 1
            return round(months, 1), float(above)
    return None, None


def evaluate_rule(f: pd.DataFrame, sig: pd.Series, eps: list[dict], period=None) -> dict:
    s = _dedupe(sig)
    if period is not None:
        s = s[(s.index >= period[0]) & (s.index <= period[1])]
    trig = s[s].index
    if len(trig) == 0:
        return {"n": 0}
    r12, r24 = f.loc[trig, "fwd12"], f.loc[trig, "fwd24"]
    dist = [_trough_distance(f, d, eps) for d in trig]
    months = [x for x, _ in dist if x is not None]
    above = [y for _, y in dist if y is not None]
    base = f["fwd12"] if period is None else f.loc[period[0]:period[1], "fwd12"]
    base24 = f["fwd24"] if period is None else f.loc[period[0]:period[1], "fwd24"]
    return {"n": int(len(trig)), "dates": [d.strftime("%Y-%m") for d in trig],
            "fwd12_mean": float(r12.mean()), "fwd24_mean": float(r24.mean()),
            "fwd12_hit": float((r12 > 0).mean()), "fwd12_worst": float(r12.min()),
            "uncond_fwd12": float(base.mean()), "uncond_fwd24": float(base24.mean()),
            "edge12": float(r12.mean() - base.mean()), "edge24": float(r24.mean() - base24.mean()),
            "months_from_trough_median": float(np.median(months)) if months else None,
            "pct_above_trough_median": float(np.median(above)) if above else None,
            "in_bear_share": float(len(months) / len(trig))}


def rule_grid(f: pd.DataFrame) -> dict[str, pd.Series]:
    rules = {}
    for x in (0.10, 0.15, 0.20, 0.25, 0.30):
        dd = f["dd_ath"] <= -x
        k = int(x * 100)
        rules[f"Drawdown >= {k}%"] = dd
        rules[f"Drawdown >= {k}% then trend turns up"] = (f["dd_ath"].rolling(12).min() <= -x) & (f["trend_turn"] == 1)
        if "sent_z" in f:
            rules[f"Drawdown >= {k}% and sentiment fearful"] = dd & (f["sent_z"] < -1)
        if "baa_chg3" in f:
            rules[f"Drawdown >= {k}% and credit spreads past peak"] = dd & (f["baa_chg3"] < 0) & (f["baa_pct"] >= 0.7)
    if "vix" in f:
        rules["VIX >= 35"] = f["vix"] >= 35
        rules["VIX >= 35 then VIX < 25"] = (f["vix"].rolling(6).max() >= 35) & (f["vix"] < 25)
    if "sent_z" in f:
        rules["Sentiment fearful (z < -1)"] = f["sent_z"] < -1
    if "baa_pct" in f:
        rules["Credit spread >= 90th pct"] = f["baa_pct"] >= 0.90
    rules["Trend turns up (any)"] = f["trend_turn"] == 1
    return rules


def buy_timing(p: pd.DataFrame, threshold: float = 0.15) -> dict:
    f = features(p)
    f = f[f.index >= "1960-01-01"]
    eps = bear_markets(p["market"], threshold)
    rules = rule_grid(f)
    ins = (pd.Timestamp("1960-01-01"), SPLIT)
    oos = (SPLIT + pd.offsets.MonthEnd(1), f.index[-1])
    table = []
    for name, sig in rules.items():
        table.append({"rule": name, "in_sample": evaluate_rule(f, sig, eps, ins),
                      "out_of_sample": evaluate_rule(f, sig, eps, oos), "full": evaluate_rule(f, sig, eps)})
    # choose on in-sample only: best 24m edge among rules with >= 5 independent triggers in 1960-1999
    cands = [t for t in table if t["in_sample"].get("n", 0) >= 5]
    best = max(cands, key=lambda t: t["in_sample"]["edge24"]) if cands else None
    for t in table:  # positive edge in both halves, with at least 3 signals in each
        a, o = t["in_sample"], t["out_of_sample"]
        t["consistent"] = bool(a.get("n", 0) >= 3 and o.get("n", 0) >= 3 and a.get("edge24", -1) > 0 and o.get("edge24", -1) > 0)
    dep_rules = [r for r in ("Drawdown >= 20%", "Drawdown >= 20% then trend turns up", "Drawdown >= 15% then trend turns up",
                             "Credit spread >= 90th pct") if r in rules]
    staged = (f["dd_ath"] <= -0.10)  # staged: a third of the reserve at -10%, -20%, -30%
    deps = [deployment(p, f, rules[r], r) for r in dep_rules]
    deps.append(deployment(p, f, staged, "Staged: a third of the reserve at -10%, -20%, -30%",
                           stages=(-0.10, -0.20, -0.30)))
    last = f.dropna(subset=["dd_ath"]).iloc[-1]
    current = {"date": f.index[-1].strftime("%Y-%m"), "drawdown_from_high": float(last["dd_ath"]),
               "trend_up": bool(last["trend_up"]), "credit_spread_pct": float(last["baa_pct"]) if "baa_pct" in f and pd.notna(last.get("baa_pct")) else None,
               "sentiment_z": float(last["sent_z"]) if "sent_z" in f and pd.notna(last.get("sent_z")) else None,
               "active_rules": [n for n, s in rules.items() if bool(s.reindex(f.index).fillna(False).iloc[-1])]}
    return {"split": SPLIT.strftime("%Y-%m"), "rules": table,
            "chosen_in_sample": best["rule"] if best else None,
            "chosen_out_of_sample": best["out_of_sample"] if best else None,
            "univariate": univariate(f), "composite": composite_score(f), "current": current,
            "deployment": deps[0] if deps else {}, "deployments": deps,
            "note": "Rules chosen on 1960-1999 only (best 24-month edge with >= 5 independent triggers); "
                    "2000-2026 is out of sample. Triggers are de-duplicated to one per 12 months."}


def univariate(f: pd.DataFrame) -> dict:
    out = {}
    for c in ("dd_ath", "dd_12m", "ret_12m", "sent_z", "baa_spread", "baa_chg3", "vix", "cape_pct", "rec_prob", "credit_gap"):
        if c not in f or f[c].notna().sum() < 120:
            continue
        res = {}
        for lab, (a, b) in (("1960-1999", ("1960-01-01", SPLIT)), ("2000-2026", (SPLIT, f.index[-1]))):
            d = f.loc[a:b, [c, "fwd12"]].dropna()
            if len(d) < 60 or d[c].nunique() < 5:
                continue
            q = pd.qcut(d[c].rank(method="first"), 5, labels=False)
            means = d.groupby(q)["fwd12"].mean()
            spread = d["fwd12"][q == 4].mean() - d["fwd12"][q == 0].mean()
            res[lab] = {"quintile_fwd12": [float(x) for x in means.tolist()], "q5_minus_q1": float(spread),
                        "corr": float(d[c].corr(d["fwd12"], method="spearman"))}
        out[c] = res
    return out


def composite_score(f: pd.DataFrame) -> dict:
    """Pre-registered buy score: equal-weight expanding z-scores with signs fixed by economics (deeper
    drawdown, wider credit spreads, more fear, cheaper valuation = higher score). No fitting."""
    parts = {}

    def ez(s):
        m = s.expanding(60).mean()
        sd = s.expanding(60).std()
        return (s - m) / sd

    parts["drawdown"] = ez(-f["dd_ath"])
    if "baa_spread" in f:
        parts["credit_spread"] = ez(f["baa_spread"])
    if "sent_z" in f:
        parts["fear"] = -f["sent_z"].clip(-3, 3)
    if "cape_pct" in f:
        parts["cheapness"] = ez(-f["cape_pct"])
    df = pd.DataFrame(parts)
    score = df.mean(axis=1, skipna=True).where(df.notna().sum(axis=1) >= 2)
    out = {"components": list(parts), "periods": {}}
    for lab, (a, b) in (("1960-1999", ("1960-01-01", SPLIT)), ("2000-2026", (SPLIT, f.index[-1]))):
        d = pd.concat([score.rename("s"), f["fwd12"], f["fwd24"]], axis=1).loc[a:b].dropna()
        if len(d) < 60:
            continue
        q = pd.qcut(d["s"].rank(method="first"), 5, labels=False)
        top = d[q == 4]
        out["periods"][lab] = {"quintile_fwd12": [float(x) for x in d.groupby(q)["fwd12"].mean()],
                               "quintile_fwd24": [float(x) for x in d.groupby(q)["fwd24"].mean()],
                               "top_quintile_minus_all_fwd12": float(top["fwd12"].mean() - d["fwd12"].mean()),
                               "top_quintile_hit12": float((top["fwd12"] > 0).mean()),
                               "corr_fwd12": float(d["s"].corr(d["fwd12"], method="spearman")),
                               "t_nw_top_minus_rest": newey_west_t(
                                   (d["fwd12"].where(q == 4) - d["fwd12"].mean()).dropna(), 12)}
    out["latest"] = {"date": score.dropna().index[-1].strftime("%Y-%m") if score.notna().any() else None,
                     "score": float(score.dropna().iloc[-1]) if score.notna().any() else None,
                     "percentile": float(expanding_pct(score.dropna(), 60).iloc[-1]) if score.notna().sum() > 60 else None}
    out["series"] = [[d.strftime("%Y-%m"), round(float(v), 3)] for d, v in score.dropna().items()]
    return out


def deployment(p: pd.DataFrame, f: pd.DataFrame, sig: pd.Series | None, name: str | None,
               reserve: float = 0.20, stages: tuple | None = None) -> dict:
    """Does holding a cash reserve for dips beat staying invested? Investor keeps `reserve` in T-bills, deploys
    it all on a buy signal, and rebuilds the reserve when the market regains its prior high."""
    if sig is None:
        return {}
    r, rf = p["market"].loc[f.index], p["rf"].loc[f.index].fillna(0)
    s = sig.reindex(f.index).fillna(False).astype(bool)
    px = f["px"]
    res = {}
    for lab, (a, b) in (("1960-1999", ("1960-01-01", SPLIT)), ("2000-2026", (SPLIT + pd.offsets.MonthEnd(1), f.index[-1])),
                        ("1960-2026", ("1960-01-01", f.index[-1]))):
        rr, rff, ss, pp = r.loc[a:b], rf.loc[a:b], s.loc[a:b], px.loc[a:b]
        w_eq, vals, peak = 1 - reserve, [], pp.iloc[0]
        dd_ = f["dd_ath"].loc[a:b]
        for d in rr.index:
            ret = w_eq * rr[d] + (1 - w_eq) * rff[d]
            vals.append(ret)
            peak = max(peak, pp[d])
            if stages:
                hit = sum(dd_[d] <= s_ for s_ in stages)
                w_eq = max(w_eq, 1 - reserve + reserve * hit / len(stages))
                if pp[d] >= peak and dd_[d] > stages[0]:
                    w_eq = 1 - reserve
            elif ss[d]:
                w_eq = 1.0                      # deploy the reserve
            elif pp[d] >= peak and w_eq > 1 - reserve:
                w_eq = 1 - reserve              # new high: rebuild the reserve
        dip = pd.Series(vals, index=rr.index)
        static = (1 - reserve) * rr + reserve * rff
        def st(x):
            eq = (1 + x).cumprod()
            return {"cagr": float(eq.iloc[-1] ** (12 / len(x)) - 1), "max_dd": float((eq / eq.cummax() - 1).min())}
        res[lab] = {"buy_the_dip": st(dip), "static_80_20": st(static), "fully_invested": st(rr)}
    return {"rule": name, "reserve": reserve, "periods": res}
