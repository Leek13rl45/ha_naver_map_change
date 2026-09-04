"""Tests for cache.py - pure asyncio, no Home Assistant needed.

Async tests are wrapped in ``asyncio.run`` inside plain unittest methods, so
they run under both ``pytest`` and ``python -m unittest`` with no plugin.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _bootstrap import cache_mod, const  # noqa: E402

TileCache = cache_mod.TileCache
TileAsset = cache_mod.TileAsset


def asset(body: bytes, *, ttl: float | None = None) -> object:
    """Build a TileAsset with the fields the tests care about."""
    return TileAsset(body=body, content_type="image/jpeg", encoding=None, ttl=ttl)


class Clock:
    """A monotonic clock the tests advance by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def key(
    provider: str = "naver",
    version: str = "1787907321",
    variant: str = "light",
    scale: int = 1,
    z: int = 12,
    x: int = 3492,
    y: int = 1586,
) -> tuple:
    """Build a cache key (design decisions D9 and D12)."""
    return (provider, version, variant, scale, z, x, y)


class TestStoreAndEvict(unittest.TestCase):
    """Size accounting and LRU eviction."""

    def test_store_and_peek(self) -> None:
        cache = TileCache(max_bytes=1024 * 1024)
        cache.store(key(), asset(b"x" * 100))
        stored = cache.peek(key())
        self.assertIsNotNone(stored)
        self.assertEqual(stored.body, b"x" * 100)
        self.assertGreater(cache.total_bytes, 100)
        self.assertEqual(len(cache), 1)

    def test_restoring_the_same_key_does_not_double_count(self) -> None:
        cache = TileCache(max_bytes=1024 * 1024)
        cache.store(key(), asset(b"x" * 100))
        first = cache.total_bytes
        cache.store(key(), asset(b"y" * 100))
        self.assertEqual(cache.total_bytes, first)
        self.assertEqual(len(cache), 1)

    def test_total_is_bounded_and_evicts_least_recently_used(self) -> None:
        body = b"x" * 1000
        per_entry = len(body) + len(repr(key(z=1))) + 300
        cache = TileCache(max_bytes=per_entry * 3 + 10)

        for index in range(3):
            cache.store(key(z=10, x=index), asset(body))
        self.assertEqual(len(cache), 3)

        # Touch the oldest so it is no longer the eviction candidate.
        cache.store(key(z=10, x=3), asset(body))
        self.assertEqual(len(cache), 3)
        self.assertLessEqual(cache.total_bytes, cache.max_bytes)
        self.assertIsNone(cache.peek(key(z=10, x=0)))
        self.assertIsNotNone(cache.peek(key(z=10, x=3)))

    def test_a_body_larger_than_the_cache_is_not_stored(self) -> None:
        cache = TileCache(max_bytes=500)
        cache.store(key(), asset(b"x" * 1000))
        self.assertEqual(len(cache), 0)
        self.assertEqual(cache.total_bytes, 0)

    def test_clear_reports_freed_bytes(self) -> None:
        cache = TileCache(max_bytes=1024 * 1024)
        cache.store(key(x=1), asset(b"x" * 100))
        cache.store(key(x=2), asset(b"x" * 100))
        freed = cache.total_bytes
        self.assertEqual(cache.clear(), freed)
        self.assertEqual(len(cache), 0)
        self.assertEqual(cache.total_bytes, 0)
        self.assertEqual(cache.clear(), 0)


class TestAsyncGet(unittest.TestCase):
    """Fetch, expiry and the stale fallback."""

    def test_miss_fetches_and_stores(self) -> None:
        cache = TileCache(max_bytes=1024 * 1024)
        calls: list[int] = []

        async def fetch() -> object:
            calls.append(1)
            return asset(b"tile")

        async def scenario() -> None:
            first = await cache.async_get(key(), const.TILE_TTL, fetch)
            second = await cache.async_get(key(), const.TILE_TTL, fetch)
            self.assertEqual(first.body, b"tile")
            self.assertEqual(second.body, b"tile")

        asyncio.run(scenario())
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.hits, 1)
        self.assertEqual(cache.misses, 1)

    def test_failed_fetch_is_not_stored(self) -> None:
        cache = TileCache(max_bytes=1024 * 1024)

        async def fetch() -> object | None:
            return None

        async def scenario() -> None:
            self.assertIsNone(await cache.async_get(key(), const.TILE_TTL, fetch))

        asyncio.run(scenario())
        self.assertEqual(len(cache), 0)

    def test_expired_entry_is_refreshed_inline(self) -> None:
        clock = Clock()
        cache = TileCache(max_bytes=1024 * 1024, time_func=clock)
        bodies = [b"new"]

        async def fetch() -> object:
            return asset(bodies[0])

        async def scenario() -> None:
            cache.store(key(), asset(b"old"))
            clock.advance(const.TILE_TTL + 1)
            served = await cache.async_get(key(), const.TILE_TTL, fetch)
            self.assertEqual(served.body, b"new")

        asyncio.run(scenario())
        self.assertEqual(cache.stale_hits, 1)

    def test_expired_entry_survives_as_a_stale_fallback(self) -> None:
        """An expired body is the only thing keeping the map up when upstream
        is down (docs/03 section 3.3, design decision D9)."""
        clock = Clock()
        cache = TileCache(max_bytes=1024 * 1024, time_func=clock)

        async def failing_fetch() -> object | None:
            return None

        async def scenario() -> None:
            cache.store(key(), asset(b"old"))
            clock.advance(const.TILE_TTL * 10)
            served = await cache.async_get(key(), const.TILE_TTL, failing_fetch)
            self.assertIsNotNone(served)
            self.assertEqual(served.body, b"old")
            # And it is still there for the next request.
            self.assertIsNotNone(cache.peek(key()))

        asyncio.run(scenario())

    def test_background_runner_serves_stale_immediately(self) -> None:
        clock = Clock()
        scheduled: list[str] = []

        def background(coro, name: str) -> None:
            scheduled.append(name)
            coro.close()

        cache = TileCache(
            max_bytes=1024 * 1024, time_func=clock, background=background
        )

        async def fetch() -> object:
            raise AssertionError("must not be awaited inline")

        async def scenario() -> None:
            cache.store(key(), asset(b"old"))
            clock.advance(const.TILE_TTL + 1)
            served = await cache.async_get(key(), const.TILE_TTL, fetch)
            self.assertEqual(served.body, b"old")

        asyncio.run(scenario())
        self.assertEqual(len(scheduled), 1)

    def test_asset_ttl_overrides_the_fallback_interval(self) -> None:
        clock = Clock()
        cache = TileCache(max_bytes=1024 * 1024, time_func=clock)
        cache.store(key(), asset(b"body", ttl=60))
        self.assertFalse(cache.is_expired(key(), const.TILE_TTL))
        clock.advance(61)
        self.assertTrue(cache.is_expired(key(), const.TILE_TTL))
        self.assertIsNone(cache.is_expired(key(x=9), const.TILE_TTL))

    def test_concurrent_misses_share_one_fetch(self) -> None:
        cache = TileCache(max_bytes=1024 * 1024)
        calls: list[int] = []

        async def fetch() -> object:
            calls.append(1)
            await asyncio.sleep(0.01)
            return asset(b"tile")

        async def scenario() -> None:
            results = await asyncio.gather(
                *(cache.async_get(key(), const.TILE_TTL, fetch) for _ in range(8))
            )
            self.assertTrue(all(result.body == b"tile" for result in results))

        asyncio.run(scenario())
        self.assertEqual(len(calls), 1, "in-flight requests were not deduplicated")

    def test_a_cancelled_waiter_does_not_cancel_the_shared_fetch(self) -> None:
        cache = TileCache(max_bytes=1024 * 1024)
        completed: list[int] = []

        async def fetch() -> object:
            await asyncio.sleep(0.02)
            completed.append(1)
            return asset(b"tile")

        async def scenario() -> None:
            first = asyncio.ensure_future(
                cache.async_get(key(), const.TILE_TTL, fetch)
            )
            second = asyncio.ensure_future(
                cache.async_get(key(), const.TILE_TTL, fetch)
            )
            await asyncio.sleep(0)
            first.cancel()
            result = await second
            self.assertEqual(result.body, b"tile")

        asyncio.run(scenario())
        self.assertEqual(len(completed), 1)

    def test_concurrency_is_capped(self) -> None:
        cache = TileCache(max_bytes=8 * 1024 * 1024, max_concurrent_fetches=2)
        inside = [0]
        peak = [0]

        async def fetch() -> object:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
            await asyncio.sleep(0.01)
            inside[0] -= 1
            return asset(b"tile")

        async def scenario() -> None:
            await asyncio.gather(
                *(
                    cache.async_get(key(x=index), const.TILE_TTL, fetch)
                    for index in range(10)
                )
            )

        asyncio.run(scenario())
        self.assertLessEqual(peak[0], 2)


class TestVersionInCacheKey(unittest.TestCase):
    """AC12: a new version code invalidates by itself (design decision D9)."""

    def test_a_new_version_does_not_hit_the_old_entry(self) -> None:
        cache = TileCache(max_bytes=1024 * 1024)
        fetched: list[str] = []

        async def fetch_v2() -> object:
            fetched.append("v2")
            return asset(b"tile-v2")

        async def scenario() -> None:
            cache.store(key(version="1787907321"), asset(b"tile-v1"))
            served = await cache.async_get(
                key(version="1799999999"), const.TILE_TTL, fetch_v2
            )
            self.assertEqual(served.body, b"tile-v2")
            # The old entry is untouched, not overwritten, and never served
            # under the new version.
            self.assertEqual(cache.peek(key(version="1787907321")).body, b"tile-v1")
            self.assertEqual(cache.peek(key(version="1799999999")).body, b"tile-v2")

        asyncio.run(scenario())
        self.assertEqual(fetched, ["v2"])

    def test_provider_and_variant_also_separate_keys(self) -> None:
        cache = TileCache(max_bytes=1024 * 1024)
        cache.store(key(provider="naver"), asset(b"naver"))
        cache.store(key(provider="osm", version=""), asset(b"osm"))
        cache.store(key(variant="dark"), asset(b"dark"))
        self.assertEqual(len(cache), 3)
        self.assertEqual(cache.peek(key(provider="naver")).body, b"naver")
        self.assertEqual(cache.peek(key(provider="osm", version="")).body, b"osm")
        self.assertEqual(cache.peek(key(variant="dark")).body, b"dark")

    def test_scale_separates_keys(self) -> None:
        """Design decision D12: a 1x body and a 2x body never mix.

        They are different images of the same tile, so serving one where the
        other was asked for is either a blurry map or 3.2x of wasted bandwidth.
        """
        cache = TileCache(max_bytes=1024 * 1024)
        cache.store(key(scale=1), asset(b"tile-1x"))
        cache.store(key(scale=2), asset(b"tile-2x"))
        self.assertEqual(len(cache), 2)
        self.assertEqual(cache.peek(key(scale=1)).body, b"tile-1x")
        self.assertEqual(cache.peek(key(scale=2)).body, b"tile-2x")

    def test_a_2x_request_does_not_hit_the_1x_entry(self) -> None:
        fetched: list[int] = []

        async def fetch_2x():
            fetched.append(2)
            return asset(b"tile-2x")

        async def scenario() -> None:
            cache = TileCache(max_bytes=1024 * 1024)
            cache.store(key(scale=1), asset(b"tile-1x"))
            served = await cache.async_get(key(scale=2), const.TILE_TTL, fetch_2x)
            self.assertEqual(served.body, b"tile-2x")
            # The 1x entry is untouched, so a 1x client still gets its own body.
            self.assertEqual(cache.peek(key(scale=1)).body, b"tile-1x")

        asyncio.run(scenario())
        self.assertEqual(fetched, [2])


class TestTtlParsing(unittest.TestCase):
    """Upstream max-age handling (requirement R3)."""

    def test_parse_max_age(self) -> None:
        self.assertEqual(cache_mod.parse_max_age("max-age=300"), 300.0)
        self.assertEqual(
            cache_mod.parse_max_age("public, max-age=88852, stale-if-error=604800"),
            88852.0,
        )
        self.assertIsNone(cache_mod.parse_max_age(""))
        self.assertIsNone(cache_mod.parse_max_age("no-store"))
        self.assertIsNone(cache_mod.parse_max_age("max-age=abc"))

    def test_naver_one_year_is_capped_at_tile_ttl(self) -> None:
        # Measured: naver sends max-age=31536000 (docs/05 section 2).
        self.assertEqual(
            cache_mod.effective_ttl("max-age=31536000", const.TILE_TTL),
            float(const.TILE_TTL),
        )

    def test_shorter_upstream_max_age_is_honored(self) -> None:
        self.assertEqual(cache_mod.effective_ttl("max-age=600", const.TILE_TTL), 600.0)

    def test_missing_header_falls_back_to_the_ceiling(self) -> None:
        self.assertEqual(
            cache_mod.effective_ttl("", const.TILE_TTL), float(const.TILE_TTL)
        )


if __name__ == "__main__":
    unittest.main()
