"""Shared test fixtures and factories."""

from __future__ import annotations

from datetime import UTC, datetime

from domain_scout.models import EntityInput, RunMetadata, ScoutResult


def mock_result() -> ScoutResult:
    """Build a minimal ScoutResult for mocking discover_async."""
    return ScoutResult(
        entity=EntityInput(company_name="TestCorp", seed_domain=["test.com"]),
        domains=[],
        run_metadata=RunMetadata(
            tool_version="0.3.0",
            timestamp=datetime.now(UTC),
            elapsed_seconds=1.0,
            domains_found=0,
        ),
    )
