"""Small numeric helpers shared by every module."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def winsorize(s: pd.Series, p: float = 0.01) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().sum() < 3:
        return s
    lo, hi = s.quantile(p), s.quantile(1 - p)
    return s.clip(lo, hi)


def pct_rank(s: pd.Series) -> pd.Series:
    """Percentile rank in (0, 1]; NaN stays NaN."""
    return pd.to_numeric(s, errors="coerce").rank(pct=True)


def group_pct_rank(s: pd.Series, groups: pd.Series, min_size: int = 5) -> pd.Series:
    """Rank within groups (e.g. GICS sector); small groups fall back to the full cross-section."""
    s = pd.to_numeric(s, errors="coerce")
    out = s.groupby(groups).rank(pct=True)
    sizes = s.groupby(groups).transform(lambda x: x.notna().sum())
    small = sizes < min_size
    if small.any():
        out[small] = s.rank(pct=True)[small]
    return out


def zscore(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    sd = s.std()
    if not sd or np.isnan(sd):
        return s * 0
    return (s - s.mean()) / sd


def expanding_pct(s: pd.Series, min_periods: int = 20) -> pd.Series:
    """Percentile of each value against its own history up to that date (no look-ahead)."""
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    hist: list[float] = []
    for i, v in enumerate(vals):
        if np.isnan(v):
            continue
        hist.append(v)
        if len(hist) >= min_periods:
            arr = np.asarray(hist)
            out[i] = ((arr < v).sum() + 0.5 * (arr == v).sum()) / len(arr)  # mid-rank for ties
    return pd.Series(out, index=s.index)


def last_pct(s: pd.Series) -> float | None:
    s = s.dropna()
    if len(s) < 5:
        return None
    v = s.iloc[-1]
    return float(((s < v).sum() + 0.5 * (s == v).sum()) / len(s))


def status_from(value, green_below=None, red_above=None):
    """Traffic light for 'higher is riskier' dials: green < green_below <= amber <= red_above < red."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "unavailable"
    if red_above is not None and value > red_above:
        return "red"
    if green_below is not None and value < green_below:
        return "green"
    return "amber"


def sparkline(s: pd.Series, n: int = 120) -> list:
    s = s.dropna()
    if s.empty:
        return []
    s = s.iloc[-n:]
    return [[d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d), _r(v)] for d, v in s.items()]


def _r(v, nd: int = 4):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def clean(obj):
    """Make an object JSON-safe (NaN -> None, numpy -> python, Timestamps -> ISO)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return _r(obj, 6)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.strftime("%Y-%m-%d") if obj == obj else None
    if obj is pd.NaT:
        return None
    return obj


def write_json(path, obj) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean(obj), fh, separators=(",", ":"), allow_nan=False)


def month_end(idx) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(idx).to_period("M").to_timestamp("M")


def ann_stats(r: pd.Series, periods: int = 12) -> dict:
    """Annualised performance stats for a return series (decimal)."""
    r = pd.to_numeric(r, errors="coerce").dropna()
    if len(r) < 6:
        return {"n": int(len(r))}
    mu = r.mean() * periods
    sd = r.std() * math.sqrt(periods)
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    t = r.mean() / (r.std() / math.sqrt(len(r))) if r.std() > 0 else np.nan
    cagr = eq.iloc[-1] ** (periods / len(r)) - 1 if eq.iloc[-1] > 0 else -1.0
    return {
        "n": int(len(r)),
        "start": r.index[0],
        "end": r.index[-1],
        "cagr": cagr,
        "ann_mean": mu,
        "ann_vol": sd,
        "sharpe": mu / sd if sd > 0 else np.nan,
        "t_stat": t,
        "max_dd": dd.min(),
        "hit_rate": (r > 0).mean(),
    }
