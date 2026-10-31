"""Validation of the market-level dials and the regime label, using only information
available at each date (publication lags applied)."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..signals.bubble import bubble_history, flagged_share_history
from ..signals.macro import fit_em_probit, one_sided_hp, recession_prob
from ..util import ann_stats, expanding_pct
from .metrics import auc, forward_max_drawdown, newey_west_t, ols

log = logging.getLogger("screener.backtest")


def _q_to_m(s: pd.Series, lag_q: int) -> pd.Series:
    """Quarterly series -> monthly, shifted by its publication lag, forward-filled."""
    s = s.dropna()
    s.index = s.index + pd.offsets.QuarterEnd(0)
    s = s.shift(lag_q, freq="QE") if lag_q else s
    return s.resample("ME").ffill()


def regime_history(macro: dict, ind49: pd.DataFrame | None, mkt: pd.Series, cfg: dict,
                   bw: pd.Series | None, vix: pd.Series | None) -> pd.DataFrame:
    """Monthly Calm / Stretched / Fragile label with lags. Columns: inputs, label, multiplier."""
    lag = cfg["macro"]["credit_gap"]["pub_lag_quarters"]
    rz = cfg["macro"]["red_zones"]
    idx = pd.date_range("1960-01-31", mkt.index[-1], freq="ME")
    h = pd.DataFrame(index=idx)

    # credit gap (one-sided HP is already real-time; add the publication lag)
    cr = macro.get("CRDQUSAPABIS")
    if cr is not None and len(cr) > 60:
        q = cr.resample("QE").last().dropna()
        gap = (q - one_sided_hp(q, cfg["macro"]["credit_gap"]["hp_lambda"], cfg["macro"]["credit_gap"]["min_obs"])).dropna()
        g = _q_to_m(gap, lag).reindex(idx).ffill()
        h["credit_gap"] = g
        h["credit_gap_red"] = g > cfg["macro"]["credit_gap"]["red_above"]

    # red zones
    def zone(credit, price, price_lag_m=0):
        if credit is None or price is None:
            return None
        cq = credit.resample("QE").last().dropna()
        dc = (cq - cq.shift(4 * rz["horizon_years"])).dropna()
        pm = price.resample("ME").last().dropna()
        if price_lag_m:
            pm = pm.shift(price_lag_m)
        dp = np.log(pm / pm.shift(12 * rz["horizon_years"])).dropna()
        dcm = _q_to_m(dc, lag)
        df = pd.concat([dcm.rename("c"), dp.rename("p")], axis=1).dropna()
        cp, pp = expanding_pct(df["c"], 20), expanding_pct(df["p"], 20)
        return ((cp >= rz["credit_pct"]) & (pp >= rz["price_pct"])).reindex(idx).fillna(False)

    stock_px = macro.get("_stock_price")
    bz = zone(macro.get("QUSNAM770A"), stock_px)
    hz = zone(macro.get("QUSHAM770A"), macro.get("CSUSHPINSA"), price_lag_m=2)
    h["business_red_zone"] = bz if bz is not None else False
    h["household_red_zone"] = hz if hz is not None else False

    # sentiment: Baker-Wurgler (lag 1 month) while available, then a VIX-based proxy
    sent = pd.Series(np.nan, index=idx)
    if bw is not None and len(bw):
        sent = bw.shift(1, freq="ME").reindex(idx)
    if vix is not None and len(vix) > 600:
        vm = vix.resample("ME").mean()
        z = -(vm - vm.rolling(60, min_periods=36).mean()) / vm.rolling(60, min_periods=36).std()
        z = z.shift(0).reindex(idx)
        sent = sent.where(sent.notna(), z)
    h["sentiment_z"] = sent
    h["sentiment"] = np.where(sent > cfg["sentiment"]["euphoric_z"], "euphoric",
                              np.where(sent < cfg["sentiment"]["fearful_z"], "fearful", "neutral"))
    h.loc[sent.isna(), "sentiment"] = "neutral"

    # bubble share across the 49 industries
    if ind49 is not None:
        sh = flagged_share_history(ind49, mkt, cfg).reindex(idx)
        h["bubble_share"] = sh
    else:
        h["bubble_share"] = np.nan

    credit_red = h.get("credit_gap_red", False) | h["business_red_zone"] | h["household_red_zone"]
    stretched = (h["sentiment"] == "euphoric") | (h["bubble_share"] >= cfg["bubble"]["stretched_share"])
    h["credit_red"] = credit_red
    h["stretched"] = stretched
    h["label"] = np.where(credit_red & stretched, "Fragile", np.where(stretched, "Stretched", "Calm"))
    h["multiplier"] = h["label"].map(cfg["regime"]["multipliers"])
    return h


def validate_regime(h: pd.DataFrame, mkt: pd.Series, rf: pd.Series) -> dict:
    """Forward 12-month market outcomes by regime label."""
    px = (1 + mkt.fillna(0)).cumprod()
    fwd12 = px.shift(-12) / px - 1
    fdd = forward_max_drawdown(px, 12)
    vol12 = mkt[::-1].rolling(12).std()[::-1].shift(-1) * np.sqrt(12)
    d = pd.concat([h["label"], fwd12.rename("fwd12"), fdd.rename("fdd"), vol12.rename("vol")], axis=1).dropna()
    out = {}
    for lab, g in d.groupby("label"):
        out[lab] = {"months": int(len(g)), "fwd12_mean": float(g["fwd12"].mean()),
                    "fwd12_median": float(g["fwd12"].median()), "fwd_vol": float(g["vol"].mean()),
                    "prob_dd20": float((g["fdd"] <= -0.20).mean()), "avg_fwd_max_dd": float(g["fdd"].mean())}
    base = {"months": int(len(d)), "fwd12_mean": float(d["fwd12"].mean()), "prob_dd20": float((d["fdd"] <= -0.20).mean()),
            "fwd_vol": float(d["vol"].mean())}
    episodes = []
    lab = h["label"]
    chg = lab != lab.shift(1)
    for dt in lab.index[chg]:
        if lab[dt] != "Calm":
            episodes.append({"start": dt.strftime("%Y-%m"), "label": lab[dt]})
    return {"by_label": out, "all": base, "episodes": episodes[-40:],
            "label_series": [[d.strftime("%Y-%m"), v] for d, v in lab.dropna().iloc[::1].items()]}


def validate_yield_curve(macro: dict, cfg: dict) -> dict:
    a, b = fit_em_probit(cfg["macro"]["yield_curve"]["em_table"])
    gs10, tb3 = macro.get("GS10"), macro.get("TB3MS")
    t10y3m = macro.get("T10Y3M")
    rec = macro.get("USREC")
    if rec is None or (gs10 is None and t10y3m is None):
        return {"status": "unavailable"}
    spread = None
    if gs10 is not None and tb3 is not None:
        spread = (gs10.resample("ME").mean() - tb3.resample("ME").mean()).dropna()
    if t10y3m is not None:
        daily = t10y3m.resample("ME").mean().dropna()
        spread = daily if spread is None else daily.combine_first(spread)
    spread_q = spread.sort_index().rolling(3).mean().dropna()
    prob = recession_prob(spread_q, a, b)
    r = rec.resample("ME").max().reindex(prob.index)
    target = r.shift(-12)                                         # in recession 12 months ahead
    start_12 = ((r.shift(-1).rolling(12).max().shift(-11) == 1) & (r == 0)).astype(float)  # recession begins within 12m
    start_12[r.shift(-12).isna()] = np.nan
    oos = prob.index > "1996-06-30"
    episodes = []
    above = prob > cfg["macro"]["yield_curve"]["red_above"] / 100
    in_ep, ep_start = False, None
    for d, v in above.items():
        if v and not in_ep:
            in_ep, ep_start = True, d
        if not v and in_ep:
            in_ep = False
            nxt = r.loc[ep_start:ep_start + pd.DateOffset(months=24)]
            episodes.append({"signal_start": ep_start.strftime("%Y-%m"), "signal_end": d.strftime("%Y-%m"),
                             "recession_within_24m": bool((nxt == 1).any()) if len(nxt) else None})
    if in_ep:
        nxt = r.loc[ep_start:]
        episodes.append({"signal_start": ep_start.strftime("%Y-%m"), "signal_end": "ongoing",
                         "recession_within_24m": bool((nxt == 1).any()) if nxt.notna().any() else None})
    return {
        "probit": {"a": a, "b": b},
        "auc_in_recession_12m_ahead": auc(prob, target),
        "auc_recession_starts_within_12m": auc(prob, start_12),
        "auc_post_publication": auc(prob[oos], start_12[oos]),
        "sample_start": prob.index[0].strftime("%Y-%m"),
        "episodes_above_red": episodes,
        "note": "AUC 0.5 = coin flip, 1.0 = perfect. Post-publication = after mid-1996.",
    }


def validate_credit_dials(h: pd.DataFrame, mkt: pd.Series) -> dict:
    px = (1 + mkt.fillna(0)).cumprod()
    fwd36 = (px.shift(-36) / px) ** (1 / 3) - 1
    fdd36 = forward_max_drawdown(px, 36)
    out = {}
    for col in ("credit_gap_red", "business_red_zone", "household_red_zone", "credit_red"):
        if col not in h:
            continue
        flag = h[col].fillna(False).astype(bool)
        d = pd.concat([flag.rename("f"), fwd36.rename("r"), fdd36.rename("dd")], axis=1).dropna()
        f_ = d["f"].astype(bool)
        on, off = d[f_], d[~f_]
        out[col] = {"months_flagged": int(len(on)),
                    "fwd3y_ann_when_on": float(on["r"].mean()) if len(on) else None,
                    "fwd3y_ann_when_off": float(off["r"].mean()) if len(off) else None,
                    "prob_dd30_3y_on": float((on["dd"] <= -0.30).mean()) if len(on) else None,
                    "prob_dd30_3y_off": float((off["dd"] <= -0.30).mean()) if len(off) else None,
                    "flag_periods": _periods(flag)}
    out["note"] = ("US-only sample: very few financial crises, so these conditional statistics rest on a "
                   "handful of independent episodes (mainly 1980s-90 and 2005-08).")
    return out


def _periods(flag: pd.Series) -> list:
    res, in_p, s = [], False, None
    for d, v in flag.items():
        if v and not in_p:
            in_p, s = True, d
        elif not v and in_p:
            in_p = False
            res.append([s.strftime("%Y-%m"), d.strftime("%Y-%m")])
    if in_p:
        res.append([s.strftime("%Y-%m"), "ongoing"])
    return res


def validate_ebp(ebp_df: pd.DataFrame | None, rec: pd.Series | None, mkt: pd.Series) -> dict:
    if ebp_df is None or ebp_df.empty or rec is None:
        return {"status": "unavailable"}
    e = ebp_df["ebp"].dropna()
    r = rec.resample("ME").max().reindex(e.index)
    start_12 = ((r.shift(-1).rolling(12).max().shift(-11) == 1) & (r == 0)).astype(float)
    start_12[r.shift(-12).isna()] = np.nan
    px = (1 + mkt.fillna(0)).cumprod()
    fwd12 = (px.shift(-12) / px - 1).reindex(e.index)
    reg = ols(fwd12, e.rename("ebp").to_frame(), nw_lags=12)
    return {"auc_recession_starts_within_12m": auc(e, start_12), "fwd12_on_ebp": reg,
            "sample": [e.index[0].strftime("%Y-%m"), e.index[-1].strftime("%Y-%m")],
            "note": "Gilchrist-Zakrajsek EBP is estimated with the full bond panel; small revisions mean this is "
                    "not a pure real-time test."}


def validate_cape(sh: pd.DataFrame | None, cfg: dict) -> dict:
    if sh is None or sh.empty:
        return {"status": "unavailable"}
    n = 12 * cfg["macro"]["cape"]["forward_years"]
    tr = sh["real_tr_price"].dropna()
    cape = sh["cape"].dropna()
    fwd = (tr.shift(-n) / tr) ** (1 / cfg["macro"]["cape"]["forward_years"]) - 1
    d = pd.concat([np.log(cape).rename("lc"), fwd.rename("fwd")], axis=1).dropna()
    b, a = np.polyfit(d["lc"], d["fwd"], 1)
    r2 = float(np.corrcoef(d["lc"], d["fwd"])[0, 1] ** 2)
    # out-of-sample: expanding regression from 1950, compared with the historical-mean forecast
    preds, means, actual = [], [], []
    for i, dt in enumerate(d.index):
        if dt < pd.Timestamp("1950-01-01"):
            continue
        hist = d[d.index <= dt - pd.DateOffset(months=n)]
        if len(hist) < 240:
            continue
        bb, aa = np.polyfit(hist["lc"], hist["fwd"], 1)
        preds.append(aa + bb * d.loc[dt, "lc"])
        means.append(hist["fwd"].mean())
        actual.append(d.loc[dt, "fwd"])
    preds, means, actual = map(np.asarray, (preds, means, actual))
    oos_r2 = 1 - ((actual - preds) ** 2).sum() / ((actual - means) ** 2).sum() if len(actual) else None
    q = pd.qcut(d["lc"], 5, labels=False)
    quint = d.groupby(q)["fwd"].mean().tolist()
    return {"in_sample_r2": r2, "oos_r2_vs_hist_mean": float(oos_r2) if oos_r2 is not None else None,
            "slope": float(b), "fwd10y_by_cape_quintile": quint, "n_months": int(len(d)),
            "note": "Positive OOS R^2 means CAPE beat the historical-average forecast of 10-year real returns. "
                    "Says nothing about 1-year timing."}


def validate_vrp(vix: pd.Series | None, spy_ret: pd.Series | None) -> dict:
    if vix is None or spy_ret is None:
        return {"status": "unavailable"}
    iv = vix.dropna() ** 2 / 12
    rv = ((np.log1p(spy_ret.dropna()) * 100) ** 2).rolling(21).sum()
    vrp = (iv - rv).dropna()
    px = (1 + spy_ret.fillna(0)).cumprod()
    fwd = (px.shift(-63) / px - 1).reindex(vrp.index)
    m = pd.concat([vrp.rename("vrp"), fwd.rename("fwd")], axis=1).dropna().resample("ME").last().dropna()
    reg = ols(m["fwd"], m[["vrp"]], nw_lags=3)
    q = pd.qcut(m["vrp"], 5, labels=False)
    return {"fwd_3m_on_vrp": reg, "fwd_3m_by_vrp_quintile": m.groupby(q)["fwd"].mean().tolist(),
            "sample": [m.index[0].strftime("%Y-%m"), m.index[-1].strftime("%Y-%m")],
            "note": "Monthly sampling of overlapping 3-month returns; t-stats use Newey-West (3 lags). "
                    "BTZ (2009) published in 2009: post-2009 is out of sample."}


def validate_sentiment(h: pd.DataFrame, sorts: dict, mkt: pd.Series) -> dict:
    """Baker-Wurgler prediction: after high sentiment, speculative stocks (small, volatile) underperform."""
    out = {}
    me, var = sorts.get("me"), sorts.get("var")
    lab = h["sentiment"]
    pairs = {}
    if me is not None and "Lo 30" in me and "Hi 30" in me:
        pairs["small_minus_big"] = me["Lo 30"] - me["Hi 30"]
    if var is not None and "Hi 20" in var and "Lo 20" in var:
        pairs["highvol_minus_lowvol"] = var["Hi 20"] - var["Lo 20"]
    pairs["market"] = mkt
    for k, r in pairs.items():
        f12 = np.expm1(np.log1p(r)[::-1].rolling(12).sum()[::-1].shift(-1))
        d = pd.concat([lab.rename("s"), f12.rename("f")], axis=1).dropna()
        out[k] = {s: {"months": int(len(g)), "fwd12_mean": float(g["f"].mean())} for s, g in d.groupby("s")}
    return out


def validate_bubbles(ind: pd.DataFrame | None, mkt: pd.Series, cfg: dict) -> dict:
    """Greenwood-Shleifer-You replication on 49 value-weighted industries (monthly since 1926)."""
    if ind is None:
        return {"status": "unavailable"}
    b = cfg["bubble"]
    ex = bubble_history(ind, mkt, cfg)
    px = (1 + ind.fillna(0)).cumprod()
    fdd = px.apply(lambda c: forward_max_drawdown(c, 24))
    fwd_ex = np.expm1(np.log1p(ind)[::-1].rolling(24).sum()[::-1].shift(-1)).sub(
        np.expm1(np.log1p(mkt)[::-1].rolling(24).sum()[::-1].shift(-1)), axis=0)
    lr = np.log1p(ind)
    r6 = lr.rolling(6).sum()
    r24 = lr.rolling(24).sum()
    accel = (r6 / r24).where(r24 > 0)
    events = []
    for col in ind.columns:
        last = None
        for dt in ex.index[ex[col] >= b["amber_excess"]]:
            if last is not None and dt < last + pd.DateOffset(months=24):
                continue
            last = dt
            if pd.isna(fdd.loc[dt, col]):
                continue
            events.append({"industry": col, "date": dt, "excess_2y": float(ex.loc[dt, col]),
                           "crash": bool(fdd.loc[dt, col] <= -b["crash_drawdown"]),
                           "fwd_excess_2y": float(fwd_ex.loc[dt, col]) if pd.notna(fwd_ex.loc[dt, col]) else None,
                           "accel": bool(accel.loc[dt, col] > b["accel_share"]) if pd.notna(accel.loc[dt, col]) else False,
                           "red": bool(ex.loc[dt, col] >= b["red_excess"])})
    ev = pd.DataFrame(events)
    base = float((fdd <= -b["crash_drawdown"]).stack().mean())
    res = {"n_events": int(len(ev)), "base_crash_prob": base}
    if len(ev):
        res.update({
            "crash_prob_after_100pct": float(ev["crash"].mean()),
            "crash_prob_after_150pct": float(ev[ev["red"]]["crash"].mean()) if ev["red"].any() else None,
            "crash_prob_with_acceleration": float(ev[ev["accel"]]["crash"].mean()) if ev["accel"].any() else None,
            "mean_fwd_2y_excess": float(ev["fwd_excess_2y"].mean()),
            "share_fwd_excess_negative": float((ev["fwd_excess_2y"] < 0).mean()),
            "post_publication_events": int((ev["date"] > cfg["backtest"]["publication"]["bubble"]).sum()),
            "recent_events": [{**e, "date": e["date"].strftime("%Y-%m")} for e in
                              ev.sort_values("date").tail(15).to_dict("records")],
        })
    res["note"] = ("Event = first month an industry's 2-year return beats the market by >=100% (no re-entry for 24 "
                   "months). Crash = a 40% drawdown within the next 24 months (GSY 2019 definition).")
    return res


def validate_trend(mkt: pd.Series, rf: pd.Series, bonds: pd.Series | None) -> dict:
    from .factor_bt import trend_signal
    out = {}
    for name, r in (("stocks", mkt), ("bonds", bonds)):
        if r is None:
            continue
        sig = trend_signal(r).shift(1)
        strat = (sig * r + (1 - sig) * rf.reindex(r.index)).dropna()
        bh = r.reindex(strat.index)
        out[name] = {"trend": ann_stats(strat), "buy_hold": ann_stats(bh)}
    return out


def tstat_mean(x):
    return newey_west_t(x)
