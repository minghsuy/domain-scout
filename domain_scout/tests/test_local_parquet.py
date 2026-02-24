"""Tests for local parquet warehouse source."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from domain_scout.config import ScoutConfig
from domain_scout.sources.local_parquet import (
    HybridCTSource,
    LocalParquetSource,
    _fingerprint_to_cert_id,
)

if TYPE_CHECKING:
    from pathlib import Path

# Minimal schema matching warehouse parquet files
_SCHEMA = pa.schema(
    [
        pa.field("org_raw", pa.string()),
        pa.field("domain", pa.string()),
        pa.field("issuer_org", pa.string()),
        pa.field("not_before", pa.timestamp("us")),
        pa.field("not_after", pa.timestamp("us")),
        pa.field("fingerprint", pa.string()),
        pa.field("first_seen", pa.timestamp("us", tz="UTC")),
        pa.field("log_source", pa.string()),
    ]
)


def _write_test_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    """Write test data to a parquet file."""
    arrays = {name: [] for name in _SCHEMA.names}
    for row in rows:
        for name in _SCHEMA.names:
            arrays[name].append(row.get(name))
    table = pa.table(arrays, schema=_SCHEMA)
    pq.write_table(table, path)


def _make_warehouse(tmp_path: Path) -> Path:
    """Create a test warehouse with known data."""
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()

    _write_test_parquet(
        warehouse / "test.parquet",
        [
            {
                "org_raw": "Apple Inc.",
                "domain": "apple.com",
                "issuer_org": "DigiCert",
                "not_before": datetime(2024, 1, 1, tzinfo=UTC),
                "not_after": datetime(2025, 1, 1, tzinfo=UTC),
                "fingerprint": "aaa111",
                "first_seen": datetime(2024, 1, 1, tzinfo=UTC),
                "log_source": "test",
            },
            {
                "org_raw": "Apple Inc.",
                "domain": "icloud.com",
                "issuer_org": "DigiCert",
                "not_before": datetime(2024, 1, 1, tzinfo=UTC),
                "not_after": datetime(2025, 1, 1, tzinfo=UTC),
                "fingerprint": "aaa111",  # Same cert, different SAN
                "first_seen": datetime(2024, 1, 1, tzinfo=UTC),
                "log_source": "test",
            },
            {
                "org_raw": "Apple Inc.",
                "domain": "apple.com",
                "issuer_org": "DigiCert",
                "not_before": datetime(2024, 6, 1, tzinfo=UTC),
                "not_after": datetime(2025, 6, 1, tzinfo=UTC),
                "fingerprint": "aaa222",  # Different cert
                "first_seen": datetime(2024, 6, 1, tzinfo=UTC),
                "log_source": "test",
            },
            {
                "org_raw": "Microsoft Corporation",
                "domain": "microsoft.com",
                "issuer_org": "DigiCert",
                "not_before": datetime(2024, 3, 1, tzinfo=UTC),
                "not_after": datetime(2025, 3, 1, tzinfo=UTC),
                "fingerprint": "bbb111",
                "first_seen": datetime(2024, 3, 1, tzinfo=UTC),
                "log_source": "test",
            },
            {
                "org_raw": "Microsoft Corporation",
                "domain": "azure.com",
                "issuer_org": "DigiCert",
                "not_before": datetime(2024, 3, 1, tzinfo=UTC),
                "not_after": datetime(2025, 3, 1, tzinfo=UTC),
                "fingerprint": "bbb111",  # Same cert
                "first_seen": datetime(2024, 3, 1, tzinfo=UTC),
                "log_source": "test",
            },
            {
                "org_raw": "Applebee's International, Inc.",
                "domain": "applebees.com",
                "issuer_org": "Let's Encrypt",
                "not_before": datetime(2024, 2, 1, tzinfo=UTC),
                "not_after": datetime(2024, 5, 1, tzinfo=UTC),
                "fingerprint": "ccc111",
                "first_seen": datetime(2024, 2, 1, tzinfo=UTC),
                "log_source": "test",
            },
            {
                "org_raw": "Apple Inc.",
                "domain": "store.apple.com",
                "issuer_org": "DigiCert",
                "not_before": datetime(2024, 1, 1, tzinfo=UTC),
                "not_after": datetime(2025, 1, 1, tzinfo=UTC),
                "fingerprint": "aaa333",
                "first_seen": datetime(2024, 1, 1, tzinfo=UTC),
                "log_source": "test",
            },
            {
                "org_raw": "",
                "domain": "empty-org.example.com",
                "issuer_org": "Let's Encrypt",
                "not_before": datetime(2024, 1, 1, tzinfo=UTC),
                "not_after": datetime(2025, 1, 1, tzinfo=UTC),
                "fingerprint": "ddd111",
                "first_seen": datetime(2024, 1, 1, tzinfo=UTC),
                "log_source": "test",
            },
            {
                "org_raw": None,
                "domain": "null-org.example.com",
                "issuer_org": "Let's Encrypt",
                "not_before": datetime(2024, 1, 1, tzinfo=UTC),
                "not_after": datetime(2025, 1, 1, tzinfo=UTC),
                "fingerprint": "eee111",
                "first_seen": datetime(2024, 1, 1, tzinfo=UTC),
                "log_source": "test",
            },
        ],
    )
    return warehouse


@pytest.fixture()
def warehouse(tmp_path: Path) -> Path:
    return _make_warehouse(tmp_path)


@pytest.fixture()
def source(warehouse: Path) -> LocalParquetSource:
    config = ScoutConfig(
        warehouse_path=str(warehouse),
        local_mode="local_only",
        local_fuzzy_threshold=65.0,
        local_max_fuzzy_matches=10,
    )
    return LocalParquetSource(config)


class TestFingerprintToCertId:
    def test_deterministic(self) -> None:
        assert _fingerprint_to_cert_id("aaa111") == _fingerprint_to_cert_id("aaa111")

    def test_different_inputs(self) -> None:
        assert _fingerprint_to_cert_id("aaa111") != _fingerprint_to_cert_id("bbb111")


class TestLocalParquetInit:
    def test_missing_directory(self, tmp_path: Path) -> None:
        config = ScoutConfig(warehouse_path=str(tmp_path / "nonexistent"))
        with pytest.raises(FileNotFoundError, match="not found"):
            LocalParquetSource(config)

    def test_empty_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        config = ScoutConfig(warehouse_path=str(empty))
        with pytest.raises(FileNotFoundError, match="No parquet"):
            LocalParquetSource(config)

    def test_loads_org_index(self, source: LocalParquetSource) -> None:
        # Apple, Microsoft, Applebee's — empty string and None excluded
        assert len(source._org_index) == 3
        assert "" not in source._org_index


class TestSearchByOrg:
    @pytest.mark.asyncio()
    async def test_exact_match(self, source: LocalParquetSource) -> None:
        results = await source.search_by_org("Apple Inc.")
        assert len(results) >= 3
        # Should find all three Apple certs (aaa111, aaa222, aaa333)
        apple_results = [r for r in results if r["org_name"] == "Apple Inc."]
        assert len(apple_results) == 3

    @pytest.mark.asyncio()
    async def test_san_reconstruction(self, source: LocalParquetSource) -> None:
        results = await source.search_by_org("Apple Inc.")
        # Find the cert with fingerprint aaa111 (has apple.com + icloud.com)
        multi_san = [r for r in results if len(r["san_dns_names"]) > 1]
        assert len(multi_san) == 1
        assert set(multi_san[0]["san_dns_names"]) == {"apple.com", "icloud.com"}

    @pytest.mark.asyncio()
    async def test_fuzzy_match(self, source: LocalParquetSource) -> None:
        # "Apple" should fuzzy-match "Apple Inc." but not "Applebee's"
        config = ScoutConfig(
            warehouse_path=source._cfg.warehouse_path,
            local_fuzzy_threshold=70.0,
        )
        s = LocalParquetSource(config)
        results = await s.search_by_org("Apple")
        orgs = {r["org_name"] for r in results}
        assert "Apple Inc." in orgs

    @pytest.mark.asyncio()
    async def test_no_match(self, source: LocalParquetSource) -> None:
        results = await source.search_by_org("Nonexistent Corp ZZZZZ")
        assert results == []

    @pytest.mark.asyncio()
    async def test_record_structure(self, source: LocalParquetSource) -> None:
        results = await source.search_by_org("Microsoft Corporation")
        assert len(results) >= 1
        rec = results[0]
        assert "cert_id" in rec
        assert "common_name" in rec
        assert "subject" in rec
        assert "org_name" in rec
        assert "not_before" in rec
        assert "not_after" in rec
        assert "san_dns_names" in rec
        assert rec["org_name"] == "Microsoft Corporation"
        assert "microsoft.com" in rec["san_dns_names"]


class TestSearchByDomain:
    @pytest.mark.asyncio()
    async def test_exact_domain(self, source: LocalParquetSource) -> None:
        results = await source.search_by_domain("apple.com")
        # aaa111 (apple.com+icloud.com), aaa222 (apple.com), aaa333 (store.apple.com via suffix)
        cert_ids = {r["cert_id"] for r in results}
        assert len(cert_ids) == 3

    @pytest.mark.asyncio()
    async def test_suffix_match(self, source: LocalParquetSource) -> None:
        # store.apple.com should match when searching for apple.com via LIKE %.apple.com
        results = await source.search_by_domain("apple.com")
        all_sans = {s for r in results for s in r["san_dns_names"]}
        assert "store.apple.com" in all_sans

    @pytest.mark.asyncio()
    async def test_no_match(self, source: LocalParquetSource) -> None:
        results = await source.search_by_domain("nonexistent.example.org")
        assert results == []


class TestGetCertOrg:
    @pytest.mark.asyncio()
    async def test_returns_none(self, source: LocalParquetSource) -> None:
        result = await source.get_cert_org(12345)
        assert result is None


class TestHybridCTSource:
    @pytest.mark.asyncio()
    async def test_returns_local_when_found(self, source: LocalParquetSource) -> None:
        remote = AsyncMock()
        hybrid = HybridCTSource(source, remote)
        results = await hybrid.search_by_org("Apple Inc.")
        assert len(results) >= 1
        remote.search_by_org.assert_not_called()

    @pytest.mark.asyncio()
    async def test_falls_back_to_remote(self, source: LocalParquetSource) -> None:
        remote = AsyncMock()
        remote.search_by_org.return_value = [{"cert_id": 1, "san_dns_names": ["fallback.com"]}]
        hybrid = HybridCTSource(source, remote)
        results = await hybrid.search_by_org("Nonexistent Corp ZZZZZ")
        assert len(results) == 1
        remote.search_by_org.assert_called_once()

    @pytest.mark.asyncio()
    async def test_domain_local_first(self, source: LocalParquetSource) -> None:
        remote = AsyncMock()
        hybrid = HybridCTSource(source, remote)
        results = await hybrid.search_by_domain("apple.com")
        assert len(results) >= 1
        remote.search_by_domain.assert_not_called()

    @pytest.mark.asyncio()
    async def test_domain_fallback(self, source: LocalParquetSource) -> None:
        remote = AsyncMock()
        remote.search_by_domain.return_value = [{"cert_id": 2, "san_dns_names": ["x.com"]}]
        hybrid = HybridCTSource(source, remote)
        results = await hybrid.search_by_domain("nonexistent.example.org")
        assert len(results) == 1
        remote.search_by_domain.assert_called_once()

    @pytest.mark.asyncio()
    async def test_get_cert_org_delegates(self, source: LocalParquetSource) -> None:
        remote = AsyncMock()
        remote.get_cert_org.return_value = "Test Org"
        hybrid = HybridCTSource(source, remote)
        result = await hybrid.get_cert_org(123)
        assert result == "Test Org"
        remote.get_cert_org.assert_called_once_with(123)


class TestEdgeCases:
    @pytest.mark.asyncio()
    async def test_empty_org_excluded_from_results(self, source: LocalParquetSource) -> None:
        """Empty string org_raw should not appear in org_index or search results."""
        results = await source.search_by_org("")
        assert results == []

    @pytest.mark.asyncio()
    async def test_null_org_excluded(self, source: LocalParquetSource) -> None:
        """Null org_raw rows should not appear in org_index."""
        # Search for the domain that has null org — it exists but shouldn't match org search
        results = await source.search_by_domain("null-org.example.com")
        assert len(results) == 1
        assert results[0]["org_name"] is None

    def test_close(self, source: LocalParquetSource) -> None:
        """close() should not raise."""
        source.close()
