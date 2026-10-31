"""Free macro / factor sources: FRED, Ken French data library, Shiller CAPE, Fed EBP, Baker-Wurgler."""
from __future__ import annotations

import io
import logging
import re
import zipfile

import numpy as np
import pandas as pd

from .cache import Fetcher, FetchError, record

log = logging.getLogger("screener.data")


# ----------------------------------------------------------------------------- FRED
def parse_fred_csv(raw: bytes, series: str) -> pd.Series:
    df = pd.read_csv(io.BytesIO(raw))
    date_col = df.columns[0]
    val_col = series if series in df.columns else df.columns[-1]
    s = pd.to_numeric(df[val_col], errors="coerce")
    s.index = pd.to_datetime(df[date_col])
    s.name = series
    return s.dropna()


def fred(f: Fetcher, cfg: dict, series: str, ttl_hours: float = 12) -> pd.Series:
    url = cfg["sources"]["fred_csv"].format(series=series)
    raw = f.get(url, source=f"FRED:{series}", ttl_hours=ttl_hours)
    s = parse_fred_csv(raw, series)
    record(f"FRED:{series}", rows=len(s), last_obs=str(s.index[-1].date()) if len(s) else None)
    return s


# ----------------------------------------------------------------------------- Ken French
_DATA_ROW = re.compile(r"^\s*(\d{4}|\d{6}|\d{8})\s*,")


def parse_french_csv(text: str) -> dict[str, pd.DataFrame]:
    """Parse a Ken French CSV (possibly several tables) into {title: DataFrame} of decimal returns."""
    lines = text.splitlines()
    tables: dict[str, pd.DataFrame] = {}
    title = "main"
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith(",") and i + 1 < len(lines) and _DATA_ROW.match(lines[i + 1]):
            header = [h.strip() for h in stripped.split(",")[1:]]
            rows, idx = [], []
            j = i + 1
            while j < len(lines) and _DATA_ROW.match(lines[j]):
                parts = [p.strip() for p in lines[j].split(",")]
                idx.append(parts[0])
                rows.append(parts[1:1 + len(header)])
                j += 1
            df = pd.DataFrame(rows, columns=header[:len(rows[0])] if rows else header)
            df = df.apply(pd.to_numeric, errors="coerce")
            df = df.mask(df <= -99.99)
            n = len(idx[0])
            if n == 6:
                df.index = pd.to_datetime(idx, format="%Y%m") + pd.offsets.MonthEnd(0)
                freq = "monthly"
            elif n == 8:
                df.index = pd.to_datetime(idx, format="%Y%m%d")
                freq = "daily"
            else:
                df.index = pd.to_datetime(idx, format="%Y") + pd.offsets.YearEnd(0)
                freq = "annual"
            key = f"{title} [{freq}]" if title != "main" else f"main [{freq}]"
            base, k = key, 2
            while key in tables:
                key = f"{base} #{k}"
                k += 1
            tables[key] = df
            i = j
            continue
        if stripped and not _DATA_ROW.match(line):
            title = stripped.strip(",").strip()
        i += 1
    return tables


def french(f: Fetcher, cfg: dict, name: str, ttl_hours: float = 24 * 7) -> dict[str, pd.DataFrame]:
    url = cfg["sources"]["french_zip"].format(name=name)
    raw = f.get(url, source=f"French:{name}", ttl_hours=ttl_hours, timeout=120)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    member = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt"))][0]
    text = zf.read(member).decode("latin-1")
    tables = parse_french_csv(text)
    first = next(iter(tables.values())) if tables else None
    record(f"French:{name}", tables=len(tables),
           last_obs=str(first.index[-1].date()) if first is not None and len(first) else None)
    return {k: v / 100.0 for k, v in tables.items()}


def french_table(tables: dict[str, pd.DataFrame], contains: str = "", freq: str = "monthly") -> pd.DataFrame:
    """Pick the first table whose title contains `contains` (case-insensitive) at a frequency."""
    for k, v in tables.items():
        if f"[{freq}]" in k and contains.lower() in k.lower():
            return v
    for k, v in tables.items():
        if f"[{freq}]" in k:
            return v
    raise KeyError(f"no {freq} table matching '{contains}'")


# ----------------------------------------------------------------------------- Shiller
def _shiller_date(x) -> pd.Timestamp | None:
    try:
        d = float(x)
    except (TypeError, ValueError):
        return None
    year = int(d)
    month = int(round((d - year) * 100))
    if month == 0 or month > 12:
        return None
    return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)


def parse_shiller(raw: bytes) -> pd.DataFrame:
    xl = pd.read_excel(io.BytesIO(raw), sheet_name="Data", header=None)
    hdr_row = None
    for i in range(min(20, len(xl))):
        row = [str(v).strip() for v in xl.iloc[i].tolist()]
        if "Date" in row and any("CAPE" in v for v in row):
            hdr_row = i
            break
    if hdr_row is None:
        raise ValueError("Shiller sheet header not found")
    header = [str(v).strip() for v in xl.iloc[hdr_row].tolist()]
    body = xl.iloc[hdr_row + 1:].copy()
    body.columns = header
    dates = body["Date"].map(_shiller_date)
    body = body[dates.notna()]
    body.index = pd.DatetimeIndex(dates[dates.notna()])

    def col(pred):
        for c in body.columns:
            if pred(c):
                return pd.to_numeric(body[c], errors="coerce")
        return pd.Series(np.nan, index=body.index)

    out = pd.DataFrame({
        "price": col(lambda c: c == "P"),
        "dividend": col(lambda c: c == "D"),
        "earnings": col(lambda c: c == "E"),
        "cpi": col(lambda c: c == "CPI"),
        "real_price": col(lambda c: c.lower() == "real price"),
        "real_tr_price": col(lambda c: "real total return price" in c.lower() or c.lower() == "real tr price"),
        "cape": col(lambda c: c.upper() in ("CAPE", "P/E10", "CYCLICALLY ADJUSTED PRICE EARNINGS RATIO P/E10 OR CAPE")),
    })
    if out["cape"].isna().all():
        out["cape"] = col(lambda c: "CAPE" in c.upper() and "TR" not in c.upper() and "EXCESS" not in c.upper())
    return out.dropna(how="all")


def shiller(f: Fetcher, cfg: dict) -> pd.DataFrame:
    raw = f.get(cfg["sources"]["shiller_xls"], source="Shiller CAPE", ttl_hours=24 * 7, timeout=120)
    df = parse_shiller(raw)
    last = df["cape"].dropna()
    record("Shiller CAPE", rows=len(df), last_obs=str(last.index[-1].date()) if len(last) else None)
    return df


# ----------------------------------------------------------------------------- Fed EBP
def parse_ebp(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip().lower() for c in df.columns]
    dcol = [c for c in df.columns if "date" in c][0]
    df.index = pd.to_datetime(df[dcol]) + pd.offsets.MonthEnd(0)
    keep = [c for c in ("gz_spread", "ebp", "est_prob") if c in df.columns]
    return df[keep].apply(pd.to_numeric, errors="coerce")


def ebp(f: Fetcher, cfg: dict) -> pd.DataFrame:
    raw = f.get(cfg["sources"]["ebp_csv"], source="Fed EBP", ttl_hours=24 * 3)
    df = parse_ebp(raw)
    record("Fed EBP", rows=len(df), last_obs=str(df.index[-1].date()) if len(df) else None)
    return df


# ----------------------------------------------------------------------------- Baker-Wurgler
def parse_bw(raw: bytes) -> pd.Series:
    xl = pd.read_excel(io.BytesIO(raw), sheet_name=0)
    xl.columns = [str(c).strip() for c in xl.columns]
    ycol = [c for c in xl.columns if "yearmo" in c.lower() or c.lower() == "date"][0]
    scol = [c for c in xl.columns if c.upper().startswith("SENT")]
    # prefer the orthogonalised level index ("SENT^" / "SENT⊥"), else the first SENT column
    pref = [c for c in scol if c.endswith("^") or "⊥" in c] or scol
    s = pd.to_numeric(xl[pref[0]], errors="coerce")
    idx = pd.to_datetime(xl[ycol].astype(int).astype(str), format="%Y%m") + pd.offsets.MonthEnd(0)
    s.index = idx
    return s.dropna().rename("baker_wurgler")


def baker_wurgler(f: Fetcher, cfg: dict) -> pd.Series:
    raw = f.get(cfg["sources"]["baker_wurgler_xlsx"], source="Baker-Wurgler", ttl_hours=24 * 30)
    s = parse_bw(raw)
    record("Baker-Wurgler", rows=len(s), last_obs=str(s.index[-1].date()) if len(s) else None)
    return s


def safe(fn, *a, default=None, source: str | None = None, **kw):
    """Call a loader; on failure log it, mark the source as errored and return `default`."""
    try:
        return fn(*a, **kw)
    except Exception as e:  # noqa: BLE001 - every source must fail soft
        log.warning("source failed: %s: %s", source or getattr(fn, "__name__", fn), e)
        if source:
            record(source, status="error", error=f"{type(e).__name__}: {e}"[:300])
        return default
