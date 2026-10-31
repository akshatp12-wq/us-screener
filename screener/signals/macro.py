"""A. Macro and financial-stability dials (market level). Pure functions: series in, results out."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.sparse.linalg import spsolve
from scipy.stats import norm

from ..util import expanding_pct, last_pct, status_from

CITE = {
    "credit_gap": "Borio & Lowe (2002); Drehmann & Juselius (2014), BIS credit-to-GDP gap early-warning research",
    "red_zones": "Greenwood, Hanson, Shleifer & Sorensen (2022), 'Predictable Financial Crises', Journal of Finance",
    "yield_curve": "Estrella & Mishkin (1996, 1998), 'The Yield Curve as a Predictor of U.S. Recessions'",
    "ebp": "Gilchrist & Zakrajsek (2012), 'Credit Spreads and Business Cycle Fluctuations', AER",
    "cape": "Campbell & Shiller (1988, 1998); Shiller, Irrational Exuberance",
}


# ----------------------------------------------------------------------------- 1. credit-to-GDP gap
def hp_trend(y: np.ndarray, lam: float) -> np.ndarray:
    """Two-sided Hodrick-Prescott trend."""
    n = len(y)
    if n < 3:
        return y.copy()
    e = np.ones(n)
    K = sparse.diags([e[:-2], -2 * e[:-2], e[:-2]], [0, 1, 2], shape=(n - 2, n))
    A = sparse.eye(n) + lam * (K.T @ K)
    return spsolve(A.tocsc(), y)


def one_sided_hp(s: pd.Series, lam: float = 400_000, min_obs: int = 40) -> pd.Series:
    """One-sided HP trend: at each date, the end-point of the HP trend fitted on data up to that date."""
    s = s.dropna()
    y = s.to_numpy(dtype=float)
    out = np.full(len(y), np.nan)
    for t in range(min_obs - 1, len(y)):
        out[t] = hp_trend(y[: t + 1], lam)[-1]
    return pd.Series(out, index=s.index)


def credit_gap(ratio: pd.Series, cfg: dict) -> dict:
    c = cfg["macro"]["credit_gap"]
    if ratio is None or len(ratio.dropna()) < c["min_obs"]:
        return {"status": "unavailable", "reason": "BIS credit-to-GDP series unavailable", "series": None}
    trend = one_sided_hp(ratio, c["hp_lambda"], c["min_obs"])
    gap = (ratio - trend).dropna()
    v = float(gap.iloc[-1])
    return {
        "value": v, "unit": "pp of GDP", "as_of": gap.index[-1],
        "percentile": last_pct(gap),
        "status": status_from(v, c["green_below"], c["red_above"]),
        "threshold": f"green < {c['green_below']} pp, amber {c['green_below']}-{c['red_above']}, red > {c['red_above']}",
        "series": gap,
    }


# ----------------------------------------------------------------------------- 2. red zones
def _three_year_change(s: pd.Series, years: int, log_growth: bool) -> pd.Series:
    q = s.resample("QE").last().dropna()
    lag = 4 * years
    return (np.log(q / q.shift(lag)) if log_growth else q - q.shift(lag)).dropna()


def red_zone(credit: pd.Series, price: pd.Series, cfg: dict, label: str) -> dict:
    rz = cfg["macro"]["red_zones"]
    if credit is None or price is None or credit.dropna().empty or price.dropna().empty:
        return {"status": "unavailable", "reason": f"{label} inputs unavailable", "series": None}
    dc = _three_year_change(credit, rz["horizon_years"], log_growth=False)
    dp = _three_year_change(price, rz["horizon_years"], log_growth=True)
    df = pd.concat([dc.rename("credit"), dp.rename("price")], axis=1).dropna()
    if len(df) < 20:
        return {"status": "unavailable", "reason": "insufficient history", "series": None}
    cp = expanding_pct(df["credit"], 20)
    pp = expanding_pct(df["price"], 20)
    hi_credit = cp >= rz["credit_pct"]
    hi_price = pp >= rz["price_pct"]
    warm = (cp >= rz["amber_credit_pct"]) & hi_price
    state = pd.Series(np.select([cp.isna() | pp.isna(), hi_credit & hi_price, hi_credit | warm],
                                ["unavailable", "red", "amber"], default="green"), index=df.index)
    last = df.index[-1]
    return {
        "value": float(df["credit"].iloc[-1]), "unit": "pp of GDP, 3-yr change",
        "price_growth_3y": float(np.expm1(df["price"].iloc[-1])),
        "credit_pct": float(cp.iloc[-1]) if pd.notna(cp.iloc[-1]) else None,
        "price_pct": float(pp.iloc[-1]) if pd.notna(pp.iloc[-1]) else None,
        "percentile": float(cp.iloc[-1]) if pd.notna(cp.iloc[-1]) else None,
        "as_of": last, "status": state.iloc[-1],
        "threshold": f"red when 3y credit growth >= {int(rz['credit_pct']*100)}th pct AND 3y price growth >= "
                     f"{round(rz['price_pct']*100)}th pct of history",
        "series": df["credit"], "state_series": state,
    }


# ----------------------------------------------------------------------------- 3. yield curve
def fit_em_probit(table: list[list[float]]) -> tuple[float, float]:
    """Fit P = Phi(a + b*spread) to Estrella & Mishkin's published probability table."""
    p = np.array([r[0] for r in table], dtype=float) / 100.0
    s = np.array([r[1] for r in table], dtype=float)
    z = norm.ppf(p)
    b, a = np.polyfit(s, z, 1)
    return float(a), float(b)


def recession_prob(spread_q: pd.Series, a: float, b: float) -> pd.Series:
    return pd.Series(norm.cdf(a + b * spread_q.to_numpy(dtype=float)), index=spread_q.index)


def yield_curve(t10y3m: pd.Series, cfg: dict) -> dict:
    yc = cfg["macro"]["yield_curve"]
    if t10y3m is None or t10y3m.dropna().empty:
        return {"status": "unavailable", "reason": "T10Y3M unavailable", "series": None}
    a, b = fit_em_probit(yc["em_table"])
    daily = t10y3m.dropna()
    qavg = daily.rolling(63, min_periods=40).mean().dropna()      # trailing quarter average
    prob = recession_prob(qavg, a, b) * 100
    m = prob.resample("ME").last()
    v = float(prob.iloc[-1])
    return {
        "value": v, "unit": "% chance of recession within 4 quarters",
        "spread_qavg": float(qavg.iloc[-1]), "probit": {"a": a, "b": b},
        "as_of": prob.index[-1], "percentile": last_pct(m),
        "status": status_from(v, yc["green_below"], yc["red_above"]),
        "threshold": f"green < {yc['green_below']}%, amber {yc['green_below']}-{yc['red_above']}%, red > {yc['red_above']}%",
        "series": m,
    }


# ----------------------------------------------------------------------------- 4. excess bond premium
def excess_bond_premium(ebp_df: pd.DataFrame | None, hy: pd.Series | None, cfg: dict) -> dict:
    e = cfg["macro"]["ebp"]
    if ebp_df is not None and not ebp_df.empty and "ebp" in ebp_df:
        s = ebp_df["ebp"].dropna()
        v = float(s.iloc[-1])
        out = {
            "value": v, "unit": "pp (excess bond premium)", "as_of": s.index[-1], "percentile": last_pct(s),
            "status": status_from(v, e["amber_above"], e["red_above"]),
            "threshold": f"green <= {e['amber_above']}, amber {e['amber_above']}-{e['red_above']}, red > {e['red_above']}",
            "series": s, "source_used": "Federal Reserve EBP",
        }
        if "est_prob" in ebp_df:
            p = ebp_df["est_prob"].dropna()
            if len(p):
                out["recession_prob_ebp"] = float(p.iloc[-1]) * (100 if p.iloc[-1] <= 1 else 1)
        return out
    if hy is not None and not hy.dropna().empty:
        s = hy.dropna()
        pct = last_pct(s)
        st = "red" if pct >= e["hy_stress_pct"] else ("amber" if pct <= e["hy_red_pct"] else "green")
        return {
            "value": float(s.iloc[-1]), "unit": "% (high-yield OAS, fallback)", "as_of": s.index[-1],
            "percentile": pct, "status": st,
            "threshold": f"red if spread >= {int(e['hy_stress_pct']*100)}th pct (stress); amber if <= "
                         f"{int(e['hy_red_pct']*100)}th pct (complacent credit)",
            "series": s.resample("ME").last(), "source_used": "ICE BofA HY OAS via FRED (fallback; short history)",
        }
    return {"status": "unavailable", "reason": "EBP CSV and HY spread both unavailable", "series": None}


# ----------------------------------------------------------------------------- 5. CAPE
def cape_dial(sh: pd.DataFrame | None, cfg: dict, spot_adjust: float | None = None) -> dict:
    c = cfg["macro"]["cape"]
    if sh is None or sh.empty or sh["cape"].dropna().empty:
        return {"status": "unavailable", "reason": "Shiller data unavailable", "series": None}
    cape = sh["cape"].dropna()
    v = float(cape.iloc[-1]) * (spot_adjust or 1.0)
    hist = cape.copy()
    pct = float((hist <= v).mean())
    # 10-year forward real total return regressed on log CAPE (in-sample, for the range only)
    tr = sh["real_tr_price"].dropna() if "real_tr_price" in sh and sh["real_tr_price"].notna().any() else None
    exp = None
    if tr is not None and len(tr) > 240:
        n = 12 * c["forward_years"]
        fwd = (tr.shift(-n) / tr) ** (1 / c["forward_years"]) - 1
        df = pd.concat([np.log(cape).rename("lc"), fwd.rename("fwd")], axis=1).dropna()
        if len(df) > 120:
            b, a = np.polyfit(df["lc"], df["fwd"], 1)
            resid = df["fwd"] - (a + b * df["lc"])
            mid = a + b * math.log(v)
            sd = resid.std()
            exp = {"median": mid, "low": mid - 1.0 * sd, "high": mid + 1.0 * sd,
                   "r2": float(np.corrcoef(df["lc"], df["fwd"])[0, 1] ** 2), "n_months": int(len(df)),
                   "note": "real, annualised; +/-1 residual s.d. band; in-sample regression since 1881"}
    st = "green" if pct < c["amber_pct"] else ("red" if pct >= c["red_pct"] else "amber")
    return {
        "value": v, "unit": "x (Shiller CAPE)", "as_of": cape.index[-1], "percentile": pct,
        "status": st, "expected_10y_real": exp, "timing_signal": False,
        "threshold": f"percentile vs history since 1881: amber >= {int(c['amber_pct']*100)}th, red >= {int(c['red_pct']*100)}th. "
                     "Sets return expectations only; never a timing signal.",
        "series": cape.resample("ME").last(),
    }
