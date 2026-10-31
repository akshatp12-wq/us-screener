"""Turn raw stock signals into within-sector scores, mode composites, flags and a one-line reason."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..util import group_pct_rank, winsorize

SIGNAL_LABELS = {
    "value": "value", "momentum": "momentum", "profitability": "profitability",
    "investment": "low asset growth", "issuance": "low net issuance", "insiders": "insider buying",
    "short_interest": "low short interest", "sue": "earnings surprise", "lowvol": "low volatility",
}


def signal_scores(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Within-GICS-sector percentile scores in (0, 1]; higher is better for every signal."""
    s = cfg["signals"]
    p, mn = s["winsor"], s["min_sector_size"]
    g = df["sector"].fillna("Unknown")

    def r(col, sign=1):
        return group_pct_rank(sign * winsorize(df[col], p), g, mn)

    out = pd.DataFrame(index=df.index)
    out["value"] = pd.concat([r("bm"), r("ep")], axis=1).mean(axis=1, skipna=True)
    out["momentum"] = r("momentum")
    out["profitability"] = r("gpa")
    out["investment"] = r("asset_growth", -1)
    iss = r("net_issuance", -1)
    out["issuance"] = (iss - 0.25 * df["young"].fillna(False).astype(float)).clip(lower=0.0)
    out.loc[iss.isna() & df["young"].fillna(False), "issuance"] = 0.1
    ins = df["insider_net"]
    out["insiders"] = group_pct_rank(ins, g, mn) if ins.notna().any() else np.nan
    out["short_interest"] = r("si_ratio", -1) if df["si_ratio"].notna().any() else np.nan
    out["sue"] = r("sue") if df["sue"].notna().any() else np.nan
    out["lowvol"] = pd.concat([r("vol", -1), r("beta", -1)], axis=1).mean(axis=1, skipna=True)
    return out


def composite(scores: pd.DataFrame, weights: dict, min_coverage: float = 0.6,
              momentum_multiplier: float = 1.0) -> pd.Series:
    w = {k: v * (momentum_multiplier if k == "momentum" else 1.0) for k, v in weights.items()}
    cols = [k for k in w if k in scores]
    W = pd.Series({k: w[k] for k in cols})
    S = scores[cols]
    avail = S.notna().mul(W, axis=1)
    num = S.fillna(0).mul(W, axis=1).sum(axis=1)
    den = avail.sum(axis=1)
    out = num / den.replace(0, np.nan)
    return out.where(den >= min_coverage * W.sum())


def euphoric_penalty(df: pd.DataFrame, cfg: dict) -> pd.Series:
    e = cfg["euphoric_penalty"]
    small = df["mcap"].rank(pct=True) <= e["small_pct"]
    volatile = df["vol"].rank(pct=True) >= e["vol_pct"]
    young = df["young"].fillna(False)
    unprof = df["net_income_ttm"] < 0
    n = small.astype(int) + volatile.astype(int) + young.astype(int) + unprof.fillna(False).astype(int)
    return e["amount"] * n


def why_ranked(scores: pd.DataFrame, weights: dict, sector: pd.Series) -> pd.Series:
    out = {}
    keys = [k for k in weights if k in scores]
    for t, row in scores[keys].iterrows():
        good = sorted([(k, row[k]) for k in keys if pd.notna(row[k]) and row[k] >= 0.75], key=lambda x: -x[1])
        bad = sorted([(k, row[k]) for k in keys if pd.notna(row[k]) and row[k] <= 0.25], key=lambda x: x[1])
        parts = []
        if good:
            parts.append("Strong " + ", ".join(f"{SIGNAL_LABELS[k]} ({v:.0%})" for k, v in good[:3]))
        if bad:
            parts.append("weak " + ", ".join(f"{SIGNAL_LABELS[k]} ({v:.0%})" for k, v in bad[:2]))
        missing = [SIGNAL_LABELS[k] for k in keys if pd.isna(row[k])]
        txt = "; ".join(parts) if parts else "No standout signals"
        txt += f" vs {sector.get(t, 'sector')} peers"
        if missing:
            txt += f" (no data: {', '.join(missing[:3])})"
        out[t] = txt
    return pd.Series(out)


def flags(df: pd.DataFrame, cfg: dict, bubble_sectors: set | None = None,
          earnings: dict | None = None, today=None) -> pd.Series:
    si = cfg["signals"]["short_interest"]
    u = cfg["universe"]
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    out = {}
    for t, r in df.iterrows():
        fl = []
        if pd.notna(r.get("si_ratio")) and r["si_ratio"] >= si["squeeze_si"] and (r.get("days_to_cover") or 0) >= si["squeeze_dtc"]:
            fl.append("squeeze risk")
        if pd.notna(r.get("si_ratio")) and r["si_ratio"] >= si["hard_to_borrow_si"]:
            fl.append("hard to borrow")
        if bool(r.get("young")):
            fl.append("young listing")
        if not (pd.notna(r.get("price")) and r["price"] >= u["min_price"] and
                pd.notna(r.get("dollar_volume")) and r["dollar_volume"] >= u["min_dollar_volume"]):
            fl.append("illiquid")
        if bubble_sectors and r.get("sector") in bubble_sectors:
            fl.append("bubble sector")
        if earnings and t in earnings:
            d = pd.Timestamp(earnings[t])
            if today <= d <= today + pd.Timedelta(days=7):
                fl.append("earnings this week")
        out[t] = fl
    return pd.Series(out)


def score_all_modes(df: pd.DataFrame, cfg: dict, regime: dict, crash: dict) -> dict:
    """Scores for every mode. Returns {'scores': DataFrame, mode: Series...}."""
    sc = signal_scores(df, cfg)
    pen = euphoric_penalty(df, cfg) if regime.get("euphoric") else pd.Series(0.0, index=df.index)
    res = {"scores": sc, "penalty": pen}
    for mode, m in cfg["modes"].items():
        mult = crash.get("momentum_multiplier", 1.0) if m.get("crash_guard") else 1.0
        res[mode] = (composite(sc, m["weights"], momentum_multiplier=mult) - pen).clip(lower=0)
    return res
