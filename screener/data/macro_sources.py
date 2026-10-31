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
    if not np.isfinite(d) or d < 1800:
        return None
    year = int(d)
    month = int(round((d - year) * 100))
    if month == 0 or month > 12:
        return None
    return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)


def parse_shiller(raw: bytes) -> pd.DataFrame:
    """Parse Shiller's ie_data sheet. Headers span several rows, so each column's label is the
    joined text of the rows above the first date row; columns are then picked by label and
    checked for plausible values."""
    xl = pd.read_excel(io.BytesIO(raw), sheet_name="Data", header=None)
    dates = xl.iloc[:, 0].map(_shiller_date)
    first = dates.first_valid_index()
    if first is None:
        raise ValueError("Shiller sheet: no date rows")
    top = max(0, first - 6)
    labels = []
    for j in range(xl.shape[1]):
        parts = [str(v).strip() for v in xl.iloc[top:first, j].tolist() if str(v).strip() not in ("", "nan", "NaN")]
        labels.append(" ".join(parts))
    body = xl.iloc[first:].copy()
    body.index = pd.DatetimeIndex(dates.iloc[first:])
    body = body[body.index.notna()]

    def num(j):
        return pd.to_numeric(body.iloc[:, j], errors="coerce")

    def find(pred, lo=None, hi=None):
        for j, lab in enumerate(labels):
            if j == 0 or not pred(lab.lower()):
                continue
            v = num(j)
            med = v.median()
            if v.notna().sum() > 100 and (lo is None or lo <= med <= hi):
                return v
        return pd.Series(np.nan, index=body.index)

    cape = find(lambda l: ("cape" in l or "p/e10" in l) and "tr " not in l and not l.startswith("tr")
                and "total return" not in l and "excess" not in l and "yield" not in l, 4, 60)
    out = pd.DataFrame({
        "price": num(1) if labels and xl.shape[1] > 1 else np.nan,
        "dividend": num(2), "earnings": num(3), "cpi": num(4),
        "real_price": find(lambda l: "real" in l and "price" in l and "total" not in l),
        "real_tr_price": find(lambda l: "total return price" in l or ("real" in l and "tr price" in l)),
        "cape": cape,
    })
    if out["cape"].notna().sum() < 100:
        raise ValueError(f"Shiller sheet: CAPE column not recognised; labels={labels[:20]}")
    return out.dropna(how="all")


def shiller(f: Fetcher, cfg: dict) -> pd.DataFrame:
    """Shiller moved his data to shillerdata.com in 2024; the old Yale file is frozen. Try the new
    site first, then Yale, and keep whichever is more recent."""
    best, err = None, None
    try:
        page = f.get(cfg["sources"]["shiller_page"], source="Shiller CAPE (page)", ttl_hours=24).decode("utf-8", "ignore")
        m = re.search(r'(https?:)?//[^"\'\s>]*ie_data\.xls[^"\'\s>]*', page)
        if m:
            url = m.group(0).replace("&amp;", "&")
            url = "https:" + url if url.startswith("//") else url
            best = parse_shiller(f.get(url, source="Shiller CAPE", ttl_hours=24 * 7, timeout=120))
    except Exception as e:  # noqa: BLE001
        err = e
    if best is None or best["cape"].dropna().index[-1] < pd.Timestamp.today() - pd.Timedelta(days=200):
        try:
            alt = parse_shiller(f.get(cfg["sources"]["shiller_xls"], source="Shiller CAPE (Yale)", ttl_hours=24 * 7, timeout=120))
            if best is None or alt["cape"].dropna().index[-1] > best["cape"].dropna().index[-1]:
                best = alt
        except Exception as e:  # noqa: BLE001
            err = err or e
    if best is None:
        raise err or RuntimeError("Shiller data unavailable")
    last = best["cape"].dropna()
    stale = last.index[-1] < pd.Timestamp.today() - pd.Timedelta(days=200)
    record("Shiller CAPE", rows=len(best), last_obs=str(last.index[-1].date()),
           status="stale" if stale else "ok", error="latest Shiller file is more than 6 months old" if stale else None)
    return best


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
    sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=None)
    for sh in sheets.values():
        for i in range(min(20, len(sh))):
            row = [str(v).strip() for v in sh.iloc[i].tolist()]
            scol = [j for j, v in enumerate(row) if v.upper().startswith("SENT")]
            if not scol:
                continue
            body = sh.iloc[i + 1:]
            for j in range(sh.shape[1]):
                v = pd.to_numeric(body.iloc[:, j], errors="coerce")
                ok = v.between(195001, 210012) & (v % 100).between(1, 12)
                if ok.sum() > 100 and ok[v.notna()].mean() > 0.95:
                    pref = [k for k in scol if row[k].endswith("^") or "⊥" in row[k]] or scol
                    s_ = pd.to_numeric(body.iloc[:, pref[0]], errors="coerce")
                    idx = pd.to_datetime(v[ok].astype(int).astype(str), format="%Y%m") + pd.offsets.MonthEnd(0)
                    out = pd.Series(s_[ok].to_numpy(), index=idx).dropna()
                    if len(out) > 100:
                        return out.rename("baker_wurgler")
    raise ValueError("Baker-Wurgler file: no SENT column next to a YYYYMM date column")


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
