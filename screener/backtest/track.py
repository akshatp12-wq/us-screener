"""Live track record: snapshot each nightly run's top picks and measure them going forward."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..util import utcnow, write_json


def save_snapshot(track_dir: Path, date: str, picks: dict[str, list[str]], prices: dict[str, float]) -> Path:
    track_dir.mkdir(parents=True, exist_ok=True)
    p = track_dir / f"{date}.json"
    if not p.exists():
        write_json(p, {"date": date, "created": utcnow(), "picks": picks, "entry_prices": prices})
    return p


def evaluate(track_dir: Path, ret_d: pd.DataFrame, bench: str = "SPY") -> dict:
    snaps = sorted(track_dir.glob("*.json")) if track_dir.exists() else []
    rows = []
    for p in snaps:
        s = json.loads(p.read_text())
        d0 = pd.Timestamp(s["date"])
        win = ret_d.loc[ret_d.index > d0]
        if win.empty:
            continue
        cum = np.expm1(np.log1p(win).sum(min_count=1))
        b = float(cum.get(bench, np.nan))
        for mode, names in s["picks"].items():
            have = [t for t in names if t in cum and pd.notna(cum[t])]
            if not have:
                continue
            r = float(cum[have].mean())
            rows.append({"date": s["date"], "mode": mode, "n": len(have), "return": r, "spy": b,
                         "excess": r - b, "days": int(len(win))})
    df = pd.DataFrame(rows)
    out = {"snapshots": len(snaps), "rows": rows[-200:]}
    if len(df):
        agg = {}
        for mode, g in df.groupby("mode"):
            mature = g[g["days"] >= 21]
            agg[mode] = {"snapshots": int(len(g)), "mean_excess": float(g["excess"].mean()),
                         "hit_rate": float((g["excess"] > 0).mean()),
                         "mature_snapshots": int(len(mature)),
                         "mean_excess_mature": float(mature["excess"].mean()) if len(mature) else None}
        out["by_mode"] = agg
    out["note"] = ("Each snapshot = equal-weighted top picks on that date, held to today, vs SPY. Overlapping "
                   "snapshots are not independent; give this at least a year before reading anything into it.")
    return out
