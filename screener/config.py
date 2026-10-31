"""Load config.yaml once and expose it as a plain dict."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg.setdefault("_root", str(ROOT))
    return cfg


def cache_dir(cfg: dict) -> Path:
    d = Path(os.environ.get("SCREENER_CACHE", ROOT / cfg["data"]["cache_dir"]))
    d.mkdir(parents=True, exist_ok=True)
    return d


def out_dir(cfg: dict) -> Path:
    d = Path(os.environ.get("SCREENER_OUT", ROOT / cfg["data"]["out_dir"]))
    d.mkdir(parents=True, exist_ok=True)
    return d
