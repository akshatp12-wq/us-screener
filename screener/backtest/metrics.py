"""Performance and validation statistics."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..util import ann_stats


def newey_west_t(x: pd.Series, lags: int | None = None) -> float:
    """t-statistic of the mean with Newey-West (Bartlett) standard errors."""
    x = pd.to_numeric(x, errors="coerce").dropna().to_numpy()
    n = len(x)
    if n < 12:
        return float("nan")
    L = lags if lags is not None else int(math.floor(4 * (n / 100) ** (2 / 9)))
    e = x - x.mean()
    s = (e @ e) / n
    for l in range(1, L + 1):
        w = 1 - l / (L + 1)
        s += 2 * w * (e[l:] @ e[:-l]) / n
    se = math.sqrt(max(s, 1e-18) / n)
    return float(x.mean() / se)


def ols(y: pd.Series, X: pd.DataFrame, nw_lags: int | None = None) -> dict:
    """OLS with Newey-West t-stats. Returns coefficients and t-stats."""
    d = pd.concat([y.rename("y"), X], axis=1).dropna()
    if len(d) < 24:
        return {"n": int(len(d))}
    Y = d["y"].to_numpy()
    Xm = np.column_stack([np.ones(len(d)), d[X.columns].to_numpy()])
    beta, *_ = np.linalg.lstsq(Xm, Y, rcond=None)
    resid = Y - Xm @ beta
    n, k = Xm.shape
    L = nw_lags if nw_lags is not None else int(math.floor(4 * (n / 100) ** (2 / 9)))
    XtX_inv = np.linalg.pinv(Xm.T @ Xm)
    u = Xm * resid[:, None]
    S = u.T @ u
    for l in range(1, L + 1):
        w = 1 - l / (L + 1)
        G = u[l:].T @ u[:-l]
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.maximum(np.diag(V), 1e-18))
    names = ["const"] + list(X.columns)
    r2 = 1 - resid.var() / Y.var() if Y.var() > 0 else float("nan")
    return {"n": int(n), "coef": dict(zip(names, beta.tolist())), "t": dict(zip(names, (beta / se).tolist())),
            "r2": float(r2)}


def capm(r: pd.Series, mkt_excess: pd.Series, rf: pd.Series | None = None) -> dict:
    y = r - rf.reindex(r.index).fillna(0) if rf is not None else r
    res = ols(y, mkt_excess.rename("mkt").to_frame())
    if "coef" not in res:
        return {}
    return {"alpha_ann": res["coef"]["const"] * 12, "alpha_t": res["t"]["const"], "beta": res["coef"]["mkt"]}


def drawdown(r: pd.Series) -> pd.Series:
    eq = (1 + r.fillna(0)).cumprod()
    return eq / eq.cummax() - 1


def mp_split(r: pd.Series, pub: str | pd.Timestamp) -> dict:
    """McLean & Pontiff (2016): performance before vs after publication."""
    pub = pd.Timestamp(pub)
    pre, post = r[r.index <= pub].dropna(), r[r.index > pub].dropna()
    out = {"publication": pub, "pre": ann_stats(pre), "post": ann_stats(post)}
    if len(pre) > 12 and len(post) > 12 and pre.mean() != 0:
        out["post_over_pre"] = float(post.mean() / pre.mean())
        out["pre_t_nw"] = newey_west_t(pre)
        out["post_t_nw"] = newey_west_t(post)
    return out


def rolling_sharpe(r: pd.Series, window: int = 36) -> pd.Series:
    m = r.rolling(window, min_periods=window).mean()
    s = r.rolling(window, min_periods=window).std()
    return (m / s * math.sqrt(12)).dropna()


def signal_health(r: pd.Series, window: int = 36, ratio: float = 0.5) -> dict:
    rs = rolling_sharpe(r, window)
    full = ann_stats(r).get("sharpe")
    if rs.empty or full is None or np.isnan(full):
        return {"status": "unavailable"}
    last = float(rs.iloc[-1])
    if full <= 0:
        st = "red"
    elif last < 0:
        st = "red"
    elif last < ratio * full:
        st = "amber"
    else:
        st = "green"
    return {"rolling_sharpe_36m": last, "full_sharpe": float(full), "status": st,
            "decay_flag": st != "green", "series": rs}


def auc(score: pd.Series, label: pd.Series) -> float:
    d = pd.concat([score.rename("s"), label.rename("y")], axis=1).dropna()
    pos, neg = d[d["y"] == 1]["s"], d[d["y"] == 0]["s"]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    ranks = d["s"].rank()
    return float((ranks[d["y"] == 1].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def forward_max_drawdown(price: pd.Series, horizon: int) -> pd.Series:
    """Worst peak-to-trough decline within the next `horizon` periods, measured from each date."""
    p = price.to_numpy(dtype=float)
    out = np.full(len(p), np.nan)
    for i in range(len(p) - 1):
        w = p[i:i + horizon + 1]
        if len(w) < horizon + 1:
            break
        peak = np.maximum.accumulate(w)
        out[i] = (w / peak - 1).min()
    return pd.Series(out, index=price.index)


def equity_curve(r: pd.Series) -> list:
    r = r.dropna()
    eq = (1 + r).cumprod()
    return [[d.strftime("%Y-%m"), round(float(v), 5)] for d, v in eq.items()]
