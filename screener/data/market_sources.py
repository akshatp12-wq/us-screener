"""FINRA short interest, SPDR ETF holdings and earnings dates."""
from __future__ import annotations

import io
import logging
from datetime import date, timedelta

import pandas as pd

from .cache import Fetcher, record

log = logging.getLogger("screener.data")


# ----------------------------------------------------------------------------- FINRA
def _settlement_candidates(today: date, n_back_days: int = 75) -> list[date]:
    """Mid-month and month-end settlement dates (rolled back to a weekday), newest first."""
    out = set()
    d = today
    for _ in range(4):
        for day in (15, None):
            if day:
                c = date(d.year, d.month, 15)
            else:
                nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
                c = nxt - timedelta(days=1)
            for k in range(0, 4):
                x = c - timedelta(days=k)
                if x.weekday() < 5:
                    out.add(x)
        d = date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, 1)
    return sorted([x for x in out if x <= today and (today - x).days <= n_back_days], reverse=True)


def parse_finra(raw: bytes) -> pd.DataFrame:
    text = raw.decode("utf-8", "ignore")
    sep = "|" if text.count("|") > text.count(",") else ","
    df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str)
    cols = {c.lower(): c for c in df.columns}
    sym = next(cols[c] for c in cols if "symbol" in c)
    cur = next(cols[c] for c in cols if "currentshort" in c.replace(" ", "").replace("_", ""))
    adv = next((cols[c] for c in cols if "averagedailyvolume" in c.replace(" ", "").replace("_", "")), None)
    dtc = next((cols[c] for c in cols if "daystocover" in c.replace(" ", "").replace("_", "")), None)
    out = pd.DataFrame({
        "ticker": df[sym].astype(str).str.upper().str.replace(".", "-", regex=False).str.strip(),
        "short_shares": pd.to_numeric(df[cur], errors="coerce"),
        "adv": pd.to_numeric(df[adv], errors="coerce") if adv else None,
        "days_to_cover": pd.to_numeric(df[dtc], errors="coerce") if dtc else None,
    })
    return out.dropna(subset=["short_shares"]).drop_duplicates("ticker")


def load_short_interest(f: Fetcher, cfg: dict) -> tuple[pd.DataFrame, str | None]:
    for d in _settlement_candidates(date.today()):
        url = cfg["sources"]["finra_short"].format(ymd=d.strftime("%Y%m%d"))
        try:
            raw = f.get(url, source="FINRA short interest", ttl_hours=24 * 3, allow_stale=False)
        except Exception:  # noqa: BLE001 - not published yet; try the previous date
            continue
        df = parse_finra(raw)
        if len(df) > 1000:
            record("FINRA short interest", status="ok", error=None, settlement=str(d), rows=int(len(df)),
                   last_obs=str(d))
            return df, str(d)
    record("FINRA short interest", status="error", error="no settlement file found in the last 75 days")
    return pd.DataFrame(columns=["ticker", "short_shares", "adv", "days_to_cover"]), None


# ----------------------------------------------------------------------------- SPDR holdings
def parse_ssga(raw: bytes) -> pd.DataFrame:
    xl = pd.read_excel(io.BytesIO(raw), header=None)
    hdr = None
    for i in range(min(15, len(xl))):
        row = [str(v).strip().lower() for v in xl.iloc[i].tolist()]
        if "ticker" in row and "weight" in row:
            hdr = i
            break
    if hdr is None:
        raise ValueError("holdings header not found")
    body = xl.iloc[hdr + 1:].copy()
    body.columns = [str(v).strip().lower() for v in xl.iloc[hdr].tolist()]
    out = pd.DataFrame({
        "ticker": body["ticker"].astype(str).str.upper().str.replace(".", "-", regex=False).str.strip(),
        "weight": pd.to_numeric(body["weight"], errors="coerce") / 100.0,
    }).dropna()
    return out[out["weight"] > 0]


def load_holdings(f: Fetcher, cfg: dict) -> dict[str, pd.DataFrame]:
    out, errs = {}, []
    for t, meta in cfg["etfs"]["list"].items():
        if meta.get("holdings") != "ssga":
            continue
        url = cfg["sources"]["ssga_holdings"].format(ticker=t.lower())
        try:
            raw = f.get(url, source="SSGA ETF holdings", ttl_hours=24 * 3)
            out[t] = parse_ssga(raw)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{t}: {e}"[:80])
    record("SSGA ETF holdings", status="ok" if out else "error", funds=len(out),
           error="; ".join(errs)[:300] or None)
    return out


# ----------------------------------------------------------------------------- earnings dates
def earnings_dates(tickers: list[str]) -> dict[str, str]:
    """Next earnings date for a short list of tickers (Yahoo). Best effort."""
    out = {}
    try:
        import yfinance as yf
    except ImportError:
        record("Earnings dates", status="error", error="yfinance not installed")
        return out
    today = pd.Timestamp.today().normalize()
    for t in tickers:
        try:
            cal = yf.Ticker(t).calendar
            d = None
            if isinstance(cal, dict):
                v = cal.get("Earnings Date")
                d = v[0] if isinstance(v, (list, tuple)) and v else v
            elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
                d = cal.loc["Earnings Date"].iloc[0]
            if d is not None and pd.Timestamp(d) >= today - pd.Timedelta(days=1):
                out[t] = pd.Timestamp(d).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
    record("Earnings dates", status="ok" if out else "error", tickers=len(out),
           error=None if out else "no earnings dates returned")
    return out
