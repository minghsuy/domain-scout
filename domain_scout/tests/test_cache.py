"""Tests for DuckDB cache layer."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain_scout.cache import (
    _VACUUM_INTERVAL,
    CachedCTLogSource,
    CachedRDAPLookup,
    DuckDBCache,
    _cache_key,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def cache(tmp_path: Path) -> Generator[DuckDBCache]:
    """Create a DuckDBCache in a temp directory."""
    c = DuckDBCache(cache_dir=tmp_path / "cache")
    yield c
    c.close()


class TestDuckDBCache:
    def test_ct_put_get(self, cache: DuckDBCache) -> None:
        data = [{"cert_id": 1, "common_name": "example.com"}]
        cache.put_ct("test_query", data)
        result = cache.get_ct("test_query")
        assert result == data

    def test_ct_miss(self, cache: DuckDBCache) -> None:
        assert cache.get_ct("nonexistent") is None

    def test_rdap_put_get(self, cache: DuckDBCache) -> None:
        data = {"org": "Acme Corp", "name": None, "country": "US"}
        cache.put_rdap("example.com", data)
        result = cache.get_rdap("example.com")
        assert result == data

    def test_rdap_miss(self, cache: DuckDBCache) -> None:
        assert cache.get_rdap("nonexistent") is None

    def test_clear(self, cache: DuckDBCache) -> None:
        cache.put_ct("q1", [{"a": 1}])
        cache.put_rdap("q2", {"org": "X", "name": None})
        cache.clear()
        assert cache.get_ct("q1") is None
        assert cache.get_rdap("q2") is None

    def test_stats_empty(self, cache: DuckDBCache) -> None:
        stats = cache.stats()
        assert stats["ct_entries"] == 0
        assert stats["rdap_entries"] == 0
        assert stats["ct_oldest_age_seconds"] is None

    def test_stats_with_entries(self, cache: DuckDBCache) -> None:
        cache.put_ct("q1", [])
        cache.put_rdap("q2", {"org": None})
        stats = cache.stats()
        assert stats["ct_entries"] == 1
        assert stats["rdap_entries"] == 1
        assert stats["ct_oldest_age_seconds"] is not None

    def test_overwrite(self, cache: DuckDBCache) -> None:
        cache.put_ct("q1", [{"v": 1}])
        cache.put_ct("q1", [{"v": 2}])
        result = cache.get_ct("q1")
        assert result == [{"v": 2}]

    def test_expired_entry(self, cache: DuckDBCache) -> None:
        """Manually insert an expired entry and verify it's evicted on get."""
        key = _cache_key("ct", "expired_query")
        expired_time = time.time() - 5 * 3600  # 5 hours ago, CT TTL is 4h
        assert cache._conn is not None
        cache._conn.execute(
            "INSERT INTO ct_cache (cache_key, result_json, created_at) VALUES (?, ?, ?)",
            [key, "[]", expired_time],
        )
        assert cache.get_ct("expired_query") is None

    def test_datetime_serialization(self, cache: DuckDBCache) -> None:
        """Ensure datetime objects in CT results survive round-trip via default=str."""
        from datetime import datetime

        data: list[dict[str, object]] = [
            {
                "cert_id": 42,
                "not_before": datetime(2025, 1, 1, 0, 0, 0),
                "not_after": datetime(2026, 1, 1, 0, 0, 0),
            }
        ]
        cache.put_ct("dt_test", data)
        result = cache.get_ct("dt_test")
        assert result is not None
        # datetime gets serialized as string via json default=str
        not_before = result[0]["not_before"]
        assert isinstance(not_before, str)
        assert "2025" in not_before

    def test_closed_cache_returns_none(self, cache: DuckDBCache) -> None:
        """After close(), all operations gracefully return None / empty."""
        cache.put_ct("q1", [{"v": 1}])
        cache.close()
        assert cache.get_ct("q1") is None
        assert cache.get_rdap("anything") is None
        stats = cache.stats()
        assert stats["ct_entries"] == 0

    def test_vacuum_triggered(self, cache: DuckDBCache) -> None:
        """Vacuum runs after _VACUUM_INTERVAL puts."""
        # Insert an expired entry directly
        assert cache._conn is not None
        expired_time = time.time() - 5 * 3600
        cache._conn.execute(
            "INSERT INTO ct_cache (cache_key, result_json, created_at) VALUES (?, ?, ?)",
            ["expired_key", "[]", expired_time],
        )
        # Verify it's there
        row = cache._conn.execute(
            "SELECT COUNT(*) FROM ct_cache WHERE cache_key = 'expired_key'"
        ).fetchone()
        assert row is not None and row[0] == 1

        # Set counter to just below threshold, then put once to trigger vacuum
        cache._vacuum_counter = _VACUUM_INTERVAL - 1
        cache.put_ct("trigger", [])

        # Expired entry should be cleaned up
        row = cache._conn.execute(
            "SELECT COUNT(*) FROM ct_cache WHERE cache_key = 'expired_key'"
        ).fetchone()
        assert row is not None and row[0] == 0


class TestCacheKey:
    def test_deterministic(self) -> None:
        k1 = _cache_key("ct", "example.com")
        k2 = _cache_key("ct", "example.com")
        assert k1 == k2

    def test_different_prefix(self) -> None:
        k1 = _cache_key("ct", "example.com")
        k2 = _cache_key("rdap", "example.com")
        assert k1 != k2

    def test_different_query(self) -> None:
        k1 = _cache_key("ct", "a.com")
        k2 = _cache_key("ct", "b.com")
        assert k1 != k2


class TestCachedCTLogSource:
    @pytest.mark.asyncio
    async def test_cache_hit(self, cache: DuckDBCache) -> None:
        inner = MagicMock()
        inner.search_by_domain = AsyncMock()
        cached_data: list[dict[str, object]] = [{"cert_id": 1}]
        cache.put_ct("domain:example.com", cached_data)

        wrapper = CachedCTLogSource(inner, cache)
        result = await wrapper.search_by_domain("example.com")
        assert result == cached_data
        inner.search_by_domain.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss(self, cache: DuckDBCache) -> None:
        inner = MagicMock()
        fresh_data: list[dict[str, object]] = [{"cert_id": 2}]
        inner.search_by_domain = AsyncMock(return_value=fresh_data)

        wrapper = CachedCTLogSource(inner, cache)
        result = await wrapper.search_by_domain("miss.com")
        assert result == fresh_data
        inner.search_by_domain.assert_called_once_with("miss.com")
        # Should be cached now
        assert cache.get_ct("domain:miss.com") == fresh_data

    @pytest.mark.asyncio
    async def test_org_search_cached(self, cache: DuckDBCache) -> None:
        inner = MagicMock()
        inner.search_by_org = AsyncMock()
        cached_data: list[dict[str, object]] = [{"cert_id": 3, "org_name": "Acme"}]
        cache.put_ct("org:Acme:verify=True", cached_data)

        wrapper = CachedCTLogSource(inner, cache)
        result = await wrapper.search_by_org("Acme")
        assert result == cached_data
        inner.search_by_org.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_cert_org_passthrough(self, cache: DuckDBCache) -> None:
        inner = MagicMock()
        inner.get_cert_org = AsyncMock(return_value="Acme Corp")

        wrapper = CachedCTLogSource(inner, cache)
        result = await wrapper.get_cert_org(42)
        assert result == "Acme Corp"
        inner.get_cert_org.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_cache_write_failure_returns_result(self, cache: DuckDBCache) -> None:
        """Cache write failure doesn't block returning the valid result."""
        inner = MagicMock()
        fresh_data: list[dict[str, object]] = [{"cert_id": 99}]
        inner.search_by_domain = AsyncMock(return_value=fresh_data)

        wrapper = CachedCTLogSource(inner, cache)
        # Close cache to force write failure
        cache.close()
        result = await wrapper.search_by_domain("fail-write.com")
        assert result == fresh_data


class TestCachedRDAPLookup:
    @pytest.mark.asyncio
    async def test_registrant_org_hit(self, cache: DuckDBCache) -> None:
        inner = MagicMock()
        inner.get_registrant_org = AsyncMock()
        cache.put_rdap("org:example.com", {"org": "Acme Corp", "name": None})

        wrapper = CachedRDAPLookup(inner, cache)
        result = await wrapper.get_registrant_org("example.com")
        assert result == "Acme Corp"
        inner.get_registrant_org.assert_not_called()

    @pytest.mark.asyncio
    async def test_registrant_org_miss(self, cache: DuckDBCache) -> None:
        inner = MagicMock()
        inner.get_registrant_org = AsyncMock(return_value="Fresh Corp")

        wrapper = CachedRDAPLookup(inner, cache)
        result = await wrapper.get_registrant_org("miss.com")
        assert result == "Fresh Corp"
        inner.get_registrant_org.assert_called_once_with("miss.com")

    @pytest.mark.asyncio
    async def test_registrant_info_hit(self, cache: DuckDBCache) -> None:
        inner = MagicMock()
        inner.get_registrant_info = AsyncMock()
        info: dict[str, str | None] = {"org": "Acme", "name": "John", "country": "US"}
        cache.put_rdap("info:example.com", info)

        wrapper = CachedRDAPLookup(inner, cache)
        result = await wrapper.get_registrant_info("example.com")
        assert result == info
        inner.get_registrant_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_registrant_info_miss(self, cache: DuckDBCache) -> None:
        inner = MagicMock()
        info: dict[str, str | None] = {"org": "New", "name": None, "country": None}
        inner.get_registrant_info = AsyncMock(return_value=info)

        wrapper = CachedRDAPLookup(inner, cache)
        result = await wrapper.get_registrant_info("miss.com")
        assert result == info
        inner.get_registrant_info.assert_called_once_with("miss.com")

    @pytest.mark.asyncio
    async def test_cache_write_failure_returns_result(self, cache: DuckDBCache) -> None:
        """Cache write failure doesn't block returning the valid result."""
        inner = MagicMock()
        inner.get_registrant_org = AsyncMock(return_value="Write-Fail Corp")

        wrapper = CachedRDAPLookup(inner, cache)
        cache.close()
        result = await wrapper.get_registrant_org("fail-write.com")
        assert result == "Write-Fail Corp"
