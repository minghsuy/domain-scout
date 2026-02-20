"""Tests for DNSChecker in domain_scout.sources.dns_utils."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import httpx
import pytest

from domain_scout.config import ScoutConfig
from domain_scout.sources.dns_utils import DNSChecker


class TestDNSChecker:
    @pytest.fixture
    def checker(self) -> DNSChecker:
        config = ScoutConfig()
        return DNSChecker(config)

    @pytest.mark.asyncio
    async def test_resolves_success(self, checker: DNSChecker) -> None:
        """resolves() should return True if A or AAAA records exist."""
        # Mock _resolver.resolve to return something
        checker._resolver.resolve = AsyncMock()

        # Test success
        assert await checker.resolves("example.com") is True
        checker._resolver.resolve.assert_called()

    @pytest.mark.asyncio
    async def test_resolves_failure(self, checker: DNSChecker) -> None:
        """resolves() should return False if no records found."""
        checker._resolver.resolve = AsyncMock(side_effect=dns.exception.DNSException)
        assert await checker.resolves("example.com") is False

    @pytest.mark.asyncio
    async def test_get_ips(self, checker: DNSChecker) -> None:
        """get_ips() should return a list of IP strings."""
        mock_answer_a = [MagicMock()]
        mock_answer_a[0].to_text.return_value = "1.2.3.4"

        mock_answer_aaaa = [MagicMock()]
        mock_answer_aaaa[0].to_text.return_value = "2001:db8::1"

        async def side_effect(domain, rdtype):
            if rdtype == dns.rdatatype.A:
                return mock_answer_a
            elif rdtype == dns.rdatatype.AAAA:
                return mock_answer_aaaa
            raise dns.exception.DNSException

        checker._resolver.resolve = AsyncMock(side_effect=side_effect)

        ips = await checker.get_ips("example.com")
        assert "1.2.3.4" in ips
        assert "2001:db8::1" in ips

    @pytest.mark.asyncio
    async def test_get_nameservers(self, checker: DNSChecker) -> None:
        """get_nameservers() should return a sorted list of normalized NS records."""
        mock_answer = [MagicMock(), MagicMock()]
        mock_answer[0].to_text.return_value = "ns1.example.com."
        mock_answer[1].to_text.return_value = "ns2.example.com."

        checker._resolver.resolve = AsyncMock(return_value=mock_answer)

        ns = await checker.get_nameservers("example.com")
        assert ns == ["ns1.example.com", "ns2.example.com"]

    @pytest.mark.asyncio
    async def test_shares_infrastructure_ns(self, checker: DNSChecker) -> None:
        """shares_infrastructure() returns True if nameservers overlap."""
        with patch.object(checker, "get_nameservers") as mock_ns:
            mock_ns.side_effect = [
                ["ns1.example.com", "ns2.example.com"], # domain_a
                ["ns2.example.com", "ns3.example.com"], # domain_b
            ]
            # We don't need get_ips if NS check passes
            assert await checker.shares_infrastructure("a.com", "b.com") is True

    @pytest.mark.asyncio
    async def test_shares_infrastructure_ips(self, checker: DNSChecker) -> None:
        """shares_infrastructure() returns True if IP prefixes overlap."""
        with patch.object(checker, "get_nameservers", return_value=[]), \
             patch.object(checker, "get_ips") as mock_ips:
            mock_ips.side_effect = [
                ["192.168.1.10"], # domain_a
                ["192.168.1.20"], # domain_b (same /24)
            ]
            assert await checker.shares_infrastructure("a.com", "b.com") is True

    @pytest.mark.asyncio
    async def test_shares_infrastructure_none(self, checker: DNSChecker) -> None:
        """shares_infrastructure() returns False if nothing overlaps."""
        with patch.object(checker, "get_nameservers", return_value=[]), \
             patch.object(checker, "get_ips") as mock_ips:
            mock_ips.side_effect = [
                ["192.168.1.10"], # domain_a
                ["10.0.0.1"],     # domain_b (different /24)
            ]
            assert await checker.shares_infrastructure("a.com", "b.com") is False

    @pytest.mark.asyncio
    async def test_bulk_resolve(self, checker: DNSChecker) -> None:
        """bulk_resolve() should resolve multiple domains concurrently."""
        with patch.object(checker, "resolves") as mock_resolves:
            mock_resolves.side_effect = lambda d: d == "valid.com"

            domains = ["valid.com", "invalid.com"]
            results = await checker.bulk_resolve(domains)

            assert results["valid.com"] is True
            assert results["invalid.com"] is False

    @pytest.mark.asyncio
    async def test_geodns_resolve_success(self, checker: DNSChecker) -> None:
        """geodns_resolve() returns True if Shodan returns answers."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"answers": [{"type": "A", "value": "1.1.1.1"}]}]
        mock_client.get.return_value = mock_resp

        assert await checker.geodns_resolve("example.com", mock_client) is True

    @pytest.mark.asyncio
    async def test_geodns_resolve_nxdomain(self, checker: DNSChecker) -> None:
        """geodns_resolve() returns False if Shodan returns 500 (NXDOMAIN)."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client.get.return_value = mock_resp

        assert await checker.geodns_resolve("example.com", mock_client) is False

    @pytest.mark.asyncio
    async def test_geodns_resolve_error(self, checker: DNSChecker) -> None:
        """geodns_resolve() returns False on HTTP error."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.side_effect = httpx.HTTPError("Boom")

        assert await checker.geodns_resolve("example.com", mock_client) is False
