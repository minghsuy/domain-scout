"""Tests for CT log source — unit tests with mocks and helpers."""

from __future__ import annotations

from domain_scout.sources.ct_logs import extract_base_domain, is_valid_domain


class TestExtractBaseDomain:
    def test_simple(self) -> None:
        assert extract_base_domain("www.example.com") == "example.com"

    def test_wildcard(self) -> None:
        assert extract_base_domain("*.example.com") == "example.com"

    def test_deep_subdomain(self) -> None:
        assert extract_base_domain("a.b.c.example.com") == "example.com"

    def test_cctld(self) -> None:
        assert extract_base_domain("www.example.co.uk") == "example.co.uk"

    def test_bare(self) -> None:
        assert extract_base_domain("example.com") == "example.com"

    def test_trailing_dot(self) -> None:
        assert extract_base_domain("example.com.") == "example.com"

    def test_single_label(self) -> None:
        assert extract_base_domain("localhost") is None

    def test_empty(self) -> None:
        assert extract_base_domain("") is None


class TestIsValidDomain:
    def test_valid(self) -> None:
        assert is_valid_domain("example.com")

    def test_wildcard_only(self) -> None:
        assert not is_valid_domain("*")

    def test_localhost(self) -> None:
        assert not is_valid_domain("localhost")

    def test_ip(self) -> None:
        assert not is_valid_domain("192.168.1.1")

    def test_empty(self) -> None:
        assert not is_valid_domain("")

    def test_wildcard_subdomain(self) -> None:
        assert is_valid_domain("*.example.com")

    def test_single_label(self) -> None:
        assert not is_valid_domain("example")
