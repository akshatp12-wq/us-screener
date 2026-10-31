"""Scenario engine, historical stress tests and fat-tailed Monte Carlo for a model portfolio."""
from __future__ import annotations

import numpy as np
import pandas as pd

FACTORS = ("value", "momentum", "profitability", "investment", "lowvol")
BOND_DURATION = 7.5   # approx duration of IEF, converts rate shocks to bond-like returns


def scenario_probs(cfg: dict, dials: dict, regime: dict) -> dict:
    sc = cfg["scenarios"]
    p = dict(sc["base_probs"])
    nd = sc["nudges"]
    notes = []
    yc = dials.get("yield_curve", {})
    if yc.get("value") is not None:
        target = yc["value"] / 100.0
        new = (1 - nd["recession_from_yield_curve"]) * p["recession"] + nd["recession_from_yield_curve"] * target
        notes.append(f"recession {p['recession']:.0%} -> {new:.0%} (yield-curve probit {target:.0%})")
        p["recession"] = new
    if regime.get("label") == "Fragile":
        p["credit_crisis"] += nd["fragile_credit_crisis_add"]
        notes.append("credit crisis +5pp (Fragile regime)")
    if dials.get("ebp", {}).get("status") == "red":
        p["credit_crisis"] += nd["ebp_red_credit_crisis_add"]
        notes.append("credit crisis +5pp (EBP red)")
    if regime.get("euphoric"):
        p["slowdown"] += nd["euphoric_slowdown_add"]
        notes.append("slowdown +5pp (euphoric sentiment)")
    # renormalise by shrinking expansion first
    excess = sum(p.values()) - 1.0
    if excess > 0:
        take = min(excess, p["expansion"] - 0.05)
        p["expansion"] -= take
        excess -= take
    tot = sum(p.values())
    p = {k: v / tot for k, v in p.items()}
    return {"probs": p, "notes": notes}


def exposures(port: pd.DataFrame, scores: pd.DataFrame, rate_beta: pd.Series | None) -> pd.DataFrame:
    x = port[["ticker", "weight", "sector", "beta"]].copy()
    x["beta"] = x["beta"].fillna(1.0).clip(-1, 3)
    for f in FACTORS:
        s = scores[f] if f in scores else pd.Series(dtype=float)
        x[f] = ((x["ticker"].map(s) - 0.5) * 2).fillna(0.0)
    x["rate_beta"] = x["ticker"].map(rate_beta).fillna(0.0) if rate_beta is not None else 0.0
    return x


def scenario_returns(port: pd.DataFrame, scores: pd.DataFrame, cfg: dict, probs: dict,
                     rate_beta: pd.Series | None = None, trend_rows: pd.DataFrame | None = None) -> dict:
    sc = cfg["scenarios"]
    eq = port[port.get("side", "long") != "trend"] if "side" in port else port
    x = exposures(eq, scores, rate_beta)
    res = {}
    for name, shock in sc["shocks"].items():
        sect = sc.get("sector_shocks", {}).get(name, {})
        bond_ret = -BOND_DURATION * shock["rates"] / 100.0
        r_i = (x["beta"] * shock["market"]
               + x["sector"].map(sect).fillna(0.0)
               + sum(x[f] * shock[f] * 0.5 for f in FACTORS)
               + x["rate_beta"] * bond_ret)
        contrib = x["weight"] * r_i
        total = float(contrib.sum())
        if trend_rows is not None and not trend_rows.empty:
            # trend sleeve approximated as a bond-like / diversifying sleeve
            total += float(trend_rows["weight"].sum()) * (0.5 * bond_ret + 0.25 * max(shock["market"], 0))
        res[name] = {"return": total, "prob": probs["probs"][name],
                     "top_detractors": contrib.sort_values().head(3).index.map(lambda i: x.loc[i, "ticker"]).tolist()}
    exp_ret = sum(v["return"] * v["prob"] for v in res.values())
    return {"scenarios": res, "expected": exp_ret, "probs": probs}


def stress_tests(port: pd.DataFrame, ret_d: pd.DataFrame, cfg: dict, bench: str = "SPY") -> dict:
    out = {}
    eq = port[port.get("side", "long") != "trend"] if "side" in port else port
    w = eq.set_index("ticker")["weight"]
    beta_p = float((eq["weight"] * eq["beta"].fillna(1.0)).sum())
    for name, (a, b) in cfg["scenarios"]["stress_windows"].items():
        win = ret_d.loc[a:b]
        if win.empty:
            out[name] = {"status": "unavailable", "reason": "no price data for window"}
            continue
        cum = np.expm1(np.log1p(win).sum(min_count=1))
        have = [t for t in w.index if t in cum and win[t].notna().mean() > 0.9]
        cov = float(w.reindex(have).abs().sum() / w.abs().sum()) if len(w) else 0.0
        replay = float((w.reindex(have) * cum.reindex(have)).sum() / max(w.reindex(have).abs().sum(), 1e-9)
                       * w.abs().sum()) if have else None
        spy = float(cum.get(bench)) if bench in cum and pd.notna(cum.get(bench)) else None
        out[name] = {"window": [a, b], "replay_return": replay, "coverage": cov,
                     "beta_estimate": beta_p * spy if spy is not None else None, "spy_return": spy,
                     "note": "replay uses today's holdings that traded then (survivor-biased); "
                             "beta estimate = portfolio beta x SPY move"}
    return out


def basket_returns(port: pd.DataFrame, ret_d: pd.DataFrame, days: int = 504) -> pd.Series:
    eq = port[port.get("side", "long") != "trend"] if "side" in port else port
    w = eq.set_index("ticker")["weight"]
    cols = [t for t in w.index if t in ret_d]
    if not cols:
        return pd.Series(dtype=float)
    r = ret_d[cols].iloc[-days:].fillna(0.0)
    return r.mul(w.reindex(cols), axis=1).sum(axis=1)


def monte_carlo(daily_vol: float, annual_mu: float, cfg: dict) -> dict:
    mc = cfg["scenarios"]["monte_carlo"]
    rng = np.random.default_rng(mc["seed"])
    df = mc["t_df"]
    n, h = mc["paths"], mc["horizon_days"]
    z = rng.standard_t(df, size=(n, h)) / np.sqrt(df / (df - 2))
    mu_d = annual_mu / 252 - 0.5 * daily_vol ** 2
    logr = mu_d + daily_vol * z
    paths = np.cumsum(logr, axis=1)
    final = np.expm1(paths[:, -1])
    eq = np.exp(paths)
    peak = np.maximum.accumulate(np.concatenate([np.ones((n, 1)), eq], axis=1), axis=1)[:, 1:]
    mdd = (eq / peak - 1).min(axis=1)
    hist, edges = np.histogram(final, bins=40)
    return {
        "median": float(np.median(final)), "p5": float(np.percentile(final, 5)),
        "p95": float(np.percentile(final, 95)), "prob_loss_20": float((final <= -0.20).mean()),
        "expected_max_drawdown": float(mdd.mean()), "annual_vol": float(daily_vol * np.sqrt(252)),
        "annual_mu": annual_mu, "paths": n, "horizon_days": h, "t_df": df,
        "hist": {"counts": hist.tolist(), "edges": edges.tolist()},
    }
