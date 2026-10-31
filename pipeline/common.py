"""Shared pipeline helpers: stage error capture, dashboard cards, macro loading."""
from __future__ import annotations

import logging
import time
import traceback

import pandas as pd

from screener.data import macro_sources as ms
from screener.util import sparkline

log = logging.getLogger("pipeline")

MACRO_SERIES = ["QUSPAM770A", "QUSNAM770A", "QUSHAM770A", "CSUSHPINSA", "T10Y3M", "BAMLH0A0HYM2",
                "VIXCLS", "DTB3", "USREC", "GS10", "TB3MS", "BAA", "BAA10Y"]
STAGE_ERRORS: list[dict] = []


def stage(name):
    def deco(fn):
        def wrap(*a, **kw):
            t0 = time.time()
            try:
                out = fn(*a, **kw)
                log.info("stage %s ok (%.1fs)", name, time.time() - t0)
                return out
            except Exception as e:  # noqa: BLE001
                log.error("stage %s failed: %s", name, e)
                STAGE_ERRORS.append({"stage": name, "error": f"{type(e).__name__}: {e}"[:500],
                                     "trace": traceback.format_exc()[-1500:]})
                return None
        return wrap
    return deco


# ============================================================================= helpers
def card(key: str, title: str, res: dict, cite: str, source: str, freq: str, formula: str) -> dict:
    """Dashboard card for one dial (series converted to a sparkline)."""
    res = dict(res or {})
    s = res.pop("series", None)
    res.pop("state_series", None)
    return {"key": key, "title": title, "cite": cite, "source": source, "frequency": freq, "formula": formula,
            "sparkline": sparkline(s, 120) if isinstance(s, pd.Series) else [], **res}


def load_macro(f, cfg) -> dict:
    out = {}
    for sid in MACRO_SERIES:
        out[sid] = ms.safe(ms.fred, f, cfg, sid, source=f"FRED:{sid}")
    return out


def spliced_stock_price(sh: pd.DataFrame | None, spy: pd.Series | None,
                        french_mkt: pd.Series | None = None) -> pd.Series | None:
    """Monthly S&P composite price (Shiller) extended with SPY price changes after Shiller's last month.

    Falls back to the Ken French market total-return index when Shiller's file is unavailable.
    """
    if sh is None or sh.empty or sh["price"].dropna().empty:
        if french_mkt is None or french_mkt.dropna().empty:
            return None
        p = (1 + french_mkt.dropna()).cumprod()
    else:
        p = sh["price"].dropna()
    if spy is not None and len(spy):
        spy_px = (1 + spy.fillna(0)).cumprod().resample("ME").last()
        after = spy_px[spy_px.index > p.index[-1]]
        if len(after) and p.index[-1] in spy_px.index:
            p = pd.concat([p, p.iloc[-1] * after / spy_px.loc[p.index[-1]]])
    return p


