"""Tests for internal Scout functionality and error handling."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout


def _make_scout(**overrides: object) -> Scout:
    """Create a Scout with patched __init__ and optional attribute overrides."""
    with patch.object(Scout, "__init__", lambda self: None):
        s = Scout.__new__(Scout)
        s.config = ScoutConfig()
        s._dns = AsyncMock()
        s._rdap = AsyncMock()
        s._ct = AsyncMock()
        for k, v in overrides.items():
            setattr(s, k, v)
        return s


class TestScoutValidateSeed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc_cls,exc_msg",
        [
            (Exception, "Connection reset by peer"),
            (TimeoutError, "RDAP request timed out"),
            (ConnectionError, "Connection refused"),
        ],
        ids=["generic-exception", "timeout-error", "connection-error"],
    )
    async def test_validate_seed_rdap_error_handling(
        self, exc_cls: type[BaseException], exc_msg: str
    ) -> None:
        """RDAP exceptions during _validate_seed are caught, recorded, and don't propagate."""
        scout = _make_scout()
        scout._dns.resolves.return_value = True
        scout._rdap.get_registrant_org.side_effect = exc_cls(exc_msg)
        scout._ct.search_by_domain.return_value = []

        errors: list[str] = []
        # Must not raise — graceful degradation
        result = await scout._validate_seed(
            seed="example.com",
            company_name="Completely Different Inc",
            all_seeds=["example.com"],
            errors=errors,
        )

        # Error was recorded
        assert len(errors) == 1
        assert f"RDAP lookup failed for example.com: {exc_msg}" in errors[0]

        # Assessment still proceeds (resolves=True, no matching org → suspicious)
        assert result["seed"] == "example.com"
        assert result["assessment"] == "suspicious"
        assert result["org_name"] is None
        assert result["co_hosted_seeds"] == []

    @pytest.mark.asyncio
    async def test_validate_seed_no_rdap_error(self) -> None:
        """When RDAP succeeds, no errors are recorded."""
        scout = _make_scout()
        scout._dns.resolves.return_value = True
        scout._rdap.get_registrant_org.return_value = "Completely Different Inc"
        scout._ct.search_by_domain.return_value = []

        errors: list[str] = []
        result = await scout._validate_seed(
            seed="example.com",
            company_name="Completely Different Inc",
            all_seeds=["example.com"],
            errors=errors,
        )

        assert errors == []
        assert result["assessment"] == "confirmed"
