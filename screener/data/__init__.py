"""Data layer: every free source, cached, with freshness recorded in cache.REGISTRY."""
from .cache import REGISTRY, Fetcher, FetchError, record  # noqa: F401
