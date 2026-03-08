"""Unit tests for internal helper functions and error handling in scout.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from domain_scout._metrics import SOURCE_ERRORS_TOTAL
from domain_scout.scout import Scout


class TestStrategySeedExpansionErrorHandling:
    """Test error handling in Scout._strategy_seed_expansion."""

    @pytest.mark.asyncio
    @patch("domain_scout.scout.inc")
    async def test_search_by_domain_exception(self, mock_inc: AsyncMock) -> None:
        """Verify _strategy_seed_expansion handles CT search exceptions gracefully."""
        scout = Scout()
        # Mock _ct to raise an Exception on search_by_domain
        scout._ct = AsyncMock()
        scout._ct.search_by_domain.side_effect = Exception("Mock timeout or connection error")

        errors: list[str] = []

        results = await scout._strategy_seed_expansion(
            seed_domain="example.com",
            company_name="Example Inc",
            errors=errors
        )

        # Assert results list is empty
        assert results == []

        # Assert that the error is appended correctly
        assert len(errors) == 1
        assert "CT seed expansion failed: Mock timeout or connection error" in errors[0]

        # Assert that the metric inc() was called
        mock_inc.assert_called_once()
        args, kwargs = mock_inc.call_args
        assert args[0] is SOURCE_ERRORS_TOTAL
        assert kwargs.get("source") == "ct"
