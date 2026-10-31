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


def parse_changes(html: str) -> pd.DataFrame:
    tables = pd.read_html(io.StringIO(html))
    for t in tables:
        cols = [" ".join(str(x) for x in (c if isinstance(c, tuple) else (c,))).lower() for c in t.columns]
        if not any("added" in c for c in cols) or not any("removed" in c for c in cols):
            continue
        date_c = next((i for i, c in enumerate(cols) if "date" in c), None)
        add_c = next((i for i, c in enumerate(cols) if "added" in c and ("ticker" in c or "symbol" in c)), None)
        rem_c = next((i for i, c in enumerate(cols) if "removed" in c and ("ticker" in c or "symbol" in c)), None)
        if date_c is None or add_c is None or rem_c is None:
            continue
        out = pd.DataFrame({
            "date": pd.to_datetime(t.iloc[:, date_c], errors="coerce"),
            "added": t.iloc[:, add_c].map(lambda x: yahoo_symbol(x) if isinstance(x, str) and x.strip() else None),
            "removed": t.iloc[:, rem_c].map(lambda x: yahoo_symbol(x) if isinstance(x, str) and x.strip() else None),
        }).dropna(subset=["date"])
        return out.sort_values("date", ascending=False).reset_index(drop=True)
    raise ValueError("S&P 500 changes table not found")


def load_changes(f: Fetcher, cfg: dict) -> pd.DataFrame:
    raw = f.get(cfg["sources"]["wiki_sp500"], source="S&P 500 changes (Wikipedia)", ttl_hours=24 * 6)
    ch = parse_changes(raw.decode("utf-8", "ignore"))
    record("S&P 500 changes (Wikipedia)", status="ok", rows=int(len(ch)),
           first=str(ch["date"].min().date()) if len(ch) else None)
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
            if r["added"]:
                mem.discard(r["added"])
            if r["removed"]:
                mem.add(r["removed"])
        cache[t] = mem
        return mem

    return f
