"""Tests for internal Scout functionality and error handling."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from domain_scout.scout import Scout


class TestScoutValidateSeed:
    @pytest.mark.asyncio
    async def test_validate_seed_rdap_error_handling(self) -> None:
        """Test that RDAP exceptions during _validate_seed are caught and recorded."""
        scout = Scout()

        # Mock dependencies
        scout._dns = AsyncMock()  # type: ignore[assignment]
        scout._dns.resolves.return_value = True

        scout._rdap = AsyncMock()  # type: ignore[assignment]
        scout._rdap.get_registrant_org.side_effect = Exception("Connection reset by peer")

        scout._ct = AsyncMock()  # type: ignore[assignment]
        scout._ct.search_by_domain.return_value = []

        # Run _validate_seed
        errors: list[str] = []
        result = await scout._validate_seed(
            seed="example.com",
            # Pass a different company name so slug matching score is below threshold
            company_name="Completely Different Inc",
            all_seeds=["example.com"],
            errors=errors
        )

        # Verify the error was recorded
        assert len(errors) == 1
        assert "RDAP lookup failed for example.com: Connection reset by peer" in errors[0]

        # Verify assessment still proceeds (resolves=True and no matching org means suspicious)
        assert result["seed"] == "example.com"
        assert result["assessment"] == "suspicious"
        assert result["org_name"] is None
        assert result["co_hosted_seeds"] == []
