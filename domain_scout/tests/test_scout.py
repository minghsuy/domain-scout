"""Tests for Scout internally."""

import pytest
from unittest.mock import AsyncMock, patch

from domain_scout.scout import Scout, SOURCE_ERRORS_TOTAL

@pytest.mark.asyncio
async def test_strategy_org_search_error_handling() -> None:
    scout = Scout()
    scout._ct.search_by_org = AsyncMock(side_effect=Exception("Database down"))

    errors: list[str] = []

    with patch("domain_scout.scout.inc") as mock_inc:
        results = await scout._strategy_org_search("Test Org", errors)

        assert results == []
        assert errors == ["CT org search failed: Database down"]
        mock_inc.assert_called_once_with(SOURCE_ERRORS_TOTAL, source="ct")
