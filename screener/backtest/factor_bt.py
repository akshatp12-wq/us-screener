"""Factor-level validation on Ken French portfolios (CRSP-based, survivorship-free, monthly since 1926/1963).

These are the closest free, bias-free stand-ins for each stock signal:
  value -> HML (and the top book-to-market quintile as the long-only leg)
  momentum -> Mom (UMD) / top prior-return decile
  profitability -> RMW (operating profitability; Novy-Marx's GP/A is not in the library)
  investment -> CMA / lowest asset-growth quintile
  issuance -> lowest minus highest net-share-issuance quintile
  lowvol -> lowest minus highest variance quintile (and beta quintiles)
Insiders, short interest and SUE have no free bias-free history; they are validated (where possible)
in the stock-level backtest instead.
"""
from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd

from ..data.macro_sources import french, french_table, safe
from ..util import ann_stats
from .metrics import capm, drawdown, equity_curve, mp_split, newey_west_t, signal_health

log = logging.getLogger("screener.backtest")


def _norm(c: str) -> str:
    return re.sub(r"\s+", "", str(c)).lower()


def pick(df: pd.DataFrame, *cands: str) -> str | None:
    cols = {_norm(c): c for c in df.columns}
    for c in cands:
        if _norm(c) in cols:
            return cols[_norm(c)]
    return None


def _vw(tables: dict) -> pd.DataFrame:
    for k, v in tables.items():
        kl = k.lower()
        if "[monthly]" in k and "value weight" in kl and "number" not in kl and "size" not in kl:
            return v
    return french_table(tables, "", "monthly")


def load_french_bundle(f, cfg: dict) -> dict:
    ff = cfg["backtest"]["french_files"]
    b: dict = {"cols_used": {}}
    t = safe(french, f, cfg, ff["ff5"], source=f"French:{ff['ff5']}")
    if t:
        b["ff5"] = french_table(t, "", "monthly")
    t = safe(french, f, cfg, ff["ff3"], source=f"French:{ff['ff3']}")
    if t:
        b["ff3"] = french_table(t, "", "monthly")
    t = safe(french, f, cfg, ff["mom"], source=f"French:{ff['mom']}")
    if t:
        m = french_table(t, "", "monthly")
        b["mom"] = m.iloc[:, 0]
    t = safe(french, f, cfg, ff["ff3_daily"], source=f"French:{ff['ff3_daily']}")
    if t:
        b["ff3_daily"] = french_table(t, "", "daily")
    t = safe(french, f, cfg, ff["ind49"], source=f"French:{ff['ind49']}")
    if t:
        ind = _vw(t)
        b["ind49"] = ind.mask(ind <= -0.99)
    sorts = {}
    for key in ("bm", "op", "inv", "nsi", "var", "beta", "me", "mom10"):
        t = safe(french, f, cfg, ff[key], source=f"French:{ff[key]}")
        if t:
            sorts[key] = _vw(t)
    b["sorts"] = sorts
    return b


def build_factor_series(b: dict) -> tuple[dict, dict, pd.Series, pd.Series, pd.Series]:
    """Long/short series, long-only legs, market total return, RF and market excess."""
    ff5 = b.get("ff5")
    ff3 = b.get("ff3")
    base = ff3 if ff3 is not None else ff5
    if base is None:
        raise RuntimeError("Fama-French factors unavailable")
    rf = base["RF"]
    mkt_ex = base["Mkt-RF"]
    mkt = mkt_ex + rf
    ls, legs, used = {}, {}, {}
    if ff5 is not None:
        ls["value"] = ff5["HML"]
        ls["profitability"] = ff5["RMW"]
        ls["investment"] = ff5["CMA"]
        used.update(value="HML", profitability="RMW", investment="CMA")
    elif ff3 is not None:
        ls["value"] = ff3["HML"]
        used["value"] = "HML"
    if "mom" in b:
        ls["momentum"] = b["mom"]
        used["momentum"] = "Mom"
    s = b.get("sorts", {})

    def q(df, lo=True):
        return pick(df, "Lo 20", "Lo20") if lo else pick(df, "Hi 20", "Hi20")

    if "nsi" in s:
        d = s["nsi"]
        lo, hi = q(d), q(d, False)
        neg = pick(d, "Neg", "<0", "< 0", "Repurchase")
        if lo and hi:
            ls["issuance"] = d[lo] - d[hi]
            legs["issuance"] = d[neg] if neg else d[lo]
            used["issuance"] = f"NI {lo} minus {hi}; long leg {neg or lo}"
    if "var" in s:
        d = s["var"]
        lo, hi = q(d), q(d, False)
        if lo and hi:
            ls["lowvol"] = d[lo] - d[hi]
            legs["lowvol"] = d[lo]
            used["lowvol"] = f"VAR {lo} minus {hi}"
    if "beta" in s:
        d = s["beta"]
        lo, hi = q(d), q(d, False)
        if lo and hi:
            ls["lowbeta"] = d[lo] - d[hi]
            used["lowbeta"] = f"BETA {lo} minus {hi}"
    if "bm" in s and (c := q(s["bm"], False)):
        legs["value"] = s["bm"][c]
    if "op" in s and (c := q(s["op"], False)):
        legs["profitability"] = s["op"][c]
    if "inv" in s and (c := q(s["inv"])):
        legs["investment"] = s["inv"][c]
    if "mom10" in s and (c := pick(s["mom10"], "Hi PRIOR", "HiPRIOR", "Hi 10")):
        legs["momentum"] = s["mom10"][c]
    b["cols_used"] = used
    return ls, legs, mkt, rf, mkt_ex


def market_rv_monthly(ff3_daily: pd.DataFrame | None) -> pd.Series | None:
    if ff3_daily is None:
        return None
    d = ff3_daily["Mkt-RF"] + ff3_daily["RF"]
    rv = (d ** 2).resample("ME").mean() * 252
    return np.sqrt(rv)


def bond_returns_from_yields(gs10: pd.Series | None, duration: float = 7.5) -> pd.Series | None:
    if gs10 is None or gs10.empty:
        return None
    y = gs10.resample("ME").last() / 100.0
    return (y.shift(1) / 12 - duration * y.diff()).dropna().rename("bonds")


def trend_signal(r: pd.Series) -> pd.Series:
    """TSMOM + 10-month SMA signal known at month end t (apply to t+1): 1 up, 0.5 mixed, 0 down."""
    px = (1 + r.fillna(0)).cumprod()
    r12 = px / px.shift(12) - 1
    sma = px.rolling(10).mean()
    up = (r12 > 0).astype(float) + (px > sma).astype(float)
    return (up / 2).where(r12.notna())


def _blend(series: dict, weights: dict) -> pd.Series:
    df = pd.DataFrame({k: series[k] for k in weights if k in series})
    W = pd.Series({k: weights[k] for k in df.columns})
    num = df.fillna(0).mul(W, axis=1).sum(axis=1)
    den = df.notna().mul(W, axis=1).sum(axis=1)
    return (num / den.replace(0, np.nan)).where(den >= 0.6 * W.sum())


def run_factor_backtests(b: dict, cfg: dict, regime_hist: pd.DataFrame | None = None,
                         sent_hist: pd.Series | None = None, extra_assets: dict | None = None) -> dict:
    bt = cfg["backtest"]
    ls, legs, mkt, rf, mkt_ex = build_factor_series(b)
    start = pd.Timestamp(bt["start"])
    costs = bt["factor_costs"]
    lo_share = bt["long_only_cost_share"]
    out: dict = {"signals": {}, "modes": {}, "columns_used": b.get("cols_used", {}),
                 "market": {"stats": ann_stats(mkt[mkt.index >= start]),
                            "note": "CRSP value-weighted market (Ken French) as the SPY stand-in before 1993"}}
    crash = crash_guard_history(mkt, b.get("ff3_daily"), cfg)

    for name, r in ls.items():
        r = r[r.index >= start].dropna()
        net = r - costs.get(name, costs.get("lowvol", 0.001))
        pub = bt["publication"].get(name if name != "lowbeta" else "lowvol")
        res = {
            "long_short_gross": ann_stats(r), "long_short_net": ann_stats(net),
            "t_nw": newey_west_t(r), "capm": capm(r, mkt_ex.reindex(r.index)),
            "mclean_pontiff": mp_split(r, pub) if pub else None,
            "health": {k: v for k, v in signal_health(r, bt["signal_health_window"], bt["decay_flag_ratio"]).items() if k != "series"},
            "curve_net": equity_curve(net),
            "rolling_sharpe": _sample(signal_health(r, bt["signal_health_window"]).get("series")),
            "proxy": out["columns_used"].get(name, name),
        }
        if name in legs:
            lg = legs[name][legs[name].index >= start].dropna()
            res["long_only_vs_market"] = ann_stats(lg - mkt.reindex(lg.index))
            res["long_only"] = ann_stats(lg)
        out["signals"][name] = res

    # ---------------------------------------------------------------- strategy modes (factor proxies)
    lt_w = {k: v for k, v in cfg["modes"]["long_term"]["weights"].items() if k in legs}
    lt = _blend(legs, lt_w)
    lt_cost = sum(costs.get(k, 0.001) * w for k, w in lt_w.items()) / max(sum(lt_w.values()), 1e-9) * lo_share
    lt_net = (lt - lt_cost)[lambda s: s.index >= start].dropna()
    out["modes"]["long_term"] = _mode_report(lt_net, mkt, rf, mkt_ex, cfg, "long_term",
                                             note="Blend of top-quintile value-weighted portfolios (B/M, OP, low INV, "
                                                  "low NSI); insiders excluded (no history). Net of costs.")

    mn_w = {k: v for k, v in cfg["modes"]["market_neutral"]["weights"].items() if k in ls}
    mom_mult = crash["multiplier"].shift(1).reindex(ls["momentum"].index).fillna(1.0) if "momentum" in ls else None
    ls_adj = dict(ls)
    if mom_mult is not None:
        ls_adj["momentum"] = ls["momentum"] * mom_mult
    mn = _blend(ls_adj, mn_w)
    mn_cost = sum(costs.get(k, 0.001) * w for k, w in mn_w.items()) / max(sum(mn_w.values()), 1e-9)
    scale = pd.Series(1.0, index=mn.index)
    if sent_hist is not None and len(sent_hist):
        ss = cfg["modes"]["market_neutral"]["short_scale_by_sentiment"]
        lab = sent_hist.shift(1).reindex(mn.index).ffill()
        scale = lab.map({"euphoric": ss["euphoric"], "fearful": ss["fearful"]}).fillna(1.0)
    mn_net = ((mn - mn_cost) * scale + rf.reindex(mn.index))[lambda s: s.index >= start].dropna()
    out["modes"]["market_neutral"] = _mode_report(mn_net, mkt, rf, mkt_ex, cfg, "market_neutral",
                                                  note="Equal-dollar factor long/shorts (HML, Mom with crash guard, RMW, "
                                                       "CMA, NSI) on T-bill collateral; insiders/short interest excluded. "
                                                       "Book scaled by lagged sentiment. Net of costs.")
    mn_nog = ((_blend(ls, mn_w) - mn_cost) + rf.reindex(mn.index))[lambda s: s.index >= start].dropna()
    out["modes"]["market_neutral"]["without_crash_guard"] = ann_stats(mn_nog)

    ar_w = {k: v for k, v in cfg["modes"]["absolute_return"]["weights"].items() if k in legs}
    ar_eq = _blend(legs, ar_w)
    ar_cost = sum(costs.get(k, 0.001) * w for k, w in ar_w.items()) / max(sum(ar_w.values()), 1e-9) * lo_share
    ar = absolute_return_series(ar_eq - ar_cost, rf, b.get("ff3_daily"), cfg, regime_hist, extra_assets, mkt)
    ar = ar[ar.index >= start].dropna()
    out["modes"]["absolute_return"] = _mode_report(ar, mkt, rf, mkt_ex, cfg, "absolute_return",
                                                   note="Long-only blend incl. low-vol, volatility-targeted (10%), x regime "
                                                        "multiplier, 30% trend sleeve, 15% drawdown brake. Net of costs.")
    out["crash_guard"] = crash_guard_report(ls.get("momentum"), crash)
    return out


def _sample(s: pd.Series | None, every: int = 3) -> list:
    if s is None or len(s) == 0:
        return []
    s = s.iloc[::every]
    return [[d.strftime("%Y-%m"), round(float(v), 3)] for d, v in s.items()]


def _mode_report(r: pd.Series, mkt: pd.Series, rf: pd.Series, mkt_ex: pd.Series, cfg: dict, mode: str, note: str) -> dict:
    m = mkt.reindex(r.index)
    exr = r - rf.reindex(r.index)
    res = {
        "note": note, "stats": ann_stats(r), "market": ann_stats(m),
        "excess_vs_market": ann_stats(r - m), "capm": capm(r, mkt_ex.reindex(r.index), rf),
        "sharpe_excess": float(exr.mean() / exr.std() * np.sqrt(12)) if exr.std() > 0 else None,
        "market_sharpe_excess": float((m - rf.reindex(m.index)).mean() / (m - rf.reindex(m.index)).std() * np.sqrt(12)),
        "curve": equity_curve(r), "market_curve": equity_curve(m),
        "drawdown_min": float(drawdown(r).min()), "market_drawdown_min": float(drawdown(m).min()),
        "hit_rate_vs_market": float((r > m).mean()),
        "rolling_10y_beat_market": _rolling_beat(r, m, 120),
        "decades": _decades(r, m),
    }
    pub = {"long_term": "1995-03-31", "market_neutral": "1995-03-31", "absolute_return": "2012-05-31"}[mode]
    res["post_1995_or_pub"] = {"since": pub, "strategy": ann_stats(r[r.index > pub]), "market": ann_stats(m[m.index > pub])}
    return res


def _rolling_beat(r, m, n):
    a = np.log1p(r).rolling(n).sum()
    b = np.log1p(m).rolling(n).sum()
    d = (a - b).dropna()
    return float((d > 0).mean()) if len(d) else None


def _decades(r, m):
    out = []
    for dec in range((r.index[0].year // 10) * 10, r.index[-1].year + 1, 10):
        rr = r[(r.index.year >= dec) & (r.index.year < dec + 10)]
        mm = m.reindex(rr.index)
        if len(rr) >= 24:
            out.append({"decade": f"{dec}s", "strategy_cagr": ann_stats(rr)["cagr"], "market_cagr": ann_stats(mm)["cagr"]})
    return out


def crash_guard_history(mkt: pd.Series, ff3_daily: pd.DataFrame | None, cfg: dict) -> pd.DataFrame:
    c = cfg["momentum_crash_guard"]
    lr = np.log1p(mkt).rolling(c["lookback_months"]).sum()
    if ff3_daily is not None:
        d = ff3_daily["Mkt-RF"] + ff3_daily["RF"]
        vol = (d.rolling(c["vol_window_days"]).std() * np.sqrt(252)).resample("ME").last()
    else:
        vol = mkt.rolling(6).std() * np.sqrt(12)
    from ..util import expanding_pct
    vp = expanding_pct(vol.dropna(), 60).reindex(mkt.index)
    on = (lr < 0) & (vp >= c["vol_pct"])
    mult = pd.Series(np.where(on, c["momentum_multiplier"], 1.0), index=mkt.index)
    return pd.DataFrame({"on": on, "multiplier": mult, "bear": lr < 0, "vol_pct": vp})


def crash_guard_report(mom: pd.Series | None, crash: pd.DataFrame) -> dict:
    if mom is None:
        return {"status": "unavailable"}
    on = crash["on"].shift(1).reindex(mom.index).fillna(False).astype(bool)
    guarded = mom * crash["multiplier"].shift(1).reindex(mom.index).fillna(1.0)
    return {
        "months_on": int(on.sum()), "share_on": float(on.mean()),
        "mom_when_on": ann_stats(mom[on]), "mom_when_off": ann_stats(mom[~on]),
        "mom_unguarded": ann_stats(mom), "mom_guarded": ann_stats(guarded),
        "worst_month_unguarded": float(mom.min()), "worst_month_guarded": float(guarded.min()),
    }


def absolute_return_series(eq: pd.Series, rf: pd.Series, ff3_daily, cfg: dict, regime_hist: pd.DataFrame | None,
                           extra: dict | None, mkt: pd.Series) -> pd.Series:
    m = cfg["modes"]["absolute_return"]
    rv = market_rv_monthly(ff3_daily)
    if rv is None:
        rv = mkt.rolling(6).std() * np.sqrt(12)
    expo = (m["target_vol"] / rv.shift(1)).clip(upper=m["max_leverage"]).reindex(eq.index)
    if regime_hist is not None and "multiplier" in regime_hist:
        rm = regime_hist["multiplier"].shift(1).reindex(eq.index).ffill().fillna(1.0)
        expo = expo * rm
    # trend sleeve: equities (market) + bonds (+ gold/commodities when available)
    assets = {"stocks": mkt}
    if extra:
        assets.update({k: v for k, v in extra.items() if v is not None})
    sleeve_parts = []
    for k, r in assets.items():
        sig = trend_signal(r).shift(1)
        sleeve_parts.append((sig * r + (1 - sig) * rf.reindex(r.index)).rename(k))
    sleeve = pd.concat(sleeve_parts, axis=1).reindex(eq.index).mean(axis=1, skipna=True)
    share = m["trend_sleeve_share"]
    rf_ = rf.reindex(eq.index)
    raw = (1 - share) * (expo * eq + (1 - expo).clip(lower=-0.5) * rf_) + share * sleeve.fillna(rf_)
    # drawdown brake
    out = []
    peak, val, braked = 1.0, 1.0, False
    for d, x in raw.items():
        if np.isnan(x):
            out.append(np.nan)
            continue
        r_t = m["drawdown_exposure"] * x + (1 - m["drawdown_exposure"]) * rf_.get(d, 0) if braked else x
        val *= 1 + r_t
        peak = max(peak, val)
        dd = val / peak - 1
        if dd <= -m["drawdown_limit"]:
            braked = True
        elif braked and dd > -m["drawdown_limit"] / 2:
            braked = False
        out.append(r_t)
    return pd.Series(out, index=raw.index)
