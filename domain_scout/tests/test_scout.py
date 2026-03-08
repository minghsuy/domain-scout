"""Tests for Scout internally."""

import pytest
from unittest.mock import AsyncMock, patch

from domain_scout.scout import Scout, SOURCE_ERRORS_TOTAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_class,exc_msg",
    [
        (Exception, "Database down"),
        (TimeoutError, "Connection timed out"),
        (ConnectionError, "Connection refused"),
        (OSError, "Network unreachable"),
    ],
    ids=["generic", "timeout", "connection", "os"],
)
async def test_strategy_org_search_error_handling(
    exc_class: type[Exception], exc_msg: str
) -> None:
    """_strategy_org_search must fail open: catch any exception, return empty
    results, record the error message, and bump the Prometheus counter."""
    scout = Scout()
    scout._ct.search_by_org = AsyncMock(side_effect=exc_class(exc_msg))  # type: ignore[method-assign]

    errors: list[str] = []

    with patch("domain_scout.scout.inc") as mock_inc:
        results = await scout._strategy_org_search("Test Org", errors)

    # Fails open — returns empty list, never raises
    assert results == []
    # Error message captured
    assert len(errors) == 1
    assert exc_msg in errors[0]
    assert errors[0].startswith("CT org search failed: ")
    # Prometheus counter incremented exactly once
    mock_inc.assert_called_once_with(SOURCE_ERRORS_TOTAL, source="ct")
