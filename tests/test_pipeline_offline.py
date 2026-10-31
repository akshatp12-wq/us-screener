"""End-to-end run of `pipeline/run.py --job all` against synthetic sources (no network)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synth  # noqa: E402

_PANELS = {}


def _fake_get(self, url, source, ttl_hours=12.0, max_rps=None, timeout=60, allow_stale=True, stream_to=None):
    from screener.data.cache import FetchError, record
    try:
        body = synth.route(url)
    except FileNotFoundError:
        record(source, url=url, status="error", error="HTTP 404")
        raise FetchError(f"{url}: HTTP 404")
    record(source, url=url, status="ok", fetched_at=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
           error=None)
    if stream_to is not None:
        Path(stream_to).write_bytes(body)
        return Path(stream_to)
    return body


def _fake_yf(tickers, start=None, period=None):
    if not _PANELS:
        from screener.config import load_config
        cfg = load_config()
        _PANELS.update(synth.price_panels(list(cfg["etfs"]["list"].keys()) + ["^VIX"]))
    out = {}
    for k, v in _PANELS.items():
        cols = [t for t in tickers if t in v.columns]
        d = v[cols]
        if period:
            d = d.iloc[-22:]
        out[k] = d
    return out


@pytest.fixture()
def patched(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENER_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCREENER_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
    from screener.data import cache, market_sources, prices
    monkeypatch.setattr(cache.Fetcher, "get", _fake_get)
    monkeypatch.setattr(prices, "_yf_download", _fake_yf)
    monkeypatch.setattr(market_sources, "earnings_dates", lambda t: {t[0]: "2026-11-03"} if t else {})
    return tmp_path


def test_full_pipeline(patched):
    from pipeline import run
    from pipeline.common import STAGE_ERRORS
    STAGE_ERRORS.clear()
    rc = run.main(["--job", "all"])
    out = patched / "out"
    errs = json.loads((out / "manifest.json").read_text())["errors"]
    assert errs == [], errs
    assert rc == 0
    for fn in ("regime.json", "bubble.json", "stocks.json", "etfs.json", "portfolio.json", "events.json",
               "methodology.json", "backtest.json", "validation.json", "stock_backtest.json", "track.json"):
        assert (out / fn).exists(), fn
    reg = json.loads((out / "regime.json").read_text())
    assert reg["regime"]["label"] in ("Calm", "Stretched", "Fragile")
    st = json.loads((out / "stocks.json").read_text())
    assert len(st["rows"]) > 100
    # intraday run on top of the nightly state
    STAGE_ERRORS.clear()
    assert run.main(["--job", "intraday"]) == 0


def test_compare_job(patched):
    from pipeline import run
    from pipeline.common import STAGE_ERRORS
    STAGE_ERRORS.clear()
    assert run.main(["--job", "all"]) == 0
    STAGE_ERRORS.clear()
    rc = run.main(["--job", "compare"])
    assert rc == 0, STAGE_ERRORS
    c = json.loads((patched / "out" / "compare.json").read_text())
    assert set(c["factor"]["verdicts"]) == {"reweight", "trend_switch", "credit_alone"}
    assert "v1" in c["factor"]["variants"] and "1963-2010" in c["factor"]["variants"]["v1"]["absolute_return"]
    assert "v1" in c["stock"]["runs"] and "v2" in c["stock"]["runs"]
    assert "v2, S&P 500 point-in-time" in c["stock"]["runs"]
    reg = json.loads((patched / "out" / "regime.json").read_text())
    assert reg["dials"]["buy_watch"]["state"] in ("Buy signal", "Watching", "No setup")
