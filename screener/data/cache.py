"""HTTP fetching with an on-disk cache, conditional requests and per-host rate limits.

Every fetch is recorded in REGISTRY so the pipeline can write per-source freshness
and errors into manifest.json (the dashboard's Data health page).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from ..util import utcnow

log = logging.getLogger("screener.data")

REGISTRY: dict[str, dict] = {}
_LOCK = threading.Lock()
_LAST_CALL: dict[str, float] = {}

DEFAULT_UA = "us-screener/1.0 (+https://github.com/akshatp12-wq/us-screener)"


class FetchError(RuntimeError):
    pass


def record(source: str, **kw) -> None:
    with _LOCK:
        entry = REGISTRY.setdefault(source, {"source": source})
        entry.update(kw)


def _throttle(host: str, max_rps: float) -> None:
    if max_rps <= 0:
        return
    gap = 1.0 / max_rps
    with _LOCK:
        last = _LAST_CALL.get(host, 0.0)
        wait = last + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[host] = time.monotonic()


def user_agent_for(url: str, cfg: dict | None = None) -> str:
    host = urlparse(url).netloc
    if host.endswith("sec.gov"):
        env = (cfg or {}).get("data", {}).get("sec_user_agent_env", "SEC_USER_AGENT")
        ua = os.environ.get(env, "").strip()
        if not ua:
            raise FetchError("SEC_USER_AGENT secret is not set (SEC requires a contact email in the User-Agent)")
        return ua
    if "wikipedia.org" in host:
        return DEFAULT_UA + " python-requests"
    return "Mozilla/5.0 (X11; Linux x86_64) " + DEFAULT_UA


class Fetcher:
    """Cached GET. `ttl_hours` controls how long a cached copy is used without revalidation."""

    def __init__(self, cache_dir: Path, cfg: dict | None = None, offline: bool = False):
        self.dir = Path(cache_dir) / "http"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg or {}
        self.offline = offline or os.environ.get("SCREENER_OFFLINE") == "1"
        self.session = requests.Session()

    def _paths(self, url: str) -> tuple[Path, Path]:
        h = hashlib.sha1(url.encode()).hexdigest()[:20]
        return self.dir / f"{h}.bin", self.dir / f"{h}.json"

    def get(self, url: str, source: str, ttl_hours: float = 12.0, max_rps: float | None = None,
            timeout: int = 60, allow_stale: bool = True, stream_to: Path | None = None) -> bytes | Path:
        body_p, meta_p = self._paths(url)
        if stream_to is not None:
            body_p = Path(stream_to)
        meta = {}
        if meta_p.exists():
            try:
                meta = json.loads(meta_p.read_text())
            except Exception:
                meta = {}
        have = body_p.exists() and meta
        age_h = (time.time() - meta.get("ts", 0)) / 3600 if have else 1e9

        def ret_cached(status: str, err: str | None = None):
            record(source, url=url, status=status, fetched_at=meta.get("fetched_at"),
                   last_modified=meta.get("last_modified"), error=err)
            return body_p if stream_to is not None else body_p.read_bytes()

        if have and (age_h < ttl_hours or self.offline):
            return ret_cached("cached")
        if self.offline:
            record(source, url=url, status="error", error="offline and not cached")
            raise FetchError(f"offline: {url} not cached")

        host = urlparse(url).netloc
        rps = max_rps if max_rps is not None else (
            self.cfg.get("data", {}).get("sec_max_rps", 8) if host.endswith("sec.gov")
            else self.cfg.get("data", {}).get("default_max_rps", 4))
        try:
            headers = {"User-Agent": user_agent_for(url, self.cfg), "Accept-Encoding": "gzip, deflate"}
        except FetchError as e:
            if have and allow_stale:
                return ret_cached("stale", str(e))
            record(source, url=url, status="error", error=str(e))
            raise
        if have:
            if meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]

        last_err = None
        for attempt in range(3):
            _throttle(host, rps)
            try:
                r = self.session.get(url, headers=headers, timeout=timeout, stream=stream_to is not None)
                if r.status_code == 304 and have:
                    meta["ts"] = time.time()
                    meta["fetched_at"] = utcnow()
                    meta_p.write_text(json.dumps(meta))
                    return ret_cached("not-modified")
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(2 ** attempt * 2)
                    continue
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    break
                if stream_to is not None:
                    tmp = body_p.with_suffix(".part")
                    with open(tmp, "wb") as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
                    tmp.replace(body_p)
                    content_len = body_p.stat().st_size
                else:
                    body = r.content
                    body_p.write_bytes(body)
                    content_len = len(body)
                meta = {"url": url, "ts": time.time(), "fetched_at": utcnow(),
                        "etag": r.headers.get("ETag"), "last_modified": r.headers.get("Last-Modified"),
                        "bytes": content_len}
                meta_p.write_text(json.dumps(meta))
                record(source, url=url, status="ok", fetched_at=meta["fetched_at"],
                       last_modified=meta["last_modified"], error=None)
                return body_p if stream_to is not None else body_p.read_bytes()
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"[:300]
                time.sleep(2 ** attempt)
        if have and allow_stale:
            return ret_cached("stale", last_err)
        record(source, url=url, status="error", error=last_err)
        raise FetchError(f"{url}: {last_err}")
