"""S&P 1500 universe with GICS sectors (Wikipedia constituent tables) and SEC CIK mapping."""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pandas as pd

from .cache import Fetcher, record

log = logging.getLogger("screener.data")


def yahoo_symbol(sym: str) -> str:
    return str(sym).strip().upper().replace(".", "-")


def parse_wiki_constituents(html: str, index_name: str) -> pd.DataFrame:
    tables = pd.read_html(io.StringIO(html))
    for t in tables:
        cols = {str(c).lower(): c for c in t.columns}
        sym = next((cols[c] for c in cols if "symbol" in c or c.startswith("ticker")), None)
        sec = next((cols[c] for c in cols if "gics sector" in c or c == "sector"), None)
        if sym is None or sec is None or len(t) < 50:
            continue
        name = next((cols[c] for c in cols if c in ("security", "company", "name")), None)
        sub = next((cols[c] for c in cols if "sub-industry" in c or "sub industry" in c), None)
        added = next((cols[c] for c in cols if "date added" in c or "added" in c), None)
        out = pd.DataFrame({
            "ticker": t[sym].map(yahoo_symbol),
            "name": t[name] if name is not None else "",
            "sector": t[sec].astype(str).str.strip(),
            "sub_industry": t[sub].astype(str).str.strip() if sub is not None else "",
            "index": index_name,
            "date_added": pd.to_datetime(t[added], errors="coerce") if added is not None else pd.NaT,
        })
        return out
    raise ValueError(f"no constituent table found for {index_name}")


def load_universe(f: Fetcher, cfg: dict, cache: Path) -> pd.DataFrame:
    frames = []
    for idx in cfg["universe"]["indices"]:
        url = cfg["sources"][f"wiki_{idx}"]
        try:
            raw = f.get(url, source=f"Universe:{idx}", ttl_hours=24)
            frames.append(parse_wiki_constituents(raw.decode("utf-8", "ignore"), idx))
        except Exception as e:  # noqa: BLE001
            log.warning("universe %s failed: %s", idx, e)
            record(f"Universe:{idx}", status="error", error=str(e)[:300])
    fp = cache / "universe.pkl"
    if frames:
        u = pd.concat(frames, ignore_index=True).drop_duplicates("ticker")
        if len(u) > 1000 or not fp.exists():
            u.to_pickle(fp)
    elif fp.exists():
        u = pd.read_pickle(fp)
        record("Universe", status="stale", error="using cached constituents")
    else:
        raise RuntimeError("universe unavailable")
    # sector normalisation (Wikipedia occasionally uses older names)
    u["sector"] = u["sector"].replace({"Telecommunication Services": "Communication Services",
                                       "Information technology": "Information Technology"})
    record("Universe", rows=int(len(u)), status="ok")
    return u.reset_index(drop=True)


def sec_cik_map(f: Fetcher, cfg: dict) -> dict[str, int]:
    raw = f.get(cfg["sources"]["sec_tickers"], source="SEC tickers", ttl_hours=24)
    data = json.loads(raw)
    out = {}
    for row in data.values():
        out[yahoo_symbol(row["ticker"])] = int(row["cik_str"])
    record("SEC tickers", rows=len(out))
    return out
