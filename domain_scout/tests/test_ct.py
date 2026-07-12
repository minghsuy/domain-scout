"""Tests for CT log source — unit tests with mocks and helpers."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from domain_scout.config import ScoutConfig
from domain_scout.sources.ct_logs import (
    CTLogSource,
    CTOrgSearchUnavailableError,
    _CircuitBreaker,
    _extract_org_from_subject,
    extract_base_domain,
    is_valid_domain,
)


def _make_httpx_mock(json_payload: list[dict[str, object]]) -> AsyncMock:
    """Build a mock httpx.AsyncClient returning the given JSON response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = json_payload

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestExtractOrgFromSubject:
    def test_simple(self) -> None:
        assert _extract_org_from_subject("O=Foo") == "Foo"

    def test_with_other_attributes(self) -> None:
        assert _extract_org_from_subject("C=US, O=Example Inc, CN=example.com") == "Example Inc"

    def test_quoted_comma(self) -> None:
        assert (
            _extract_org_from_subject('C=US, O="Example, Inc.", CN=example.com') == "Example, Inc."
        )

    def test_escaped_quotes(self) -> None:
        assert _extract_org_from_subject(r'O="Org with \"quotes\""') == 'Org with "quotes"'

    def test_escaped_comma_unquoted(self) -> None:
        assert _extract_org_from_subject(r"O=ACME\, Inc., C=US") == "ACME, Inc."

    def test_spaces_around_equals(self) -> None:
        assert _extract_org_from_subject("O = Spaced Org, C=US") == "Spaced Org"

    def test_multiple_attributes(self) -> None:
        assert _extract_org_from_subject("CN=example.com, O=MyOrg") == "MyOrg"

    def test_edge_case_o_in_value(self) -> None:
        # "O=" appears inside the common name, should not confuse the parser
        assert _extract_org_from_subject('CN="O=Fake", O=RealOrg') == "RealOrg"

    def test_not_found(self) -> None:
        assert _extract_org_from_subject("CN=example.com") is None

    def test_empty(self) -> None:
        assert _extract_org_from_subject("") is None


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

    def test_ipv4_returns_none(self) -> None:
        assert extract_base_domain("192.168.1.1") is None

    def test_ipv4_common(self) -> None:
        assert extract_base_domain("10.0.0.1") is None

    def test_ipv4_public(self) -> None:
        assert extract_base_domain("8.8.8.8") is None

    def test_com_au(self) -> None:
        assert extract_base_domain("www.example.com.au") == "example.com.au"

    def test_co_jp(self) -> None:
        assert extract_base_domain("shop.example.co.jp") == "example.co.jp"

    def test_private_suffix_collapses(self) -> None:
        """Shared hosting suffixes collapse to public TLD, not tenant subdomain."""
        assert extract_base_domain("myco.github.io") == "github.io"
        assert extract_base_domain("app.myco.s3.amazonaws.com") == "amazonaws.com"


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


class TestJsonQueryFields:
    """Verify JSON fallback sets correct field values."""

    @pytest.mark.asyncio
    async def test_json_org_name_is_none(self) -> None:
        """JSON API doesn't provide subject organization — org_name must be None."""
        config = ScoutConfig()
        ct = CTLogSource(config)

        mock_client = _make_httpx_mock(
            [
                {
                    "id": 12345,
                    "common_name": "example.com",
                    "name_value": "example.com\nwww.example.com",
                    "issuer_name": "DigiCert Inc",
                    "not_before": "2024-01-01T00:00:00",
                    "not_after": "2025-01-01T00:00:00",
                }
            ]
        )

        with patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_client):
            results = await ct._json_query("example.com")

        assert len(results) == 1
        assert results[0]["org_name"] is None
        assert results[0]["subject"] == ""

    @pytest.mark.asyncio
    async def test_json_sans_parsed_from_name_value(self) -> None:
        """SANs should be parsed from name_value (newline-separated)."""
        config = ScoutConfig()
        ct = CTLogSource(config)

        mock_client = _make_httpx_mock(
            [
                {
                    "id": 99999,
                    "common_name": "test.example.com",
                    "name_value": "test.example.com\nwww.example.com\napi.example.com",
                    "issuer_name": "Let's Encrypt",
                    "not_before": "2024-06-01T00:00:00",
                    "not_after": "2024-09-01T00:00:00",
                }
            ]
        )

        with patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_client):
            results = await ct._json_query("example.com")

        assert len(results) == 1
        sans = results[0]["san_dns_names"]
        assert isinstance(sans, list)
        assert "test.example.com" in sans
        assert "www.example.com" in sans
        assert "api.example.com" in sans


class TestCircuitBreaker:
    """Unit tests for the _CircuitBreaker class."""

    def test_closed_allows(self) -> None:
        cb = _CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        assert cb.state == "closed"
        assert cb.should_allow() is True

    def test_closed_to_open_after_threshold(self) -> None:
        """N consecutive failures trip the breaker to open."""
        cb = _CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "open"
        assert cb.should_allow() is False

    def test_open_to_half_open_after_timeout(self) -> None:
        """After recovery_timeout, breaker transitions to half_open."""
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
        cb.record_failure()
        assert cb.state == "open"

        with patch.object(time, "monotonic", return_value=time.monotonic() + 11.0):
            assert cb.should_allow() is True
            assert cb.state == "half_open"

    def test_half_open_to_closed_on_success(self) -> None:
        """Successful probe in half_open resets to closed."""
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
        cb.record_failure()
        assert cb.state == "open"

        # Immediately allow (recovery_timeout=0)
        assert cb.should_allow() is True
        assert cb.state == "half_open"

        cb.record_success()
        assert cb.state == "closed"
        assert cb.should_allow() is True

    def test_half_open_to_open_on_failure(self) -> None:
        """Failed probe in half_open sends back to open."""
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
        cb.record_failure()
        assert cb.state == "open"

        assert cb.should_allow() is True
        assert cb.state == "half_open"

        cb.record_failure()
        assert cb.state == "open"

    def test_success_resets_failure_count(self) -> None:
        """A success resets the counter so it takes full threshold again to trip."""
        cb = _CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # reset
        assert cb.state == "closed"

        # Need 3 more failures to trip
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "open"

    def test_reset(self) -> None:
        cb = _CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        cb.record_failure()
        assert cb.state == "open"
        cb.reset()
        assert cb.state == "closed"
        assert cb.should_allow() is True


class TestCircuitBreakerWiring:
    """Test circuit breaker wired into CTLogSource._pg_query_with_fallback."""

    @pytest.mark.asyncio
    async def test_breaker_trips_after_threshold_skips_pg(self) -> None:
        """After cb_failure_threshold PG failures, subsequent calls skip PG entirely."""
        config = ScoutConfig(
            cb_failure_threshold=2,
            postgres_max_retries=1,
            burst_delay=0.0,
        )
        ct = CTLogSource(config)
        mock_json = _make_httpx_mock(
            [
                {
                    "id": 1,
                    "common_name": "example.com",
                    "name_value": "example.com",
                    "not_before": "2024-01-01T00:00:00",
                    "not_after": "2025-01-01T00:00:00",
                }
            ]
        )

        pg_call_count = 0

        async def failing_pg(term: str) -> list[dict[str, object]]:
            nonlocal pg_call_count
            pg_call_count += 1
            raise ConnectionError("pg down")

        with (
            patch.object(ct, "_pg_query", side_effect=failing_pg),
            patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_json),
        ):
            # Call 1: PG fails, breaker records 1 failure
            await ct._pg_query_with_fallback("test")
            assert pg_call_count == 1
            # Call 2: PG fails, breaker records 2 failures → trips open
            await ct._pg_query_with_fallback("test")
            assert pg_call_count == 2

            # Call 3: breaker is open, should skip PG entirely
            await ct._pg_query_with_fallback("test")
            assert pg_call_count == 2  # no new PG attempt

    @pytest.mark.asyncio
    async def test_breaker_recovery_probe_succeeds(self) -> None:
        """After recovery timeout, a successful probe resets the breaker."""
        config = ScoutConfig(
            cb_failure_threshold=1,
            cb_recovery_timeout=5.0,
            postgres_max_retries=1,
            burst_delay=0.0,
        )
        ct = CTLogSource(config)
        mock_json = _make_httpx_mock(
            [
                {
                    "id": 1,
                    "common_name": "example.com",
                    "name_value": "example.com",
                    "not_before": "2024-01-01T00:00:00",
                    "not_after": "2025-01-01T00:00:00",
                }
            ]
        )

        call_count = 0

        async def pg_query(term: str) -> list[dict[str, object]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("pg down")
            return [{"cert_id": 42, "common_name": "ok.com", "san_dns_names": []}]

        with (
            patch.object(ct, "_pg_query", side_effect=pg_query),
            patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_json),
        ):
            # Trip the breaker
            await ct._pg_query_with_fallback("test")
            assert ct._breaker.state == "open"

            # Advance past recovery timeout
            with patch.object(
                time,
                "monotonic",
                return_value=time.monotonic() + 6.0,
            ):
                result = await ct._pg_query_with_fallback("test")

            assert ct._breaker.state == "closed"
            assert result[0]["cert_id"] == 42

    @pytest.mark.asyncio
    async def test_shared_breaker_across_instances(self) -> None:
        """Two CTLogSource instances share the same breaker state."""
        config = ScoutConfig(
            cb_failure_threshold=1,
            postgres_max_retries=1,
            burst_delay=0.0,
        )
        ct1 = CTLogSource(config)
        ct2 = CTLogSource(config)
        mock_json = _make_httpx_mock(
            [
                {
                    "id": 1,
                    "common_name": "example.com",
                    "name_value": "example.com",
                    "not_before": "2024-01-01T00:00:00",
                    "not_after": "2025-01-01T00:00:00",
                }
            ]
        )

        async def failing_pg(term: str) -> list[dict[str, object]]:
            raise ConnectionError("pg down")

        # Trip the breaker via ct1
        with (
            patch.object(ct1, "_pg_query", side_effect=failing_pg),
            patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_json),
        ):
            await ct1._pg_query_with_fallback("test")
            # Same config → both instances resolve to the one registry breaker.
            assert ct1._breaker is ct2._breaker
            assert ct1._breaker.state == "open"

        # ct2 should also see the open breaker (shared class variable)
        pg_called = False

        async def spy_pg(term: str) -> list[dict[str, object]]:
            nonlocal pg_called
            pg_called = True
            raise ConnectionError("should not be called")

        with (
            patch.object(ct2, "_pg_query", side_effect=spy_pg),
            patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_json),
        ):
            await ct2._pg_query_with_fallback("test")

        assert not pg_called  # breaker prevented the call

    @pytest.mark.parametrize("reverse", [False, True])
    def test_different_config_breakers_independent_both_orders(self, reverse: bool) -> None:
        """Instances with DIFFERENT breaker configs get independent breakers,
        regardless of construction order (#172, regression #191).

        The pre-#172 first-wins code shared one breaker keyed to whichever
        instance was built first, so a second instance with different thresholds
        silently inherited the wrong config. The registry keys by
        ``(failure_threshold, recovery_timeout)``, so each config trips on its
        own. Parametrized over both orders because order is exactly what the old
        bug was sensitive to.
        """
        low = ScoutConfig(cb_failure_threshold=1, cb_recovery_timeout=30.0)
        high = ScoutConfig(cb_failure_threshold=99, cb_recovery_timeout=30.0)
        order = [high, low] if reverse else [low, high]
        built = {src._cfg.cb_failure_threshold: src for src in (CTLogSource(c) for c in order)}
        ct_low, ct_high = built[1], built[99]

        # The defining assertion: distinct breaker objects in EITHER order.
        # (Old first-wins code would share one object → this fails.)
        assert ct_low._breaker is not ct_high._breaker
        assert ct_low._breaker.state == "closed"
        assert ct_high._breaker.state == "closed"

        # A sub-threshold failure on the high breaker leaks into neither breaker.
        ct_high._breaker.record_failure()
        assert ct_high._breaker.state == "closed"
        assert ct_low._breaker.state == "closed"

        # The low breaker trips on its first failure without perturbing the high one.
        ct_low._breaker.record_failure()
        assert ct_low._breaker.state == "open"
        assert ct_high._breaker.state == "closed"


class TestOrgSearchFallbackUnavailable:
    """#163: org search must not silently return zero via the JSON fallback.

    The JSON API lacks the certificate subject organization, so with
    verify_org=True every fallback record would be filtered out.  The source
    must skip the JSON query and raise instead of returning an empty list.
    """

    _JSON_PAYLOAD: list[dict[str, object]] = [
        {
            "id": 1,
            "common_name": "example.com",
            "name_value": "example.com\nwww.example.com",
            "not_before": "2024-01-01T00:00:00",
            "not_after": "2025-01-01T00:00:00",
        }
    ]

    @pytest.mark.asyncio
    async def test_pg_failure_verify_org_raises_without_json_query(self) -> None:
        """Postgres failure + verify_org=True raises and never queries the JSON API."""
        config = ScoutConfig(postgres_max_retries=1, burst_delay=0.0)
        ct = CTLogSource(config)

        with (
            patch.object(ct, "_pg_query", side_effect=ConnectionError("pg down")),
            patch.object(ct, "_json_query", new_callable=AsyncMock) as json_query,
        ):
            with pytest.raises(CTOrgSearchUnavailableError):
                await ct.search_by_org("Example Corp", verify_org=True)
            json_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_breaker_open_verify_org_raises_without_json_query(self) -> None:
        """Circuit-breaker skip + verify_org=True raises; neither backend is queried."""
        config = ScoutConfig(cb_failure_threshold=1, postgres_max_retries=1, burst_delay=0.0)
        ct = CTLogSource(config)
        ct._breaker.record_failure()  # threshold=1 → trips open
        assert ct._breaker.state == "open"

        with (
            patch.object(ct, "_pg_query", new_callable=AsyncMock) as pg_query,
            patch.object(ct, "_json_query", new_callable=AsyncMock) as json_query,
        ):
            with pytest.raises(CTOrgSearchUnavailableError):
                await ct.search_by_org("Example Corp", verify_org=True)
            pg_query.assert_not_called()
            json_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_org_false_still_falls_back(self) -> None:
        """verify_org=False keeps current behavior: the JSON fallback is still used."""
        config = ScoutConfig(postgres_max_retries=1, burst_delay=0.0)
        ct = CTLogSource(config)
        mock_client = _make_httpx_mock(list(self._JSON_PAYLOAD))

        with (
            patch.object(ct, "_pg_query", side_effect=ConnectionError("pg down")),
            patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_client),
        ):
            records = await ct.search_by_org("Example Corp", verify_org=False)

        assert len(records) == 1
        assert records[0]["cert_id"] == 1

    @pytest.mark.asyncio
    async def test_domain_search_fallback_unchanged(self) -> None:
        """search_by_domain still falls back to the JSON API on Postgres failure."""
        config = ScoutConfig(postgres_max_retries=1, burst_delay=0.0)
        ct = CTLogSource(config)
        mock_client = _make_httpx_mock(list(self._JSON_PAYLOAD))

        with (
            patch.object(ct, "_pg_query", side_effect=ConnectionError("pg down")),
            patch("domain_scout.sources.ct_logs.httpx.AsyncClient", return_value=mock_client),
        ):
            records = await ct.search_by_domain("example.com")

        assert len(records) == 1
        assert records[0]["cert_id"] == 1

    @pytest.mark.asyncio
    async def test_degradation_lands_in_run_metadata_errors(self) -> None:
        """Scout surfaces the skipped org search in RunMetadata.errors."""
        from domain_scout.models import EntityInput
        from domain_scout.scout import Scout

        ct_mock = AsyncMock(
            search_by_org=AsyncMock(
                side_effect=CTOrgSearchUnavailableError(
                    "crt.sh Postgres is unavailable and the JSON API fallback cannot "
                    "verify certificate subject organization; org search skipped"
                )
            ),
            search_by_domain=AsyncMock(return_value=[]),
        )
        with patch.object(Scout, "__init__", lambda self: None):
            s = Scout.__new__(Scout)
            s.config = ScoutConfig()
            s._subsidiaries = {}
            s._gleif_con = None
            s._ct = ct_mock
            s._rdap = AsyncMock()
            s._dns = AsyncMock(bulk_resolve=AsyncMock(return_value={}), reset=MagicMock())

        result = await s._discover(EntityInput(company_name="Example Corp"))

        assert any("CT org search unavailable" in e for e in result.run_metadata.errors)


class TestPgConnect:
    """Connection-level behavior for the crt.sh Postgres backend."""

    def test_connect_passes_connect_timeout(self) -> None:
        """_connect_pg must bound the TCP connect via connect_timeout (#165)."""
        config = ScoutConfig(postgres_connect_timeout=7)  # non-default proves config wiring
        ct = CTLogSource(config)
        with patch("domain_scout.sources.ct_logs.psycopg2.connect") as mock_connect:
            ct._connect_pg()
        mock_connect.assert_called_once()
        assert mock_connect.call_args.kwargs["connect_timeout"] == 7


class TestPropertyBased:
    """Property-based tests using hypothesis."""

    @given(
        a=st.integers(min_value=0, max_value=255),
        b=st.integers(min_value=0, max_value=255),
        c=st.integers(min_value=0, max_value=255),
        d=st.integers(min_value=0, max_value=255),
    )
    def test_extract_base_domain_rejects_ipv4(self, a: int, b: int, c: int, d: int) -> None:
        """Any IPv4 address must return None from extract_base_domain."""
        ip = f"{a}.{b}.{c}.{d}"
        assert extract_base_domain(ip) is None
