"""Bounded in-memory tile cache.

A reduced reimplementation of core ``homeassistant/components/map_tiles/cache.py``
(read directly from 2026.9.0). Two deliberate differences:

* It imports nothing from Home Assistant, so eviction, staleness and in-flight
  deduplication are unit-testable without a Home Assistant installation
  (design decision D1). The background-refresh hook the core cache gets from
  ``hass.async_create_background_task`` is an injected callable here.
* The cache key carries the provider id, the upstream version code and the
  style variant (design decision D9), so a new naver version code separates
  keys by itself and no explicit invalidation is needed
  (docs/05-UPSTREAM-FINDINGS.md section 5, AC12).

Nothing is written to disk: Home Assistant runs on SD cards, and the working
set is worth a few dozen requests after a restart.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Final

from .const import CACHE_MAX_BYTES, MAX_CONCURRENT_FETCHES

# Approximate bookkeeping cost of one entry (key tuple, dataclass, dict slot),
# charged so that tiny bodies cannot grow the entry count without bound. Same
# rationale and value as core cache.py.
_ENTRY_OVERHEAD: Final = 300

# (provider_id, version_or_empty, variant, z, x, y) - design decision D9.
type CacheKey = tuple[str, str, str, int, int, int]


@dataclass(frozen=True, slots=True)
class TileAsset:
    """An upstream response body with the headers worth keeping.

    The body is stored exactly as upstream sent it, in whatever
    ``Content-Encoding`` it arrived with, so it is never recompressed.
    """

    body: bytes
    content_type: str
    encoding: str | None = None
    ttl: float | None = None


type FetchCallback = Callable[[], Coroutine[Any, Any, TileAsset | None]]
type BackgroundRunner = Callable[[Coroutine[Any, Any, Any], str], None]


def _entry_size(key: CacheKey, asset: TileAsset) -> int:
    """Return what an entry counts against the size ceiling."""
    return len(asset.body) + len(repr(key)) + _ENTRY_OVERHEAD


class TileCache:
    """A bounded in-memory cache of upstream tiles, keyed by CacheKey.

    Expired entries are never dropped for being expired - only the size ceiling
    evicts, least recently used first. An expired entry is what keeps the map up
    while upstream is unreachable (docs/03 section 3.3; OSM itself advertises
    ``stale-if-error``, docs/05 section 7.1).
    """

    def __init__(
        self,
        *,
        max_bytes: int = CACHE_MAX_BYTES,
        max_concurrent_fetches: int = MAX_CONCURRENT_FETCHES,
        background: BackgroundRunner | None = None,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the cache.

        ``background`` mirrors core's ``hass.async_create_background_task``:
        when given, an expired entry is served immediately and refreshed out of
        band. When it is None, an expired entry is refreshed inline and only
        falls back to the expired body if that refresh fails.
        """
        self._max_bytes = max_bytes
        self._entries: OrderedDict[CacheKey, tuple[TileAsset, float]] = OrderedDict()
        self._size = 0
        self._fetches: dict[CacheKey, asyncio.Task[TileAsset | None]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent_fetches)
        self._background = background
        self._time = time_func
        self.hits = 0
        self.misses = 0
        self.stale_hits = 0

    @property
    def total_bytes(self) -> int:
        """Return the current accounted size of the cache."""
        return self._size

    @property
    def max_bytes(self) -> int:
        """Return the size ceiling."""
        return self._max_bytes

    def __len__(self) -> int:
        """Return the number of cached entries."""
        return len(self._entries)

    def stats(self) -> dict[str, int]:
        """Return counters for diagnostics."""
        return {
            "entries": len(self._entries),
            "bytes": self._size,
            "max_bytes": self._max_bytes,
            "hits": self.hits,
            "misses": self.misses,
            "stale_hits": self.stale_hits,
        }

    def peek(self, key: CacheKey) -> TileAsset | None:
        """Return the stored asset without touching LRU order or counters."""
        if (entry := self._entries.get(key)) is None:
            return None
        return entry[0]

    def is_expired(self, key: CacheKey, ttl: float) -> bool | None:
        """Return whether the entry for key is past its refresh interval."""
        if (entry := self._entries.get(key)) is None:
            return None
        asset, stored_at = entry
        return self._time() - stored_at > (ttl if asset.ttl is None else asset.ttl)

    async def async_get(
        self, key: CacheKey, ttl: float, fetch: FetchCallback
    ) -> TileAsset | None:
        """Return the asset for key, fetching or refreshing it as needed.

        ``ttl`` is the fallback refresh interval, used when the stored asset
        carries no upstream max-age of its own.
        """
        if (entry := self._entries.get(key)) is None:
            self.misses += 1
            return await self._async_fetch(key, fetch)

        self._entries.move_to_end(key)
        asset, stored_at = entry
        if self._time() - stored_at <= (ttl if asset.ttl is None else asset.ttl):
            self.hits += 1
            return asset

        self.stale_hits += 1
        if self._background is not None:
            # Serve the expired entry now and refresh out of band, so an
            # upstream outage degrades to slightly old tiles, not to no map.
            self._background(self._async_fetch(key, fetch), f"refresh {key}")
            return asset

        # No background runner: refresh inline, but never lose the map over it.
        refreshed = await self._async_fetch(key, fetch)
        return refreshed if refreshed is not None else asset

    def store(self, key: CacheKey, asset: TileAsset) -> None:
        """Store an entry, evicting least recently used until back under size."""
        if (previous := self._entries.pop(key, None)) is not None:
            self._size -= _entry_size(key, previous[0])

        size = _entry_size(key, asset)
        if size > self._max_bytes:
            # A single body larger than the whole cache is never stored: it
            # would evict everything else on the way in.
            return

        self._entries[key] = (asset, self._time())
        self._size += size

        while self._size > self._max_bytes and self._entries:
            evicted_key, (evicted, _stored_at) = self._entries.popitem(last=False)
            self._size -= _entry_size(evicted_key, evicted)

    def clear(self) -> int:
        """Drop every entry, returning how many bytes were freed."""
        evicted = self._size
        self._entries.clear()
        self._size = 0
        return evicted

    async def _async_fetch(
        self, key: CacheKey, fetch: FetchCallback
    ) -> TileAsset | None:
        """Fetch key upstream, joining a fetch already in flight for it."""
        if (pending := self._fetches.get(key)) is None:
            pending = asyncio.get_running_loop().create_task(
                self._async_fetch_and_store(key, fetch)
            )
            if not pending.done():
                self._fetches[key] = pending
                pending.add_done_callback(lambda _task: self._fetches.pop(key, None))

        # Shielded: one client navigating away must not cancel the fetch the
        # other waiters depend on (same reasoning as core cache.py).
        return await asyncio.shield(pending)

    async def _async_fetch_and_store(
        self, key: CacheKey, fetch: FetchCallback
    ) -> TileAsset | None:
        """Fetch key upstream and store what comes back."""
        # Bounds both parallel upstream requests and the in-flight body memory
        # they hold. The store afterwards is synchronous and needs no slot.
        async with self._semaphore:
            asset = await fetch()
        if asset is not None:
            self.store(key, asset)
        return asset


def parse_max_age(cache_control: str) -> float | None:
    """Return upstream's max-age in seconds, or None when it sends none.

    Same parsing as core ``views.py:_upstream_ttl``.
    """
    for directive in cache_control.split(","):
        name, _, value = directive.strip().partition("=")
        if name.lower() == "max-age" and value.isdigit():
            return float(value)
    return None


def effective_ttl(cache_control: str, ceiling: float) -> float:
    """Return the TTL to store an asset with, honoring upstream but capped.

    Core honors upstream ``max-age`` verbatim. Naver answers
    ``max-age=31536000`` - one year (docs/05 section 2) - because the version
    code makes a tile URL effectively immutable. Honoring that literally would
    pin a body in memory indefinitely and would outlive the version code it was
    fetched under, so the value is capped at ``ceiling`` (TILE_TTL). Requirement
    R3 from the coordinator.
    """
    upstream = parse_max_age(cache_control)
    if upstream is None:
        return ceiling
    return min(upstream, ceiling)
