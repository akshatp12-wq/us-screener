"""Daily prices: Yahoo Finance (yfinance) with Stooq as a free fallback.

Stored panels (date x ticker), cached as pickles and updated incrementally:
  ret    daily total return (from adjusted close; stable as history is re-adjusted)
  close  unadjusted-for-dividends close (split-adjusted by Yahoo) - used for price filters
  volume daily share volume
  splits split ratio on ex-date (1.0 otherwise)
"""
from __future__ import annotations

import io
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .cache import Fetcher, record

log = logging.getLogger("screener.data")
PANELS = ("ret", "close", "volume", "splits")


def _yf_download(tickers: list[str], start: str | None = None, period: str | None = None) -> dict[str, pd.DataFrame]:
    import yfinance as yf  # imported lazily so tests run without it

    out = {p: [] for p in PANELS}
    for i in range(0, len(tickers), 150):
        batch = tickers[i:i + 150]
        for attempt in range(3):
            try:
                df = yf.download(batch, start=start, period=period, auto_adjust=False, actions=True,
                                 group_by="column", threads=True, progress=False, timeout=30)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("yfinance batch failed (%s), retry %d", e, attempt)
                time.sleep(5 * (attempt + 1))
                df = None
        if df is None or df.empty:
            continue
        if not isinstance(df.columns, pd.MultiIndex):
            df.columns = pd.MultiIndex.from_product([df.columns, batch[:1]])
        adj = df["Adj Close"] if "Adj Close" in df.columns.get_level_values(0) else df["Close"]
        out["ret"].append(adj.pct_change(fill_method=None))
        out["close"].append(df["Close"])
        out["volume"].append(df["Volume"])
        if "Stock Splits" in df.columns.get_level_values(0):
            sp = df["Stock Splits"].replace(0, np.nan).fillna(1.0)
        else:
            sp = pd.DataFrame(1.0, index=df.index, columns=adj.columns)
        out["splits"].append(sp)
        time.sleep(1.0)
    res = {}
    for p in PANELS:
        if out[p]:
            d = pd.concat(out[p], axis=1)
            d = d.loc[:, ~d.columns.duplicated()]
            d.index = pd.DatetimeIndex(d.index).tz_localize(None).normalize()
            res[p] = d
    return res


def _stooq_one(f: Fetcher, cfg: dict, ticker: str) -> pd.DataFrame | None:
    sym = ticker.lower().replace(".", "-")
    sym = sym[1:] if sym.startswith("^") else f"{sym}.us"
    url = cfg["sources"]["stooq_daily"].format(symbol=sym)
    try:
        raw = f.get(url, source="Stooq", ttl_hours=12, max_rps=1)
        df = pd.read_csv(io.BytesIO(raw))
        if "Close" not in df.columns or df.empty:
            return None
        df.index = pd.to_datetime(df["Date"])
        return df
    except Exception as e:  # noqa: BLE001
        log.info("stooq miss %s: %s", ticker, e)
        return None


def _load(cache: Path) -> dict[str, pd.DataFrame]:
    res = {}
    for p in PANELS:
        fp = cache / f"px_{p}.pkl"
        if fp.exists():
            res[p] = pd.read_pickle(fp).astype("float64")
    return res


def _save(cache: Path, panels: dict[str, pd.DataFrame]) -> None:
    for p, d in panels.items():
        if d is None:
            continue
        # float32 keeps the Actions cache small; ample precision for returns, prices and volumes
        d.astype("float32").to_pickle(cache / f"px_{p}.pkl")


def _merge(old: pd.DataFrame | None, new: pd.DataFrame | None) -> pd.DataFrame | None:
    if old is None:
        return new
    if new is None or new.empty:
        return old
    cols = old.columns.union(new.columns)
    idx = old.index.union(new.index)
    out = old.reindex(index=idx, columns=cols)
    nn = new.reindex(index=idx, columns=cols)
    return nn.combine_first(out)


def update_prices(f: Fetcher, cfg: dict, cache: Path, tickers: list[str], full: bool = False,
                  intraday: bool = False) -> dict[str, pd.DataFrame]:
    """Bring the price panels up to date for `tickers`. Returns the panels."""
    tickers = sorted(set(tickers))
    panels = _load(cache)
    have = set(panels["ret"].columns) if "ret" in panels else set()
    missing = [t for t in tickers if t not in have]
    start = cfg["data"]["price_history_start"]
    try:
        if full or not panels:
            fresh = _yf_download(tickers, start=start)
        else:
            fresh = _yf_download(sorted(have & set(tickers)), period="5d" if intraday else "1mo")
            if missing:
                more = _yf_download(missing, start=start)
                for p in PANELS:
                    fresh[p] = _merge(fresh.get(p), more.get(p))
        status = "ok" if fresh.get("ret") is not None else "error"
        err = None if status == "ok" else "yfinance returned no data (Yahoo may be blocking this runner)"
    except ImportError:
        fresh, status, err = {}, "error", "yfinance not installed"
    for p in PANELS:
        panels[p] = _merge(panels.get(p), fresh.get(p))

    # Stooq fallback for tickers with no recent data
    if panels.get("ret") is not None:
        recent = panels["ret"].iloc[-5:].notna().any()
        stale = [t for t in tickers if t not in recent.index or not recent.get(t, False)]
    else:
        stale = tickers
    if stale and len(stale) <= 300:
        got = 0
        for t in stale:
            df = _stooq_one(f, cfg, t)
            if df is None:
                continue
            got += 1
            new = {"ret": df[["Close"]].pct_change().rename(columns={"Close": t}),
                   "close": df[["Close"]].rename(columns={"Close": t}),
                   "volume": df[["Volume"]].rename(columns={"Volume": t}) if "Volume" in df else None,
                   "splits": None}
            for p, d in new.items():
                if d is not None:
                    panels[p] = _merge(panels.get(p), d)
        record("Stooq", status="ok" if got else "error", filled=got, requested=len(stale),
               error=None if got else "no fallback data")
    if panels.get("ret") is not None:
        panels["ret"] = panels["ret"].sort_index()
        for p in ("close", "volume", "splits"):
            if panels.get(p) is not None:
                panels[p] = panels[p].sort_index()
        _save(cache, panels)
        last = panels["ret"].dropna(how="all").index[-1]
        record("Yahoo prices", status=status, error=err, last_obs=str(last.date()),
               tickers=int(panels["ret"].shape[1]))
    else:
        record("Yahoo prices", status="error", error=err or "no price data")
    return panels


def total_return_index(ret: pd.DataFrame) -> pd.DataFrame:
    return (1 + ret.fillna(0)).cumprod()


def monthly_returns(ret: pd.DataFrame) -> pd.DataFrame:
    m = (1 + ret.fillna(0)).resample("ME").prod() - 1
    valid = ret.notna().resample("ME").sum() > 0
    return m.where(valid)


def dollar_volume(panels: dict, window: int = 63) -> pd.Series:
    if panels.get("close") is None or panels.get("volume") is None:
        return pd.Series(dtype=float)
    dv = (panels["close"] * panels["volume"]).iloc[-window:]
    return dv.median()
