"""Point-in-time S&P 500 membership, rebuilt from Wikipedia's change log.

Start from today's constituents and walk the "Selected changes" table backwards: for every change
after date t, undo it (drop the stock that was added, restore the one that was removed). Used only
as a survivorship-bias sensitivity check in the stock-level backtest; stocks that left the index and
no longer trade have no free price history, so coverage below 100% is the bias that remains.
"""
from __future__ import annotations

import io

import pandas as pd

from .cache import Fetcher, record
from .universe import yahoo_symbol


def _flat(c) -> str:
    return " ".join(str(x) for x in (c if isinstance(c, tuple) else (c,))).lower()


def _ticker(x):
    if not isinstance(x, str):
        return None
    x = x.strip().split("[")[0].strip()
    return yahoo_symbol(x) if x and x.lower() not in ("nan", "—", "-") else None


def parse_changes(html: str) -> pd.DataFrame:
    tables = pd.read_html(io.StringIO(html))
    seen = []
    for t in tables:
        cols = [_flat(c) for c in t.columns]
        seen.append(cols[:6])
        add_idx = [i for i, c in enumerate(cols) if "added" in c and "date added" not in c]
        rem_idx = [i for i, c in enumerate(cols) if "removed" in c]
        if not add_idx or not rem_idx:
            continue
        add_c = next((i for i in add_idx if "ticker" in cols[i] or "symbol" in cols[i]), add_idx[0])
        rem_c = next((i for i in rem_idx if "ticker" in cols[i] or "symbol" in cols[i]), rem_idx[0])
        date_c = next((i for i, c in enumerate(cols) if "date" in c and "added" not in c and "removed" not in c), 0)
        dates = t.iloc[:, date_c].astype(str).str.replace(r"\[.*?\]", "", regex=True).str.strip()
        out = pd.DataFrame({
            "date": pd.to_datetime(dates, errors="coerce", format="mixed"),
            "added": t.iloc[:, add_c].map(_ticker),
            "removed": t.iloc[:, rem_c].map(_ticker),
        }).dropna(subset=["date"])
        if out.empty:
            continue
        return out.sort_values("date", ascending=False).reset_index(drop=True)
    raise ValueError(f"S&P 500 changes table not found; table headers seen: {seen[:4]}")


def load_changes(f: Fetcher, cfg: dict) -> pd.DataFrame:
    raw = f.get(cfg["sources"]["wiki_sp500"], source="S&P 500 changes (Wikipedia)", ttl_hours=24 * 6)
    ch = parse_changes(raw.decode("utf-8", "ignore"))
    record("S&P 500 changes (Wikipedia)", status="ok", rows=int(len(ch)),
           first=str(ch["date"].min().date()))
    return ch


def members_fn(current: set[str], changes: pd.DataFrame):
    """Return f(t) -> set of S&P 500 members at date t (None before the change log starts)."""
    ch = changes.sort_values("date", ascending=False)
    start = ch["date"].min() if len(ch) else None
    cache: dict = {}

    def f(t):
        t = pd.Timestamp(t)
        if start is None or t < start:
            return None
        if t in cache:
            return cache[t]
        mem = set(current)
        for _, r in ch[ch["date"] > t].iterrows():
            if isinstance(r["added"], str):
                mem.discard(r["added"])
            if isinstance(r["removed"], str):
                mem.add(r["removed"])
        cache[t] = mem
        return mem

    return f
