#!/usr/bin/env python
"""Single entry point. Writes docs/data/*.json and manifest.json.

  python pipeline/run.py --job nightly     # full rebuild (universe, fundamentals, scores, portfolios)
  python pipeline/run.py --job intraday    # ETFs, indices, sentiment, regime, bubble watch, ETF ranks
  python pipeline/run.py --job weekly      # backtests and validation
  python pipeline/run.py --job all         # nightly + weekly
  python pipeline/run.py --offline         # use cached downloads only
Exit code 1 if any pipeline stage raised (data-source outages alone do not fail the run).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from pipeline.common import STAGE_ERRORS  # noqa: E402
from screener import __version__  # noqa: E402
from screener.config import cache_dir, load_config, out_dir  # noqa: E402
from screener.data import REGISTRY, Fetcher  # noqa: E402
from screener.util import utcnow, write_json  # noqa: E402

log = logging.getLogger("pipeline")

STALE_KEYS = (("yahoo", "prices"), ("stooq", "prices"), ("fred", "fred"), ("french", "french"),
              ("shiller", "shiller"), ("companyfacts", "sec_fundamentals"), ("fundamentals", "sec_fundamentals"),
              ("form 3/4/5", "insiders"), ("finra", "short_interest"), ("ebp", "ebp"), ("universe", "universe"),
              ("ssga", "etf_holdings"))


def _stale_key(src: str) -> str:
    s = src.lower()
    for k, key in STALE_KEYS:
        if k in s:
            return key
    return ""


def write_manifest(out: Path, job: str, t0: float, cfg: dict, files: list[str]) -> dict:
    mp = out / "manifest.json"
    prev = json.loads(mp.read_text()) if mp.exists() else {}
    sources = {s["source"]: s for s in prev.get("sources", [])}
    for k, v in REGISTRY.items():
        old = sources.get(k, {})
        merged = {**old, **v}
        merged["checked_at"] = utcnow()
        sources[k] = merged
    stale_cfg = cfg["data"]["stale_after_hours"]
    now = pd.Timestamp.now(tz="UTC")
    for s in sources.values():
        lim = stale_cfg.get(_stale_key(s["source"]))
        fa = s.get("fetched_at")
        if lim and fa:
            age = (now - pd.Timestamp(fa)).total_seconds() / 3600
            s["age_hours"] = round(age, 1)
            s["stale"] = bool(age > lim)
        s["stale_limit_hours"] = lim
    files_meta = prev.get("files", {})
    for fn in files:
        p = out / fn
        if p.exists():
            h = hashlib.sha1(p.read_bytes()).hexdigest()[:12]
            if files_meta.get(fn, {}).get("hash") != h:
                files_meta[fn] = {"hash": h, "updated": utcnow()}
    runs = [{"job": job, "time": utcnow(), "ok": not STAGE_ERRORS, "duration_s": round(time.time() - t0, 1)}]
    m = {"version": __version__, "run_time": utcnow(), "job": job, "duration_s": round(time.time() - t0, 1),
         "sources": sorted(sources.values(), key=lambda x: x["source"]), "files": files_meta,
         "errors": STAGE_ERRORS, "last_runs": (runs + prev.get("last_runs", []))[:40]}
    write_json(mp, m)
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default="nightly", choices=["intraday", "nightly", "weekly", "all"])
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    t0 = time.time()
    cfg = load_config(a.config)
    cache, out = cache_dir(cfg), out_dir(cfg)
    f = Fetcher(cache, cfg, offline=a.offline)
    written: list[str] = []
    jobs = {"intraday": ["intraday"], "nightly": ["nightly"], "weekly": ["weekly"], "all": ["nightly", "weekly"]}[a.job]
    from pipeline.live import run_live
    from pipeline.weekly import run_weekly
    for job in jobs:
        if job in ("intraday", "nightly"):
            written += run_live(cfg, f, cache, out, full=(job == "nightly")) or []
        if job == "weekly":
            written += run_weekly(cfg, f, cache, out) or []
    write_manifest(out, a.job, t0, cfg, written)
    if STAGE_ERRORS:
        for e in STAGE_ERRORS:
            log.error("stage %s failed: %s\n%s", e["stage"], e["error"], e["trace"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
