"""Stock-level backtest of the actual screener (monthly, ~2011 onward).

Point-in-time: fundamentals only once filed (SEC `filed` dates), prices up to each month end, insider
trades only once filed. NOT survivorship-free: the universe is today's S&P 1500, because free price
history for delisted stocks is not available. We measure that bias directly (equal-weighted universe
vs RSP) and judge the screener by its spread over its own universe, not by its return vs SPY.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..data.sec import snapshot
from ..portfolio.construct import cost_bps, liquid_mask, select
from ..scoring.composite import composite, signal_scores
from ..signals.stock import compute_stock_signals, first_trade_dates, vol_beta
from ..util import ann_stats
from .metrics import capm, drawdown, equity_curve, newey_west_t

log = logging.getLogger("screener.backtest")
SIGNALS = ("value", "momentum", "profitability", "investment", "issuance", "insiders", "sue", "lowvol")


def _next_month_returns(ret_m: pd.DataFrame, t: pd.Timestamp) -> pd.Series | None:
    i = ret_m.index.searchsorted(t, side="right")
    if i >= len(ret_m):
        return None
    return ret_m.iloc[i]


def _port_return(w: pd.Series, nxt: pd.Series) -> tuple[float, float]:
    r = nxt.reindex(w.index)
    cov = float(w[r.notna()].abs().sum() / w.abs().sum()) if w.abs().sum() else 0.0
    return float((w * r.fillna(0.0)).sum()), cov


def run_stock_backtest(univ: pd.DataFrame, panels: dict, derived: dict, cfg: dict,
                       insiders: pd.DataFrame | None = None, regime_hist: pd.DataFrame | None = None,
                       bench: str = "SPY") -> dict:
    bt = cfg["backtest"]
    ret_d = panels["ret"]
    ret_m = (1 + ret_d.fillna(0)).resample("ME").prod() - 1
    ret_m = ret_m.where(ret_d.notna().resample("ME").sum() > 0)
    last_complete = ret_d.index[-1] + pd.offsets.MonthEnd(0)
    if ret_d.index[-1] < last_complete:
        last_complete = last_complete - pd.offsets.MonthEnd(1)
    dates = ret_m.index[(ret_m.index >= pd.Timestamp(bt["stock_level_start"])) & (ret_m.index < last_complete)]
    tickers = [t for t in univ["ticker"] if t in ret_d.columns]
    fd = first_trade_dates(ret_d[tickers])
    mkt = ret_d[bench] if bench in ret_d else ret_d[tickers].mean(axis=1)
    if insiders is not None and not insiders.empty and "cik" in univ:
        insiders = insiders[insiders["issuer_cik"].isin(set(univ["cik"].dropna().astype(int)))]

    series = {m: [] for m in cfg["modes"]}
    sig_ls, sig_lo, ics = {s: [] for s in SIGNALS}, {s: [] for s in SIGNALS}, {s: [] for s in SIGNALS}
    ew, coverage, turnover = [], [], {m: [] for m in cfg["modes"]}
    prev = {m: None for m in cfg["modes"]}
    prev_w = {m: pd.Series(dtype=float) for m in cfg["modes"]}
    ar_state = {"val": 1.0, "peak": 1.0, "braked": False}
    trend_assets = cfg["trend"]["assets"]

    for t in dates:
        nxt = _next_month_returns(ret_m, t)
        if nxt is None:
            break
        nd = ret_m.index[ret_m.index.searchsorted(t, side="right")]
        fund = snapshot(derived, t) if derived else None
        vb = vol_beta(ret_d[tickers], mkt, t, cfg["signals"]["lowvol"]["window_days"],
                      cfg["signals"]["lowvol"]["beta_window_days"])
        df = compute_stock_signals(univ, fund, panels, t, cfg, insiders=insiders, short=None,
                                   mkt_symbol=bench, ret_m=ret_m.loc[:t], vb=vb, first_dates=fd)
        alive = ret_d[df.index].loc[:t].iloc[-5:].notna().any()
        df = df[alive.reindex(df.index).fillna(False)]
        if len(df) < 100:
            continue
        sc = signal_scores(df, cfg)
        liq = liquid_mask(df, cfg).fillna(False)
        uni_r = nxt.reindex(df.index[liq])
        ew.append((nd, float(uni_r.mean())))
        coverage.append((nd, float(df["gpa"].notna().mean()), float(df["bm"].notna().mean())))
        for s in SIGNALS:
            x = sc[s][liq].dropna() if s in sc else pd.Series(dtype=float)
            if len(x) < 100 or x.nunique() < 5:
                continue
            r = nxt.reindex(x.index)
            hi, lo = r[x >= 0.7].mean(), r[x <= 0.3].mean()
            sig_ls[s].append((nd, float(hi - lo)))
            sig_lo[s].append((nd, float(hi - r.mean())))
            ok = r.notna()
            if ok.sum() > 50:
                ics[s].append((nd, float(x[ok].rank().corr(r[ok].rank()))))

        # crash guard (market 24m return < 0 and high vol)
        m24 = float(np.expm1(np.log1p(mkt.loc[:t].iloc[-504:]).sum()))
        vol = mkt.loc[:t].rolling(126).std().dropna()
        vpct = float((vol <= vol.iloc[-1]).mean()) if len(vol) else 0.0
        guard = cfg["momentum_crash_guard"]["momentum_multiplier"] if (m24 < 0 and vpct >= cfg["momentum_crash_guard"]["vol_pct"]) else 1.0
        reg = regime_hist.loc[:t].iloc[-1] if regime_hist is not None and len(regime_hist.loc[:t]) else None
        label = reg["label"] if reg is not None else "Calm"
        mult = float(reg["multiplier"]) if reg is not None else 1.0
        sent = reg["sentiment"] if reg is not None else "neutral"

        for mode, mc in cfg["modes"].items():
            score = composite(sc, mc["weights"], momentum_multiplier=guard if mc.get("crash_guard") else 1.0)
            if mode == "long_term":
                n = mc["portfolio"]["n_by_regime"][label]
                names = select(score, df, n, cfg, prev[mode])
                if not names:
                    continue
                gross = mc["portfolio"]["gross_by_regime"][label]
                w = pd.Series(gross / len(names), index=names)
                prev[mode] = names
            elif mode == "market_neutral":
                longs = select(score, df, mc["portfolio"]["n_long"], cfg, prev[mode][0] if prev[mode] else None)
                shorts = select(score.drop(longs, errors="ignore"), df, mc["portfolio"]["n_short"], cfg,
                                prev[mode][1] if prev[mode] else None, ascending=True)
                if not longs or not shorts:
                    continue
                scale = mc["short_scale_by_sentiment"].get(sent, 1.0)
                bl = df["beta"].reindex(longs).fillna(1).clip(0.2, 3).mean()
                bs = df["beta"].reindex(shorts).fillna(1).clip(0.2, 3).mean()
                w = pd.concat([pd.Series(scale / len(longs), index=longs),
                               pd.Series(-scale * bl / bs / len(shorts), index=shorts)])
                prev[mode] = (longs, shorts)
            else:
                names = select(score, df, mc["portfolio"]["n"], cfg, prev[mode])
                if not names:
                    continue
                basket = ret_d[names].loc[:t].iloc[-63:].mean(axis=1)
                bvol = float(basket.std() * np.sqrt(252)) or 0.15
                expo = min(mc["max_leverage"], mc["target_vol"] / bvol) * mult
                w = pd.Series(expo * (1 - mc["trend_sleeve_share"]) / len(names), index=names)
                prev[mode] = names
                ups = []
                for sym in trend_assets.values():
                    if sym in ret_d:
                        r_ = ret_d[sym].loc[:t].dropna()
                        if len(r_) > 260:
                            px = (1 + r_).cumprod().resample("ME").last()
                            if len(px) > 13 and px.iloc[-1] > px.iloc[-10:].mean() and px.iloc[-1] > px.iloc[-13]:
                                ups.append(sym)
                if ups:
                    w = pd.concat([w, pd.Series(mc["trend_sleeve_share"] / len(ups), index=ups)])
            r, cov = _port_return(w, nxt)
            if mode != "market_neutral":
                cash = 1 - w.sum()
                r += cash * 0.0  # cash earns ~0 here (conservative; T-bill not added)
            # costs
            allt = w.index.union(prev_w[mode].index)
            dw = (w.reindex(allt).fillna(0) - prev_w[mode].reindex(allt).fillna(0)).abs()
            dv = df["dollar_volume"].reindex(allt).fillna(50e6)
            cost = float((dw * cost_bps(dv, cfg)).sum()) / 1e4
            turnover[mode].append(float(dw.sum() / 2))
            prev_w[mode] = w
            net = r - cost
            if mode == "absolute_return":
                if ar_state["braked"]:
                    net *= mc["drawdown_exposure"]
                ar_state["val"] *= 1 + net
                ar_state["peak"] = max(ar_state["peak"], ar_state["val"])
                dd = ar_state["val"] / ar_state["peak"] - 1
                ar_state["braked"] = dd <= -mc["drawdown_limit"] or (ar_state["braked"] and dd <= -mc["drawdown_limit"] / 2)
            series[mode].append((nd, net))

    def to_s(lst):
        return pd.Series(dict(lst)).sort_index() if lst else pd.Series(dtype=float)

    spy_m = ret_m[bench] if bench in ret_m else None
    rsp_m = ret_m["RSP"] if "RSP" in ret_m else None
    ew_s = to_s(ew)
    res = {"modes": {}, "signals": {}, "universe": {}, "notes": []}
    for mode, lst in series.items():
        s = to_s(lst)
        if s.empty:
            continue
        spy = spy_m.reindex(s.index) if spy_m is not None else None
        uni = ew_s.reindex(s.index)
        res["modes"][mode] = {
            "stats": ann_stats(s), "spy": ann_stats(spy) if spy is not None else None,
            "excess_vs_spy": ann_stats(s - spy) if spy is not None else None,
            "excess_vs_universe_ew": ann_stats(s - uni),
            "capm_vs_spy": capm(s, spy) if spy is not None else None,
            "avg_turnover": float(np.mean(turnover[mode])) if turnover[mode] else None,
            "max_dd": float(drawdown(s).min()), "curve": equity_curve(s),
            "spy_curve": equity_curve(spy) if spy is not None else [],
            "universe_curve": equity_curve(uni),
        }
    for s in SIGNALS:
        ls, lo, ic = to_s(sig_ls[s]), to_s(sig_lo[s]), to_s(ics[s])
        if ls.empty:
            res["signals"][s] = {"status": "unavailable", "reason": "insufficient coverage in backtest"}
            continue
        res["signals"][s] = {"top30_minus_bottom30": ann_stats(ls), "t_nw": newey_west_t(ls),
                             "top30_minus_universe": ann_stats(lo), "ic_mean": float(ic.mean()) if len(ic) else None,
                             "ic_t": newey_west_t(ic) if len(ic) > 12 else None, "curve": equity_curve(ls)}
    if not ew_s.empty:
        res["universe"]["equal_weight"] = ann_stats(ew_s)
        if rsp_m is not None:
            res["universe"]["rsp"] = ann_stats(rsp_m.reindex(ew_s.index))
            res["universe"]["survivorship_gap"] = ann_stats(ew_s - rsp_m.reindex(ew_s.index))
        if spy_m is not None:
            res["universe"]["spy"] = ann_stats(spy_m.reindex(ew_s.index))
    cv = pd.DataFrame(coverage, columns=["date", "gpa", "bm"]).set_index("date") if coverage else None
    if cv is not None:
        res["coverage"] = {"gross_profitability": float(cv["gpa"].mean()), "book_to_market": float(cv["bm"].mean())}
    res["notes"] = [
        "Universe = today's S&P 1500 constituents (survivorship-biased). 'survivorship_gap' = equal-weighted "
        "universe minus RSP (equal-weighted S&P 500 ETF): an estimate of how much the universe itself is flattered.",
        "Short interest is excluded (FINRA free files only cover listed stocks from mid-2021).",
        "Cash in long-only modes earns 0 (T-bills not added); costs are bps by liquidity bucket on turnover.",
    ]
    return res
