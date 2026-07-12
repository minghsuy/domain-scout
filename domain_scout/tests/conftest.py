"""Shared test fixtures and factories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from domain_scout.models import EntityInput, RunMetadata, ScoutResult
from domain_scout.sources.ct_logs import CTLogSource
from domain_scout.sources.rdap import RDAPLookup

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_shared_source_state() -> Iterator[None]:
    """Reset process-wide breaker/semaphore state shared across source instances.

    The CT/RDAP breakers and the RDAP semaphore are shared class-level state
    (#172). Clearing it around every test prevents circuit state or a stale
    event-loop-bound semaphore from bleeding across tests.
    """

    def _clear() -> None:
        CTLogSource._breakers.clear()
        RDAPLookup._breakers.clear()
        RDAPLookup._semaphore = None
        RDAPLookup._semaphore_loop_id = None

    _clear()
    yield
    _clear()


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
