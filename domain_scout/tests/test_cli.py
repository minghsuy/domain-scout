"""Tests for CLI commands."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

import pytest
from typer.testing import CliRunner

from domain_scout.cli import app
from domain_scout.models import (
    DeltaReport,
    DeltaSummary,
    EntityInput,
    RunMetadata,
    ScoutResult,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def mock_configure_logging() -> Iterator[None]:
    """Prevent CLI from reconfiguring structlog with closed streams."""
    with patch("domain_scout.cli.configure_logging"):
        yield


def _mock_result() -> ScoutResult:
    """Build a minimal ScoutResult for mocking discover."""
    from datetime import UTC, datetime

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


class TestScoutCommand:
    def test_scout_basic(self, runner: CliRunner) -> None:
        """Test basic scout command invocation."""
        with patch("domain_scout.cli.Scout") as mock_scout_cls:
            instance = mock_scout_cls.return_value
            instance.discover.return_value = _mock_result()

            result = runner.invoke(app, ["scout", "--name", "TestCorp"])

            assert result.exit_code == 0
            # _print_table writes to stderr
            assert "Entity: TestCorp" in result.stderr
            instance.discover.assert_called_once()
            # Verify correct args passed to discover
            args, kwargs = instance.discover.call_args
            assert kwargs["company_name"] == "TestCorp"
            assert kwargs["seed_domain"] == []

    def test_scout_full_options(self, runner: CliRunner) -> None:
        """Test scout command with all options."""
        with patch("domain_scout.cli.Scout") as mock_scout_cls:
            instance = mock_scout_cls.return_value
            instance.discover.return_value = _mock_result()

            result = runner.invoke(
                app,
                [
                    "scout",
                    "--name",
                    "TestCorp",
                    "--location",
                    "USA",
                    "--seed",
                    "test.com",
                    "--industry",
                    "Tech",
                    "--deep",
                    "--timeout",
                    "60",
                    "--verbose",
                ],
            )

            assert result.exit_code == 0
            instance.discover.assert_called_once()
            kwargs = instance.discover.call_args.kwargs
            assert kwargs["company_name"] == "TestCorp"
            assert kwargs["location"] == "USA"
            assert kwargs["seed_domain"] == ["test.com"]
            assert kwargs["industry"] == "Tech"

            # Verify config creation logic indirectly via Scout constructor args
            # mock_scout_cls constructor called with config
            assert mock_scout_cls.call_count == 1
            config = mock_scout_cls.call_args.kwargs.get("config")
            assert config is not None
            assert config.deep_mode is True
            # deep mode bumps timeout to at least 180
            assert config.total_timeout == 180

    def test_scout_json_output(self, runner: CliRunner) -> None:
        """Test scout command with JSON output."""
        with patch("domain_scout.cli.Scout") as mock_scout_cls:
            instance = mock_scout_cls.return_value
            instance.discover.return_value = _mock_result()

            result = runner.invoke(app, ["scout", "--name", "TestCorp", "--output", "json"])

            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert data["entity"]["company_name"] == "TestCorp"

    def test_scout_keyboard_interrupt(self, runner: CliRunner) -> None:
        """Test handling of KeyboardInterrupt."""
        with patch("domain_scout.cli.Scout") as mock_scout_cls:
            instance = mock_scout_cls.return_value
            instance.discover.side_effect = KeyboardInterrupt()

            result = runner.invoke(app, ["scout", "--name", "TestCorp"])

            assert result.exit_code == 1
            assert "Aborted" in result.stderr

    def test_scout_with_cache(self, runner: CliRunner) -> None:
        """Test scout command with cache enabled."""
        with (
            patch("domain_scout.cli.Scout") as mock_scout_cls,
            patch("domain_scout.cache.DuckDBCache") as mock_cache_cls,
        ):
            instance = mock_scout_cls.return_value
            instance.discover.return_value = _mock_result()

            result = runner.invoke(
                app,
                [
                    "scout",
                    "--name",
                    "TestCorp",
                    "--cache",
                    "--cache-dir",
                    "/tmp/cache",
                ],
            )

            assert result.exit_code == 0
            mock_cache_cls.assert_called_once_with(cache_dir="/tmp/cache")
            # Verify cache passed to Scout
            scout_kwargs = mock_scout_cls.call_args.kwargs
            assert scout_kwargs["cache"] == mock_cache_cls.return_value
            # Verify cache closed
            mock_cache_cls.return_value.close.assert_called_once()


class TestServeCommand:
    def test_serve(self, runner: CliRunner) -> None:
        """Test serve command invocation."""
        with (
            patch("uvicorn.run") as mock_run,
            patch.dict(os.environ, {}, clear=False),
        ):
            result = runner.invoke(
                app,
                [
                    "serve",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9000",
                    "--workers",
                    "2",
                    "--max-concurrent",
                    "5",
                    "--verbose",
                ],
            )
            assert result.exit_code == 0
            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert args[0] == "domain_scout.api:get_app"
            assert kwargs["host"] == "0.0.0.0"
            assert kwargs["port"] == 9000
            assert kwargs["workers"] == 2
            assert kwargs["log_level"] == "debug"

            # Check environment variables
            assert os.environ["DOMAIN_SCOUT_MAX_CONCURRENT"] == "5"

    def test_serve_workers_warning(self, runner: CliRunner) -> None:
        """Test warning when workers > 1 without no-cache."""
        with (
            patch("uvicorn.run"),
            patch.dict(os.environ, {}, clear=False),
        ):
            result = runner.invoke(app, ["serve", "--workers", "2"])
            assert result.exit_code == 0
            assert "Warning: --workers > 1 requires --no-cache" in result.stderr
            # Verify cache disabled in env var
            assert os.environ["DOMAIN_SCOUT_CACHE"] == "false"


class TestDiffCommand:
    def test_diff_basic(self, runner: CliRunner, tmp_path: Path) -> None:
        """Test diff command with temporary files."""
        base_result = _mock_result()
        curr_result = _mock_result()

        base_file = tmp_path / "base.json"
        curr_file = tmp_path / "curr.json"

        base_file.write_text(base_result.model_dump_json())
        curr_file.write_text(curr_result.model_dump_json())

        # Mock compute_delta to return a report
        mock_report = DeltaReport(
            added=[],
            removed=[],
            changed=[],
            summary=DeltaSummary(
                added=0, removed=0, changed=0, unchanged=0, baseline_total=0, current_total=0
            ),
            baseline_metadata=base_result.run_metadata,
            current_metadata=curr_result.run_metadata,
        )

        with patch("domain_scout.delta.compute_delta", return_value=mock_report):
            result = runner.invoke(app, ["diff", str(base_file), str(curr_file)])

            assert result.exit_code == 0
            assert "Summary: 0 added, 0 removed" in result.stderr
            assert "Baseline: 0 domains" in result.stderr

    def test_diff_json(self, runner: CliRunner, tmp_path: Path) -> None:
        """Test diff command with JSON output."""
        base_file = tmp_path / "base.json"
        curr_file = tmp_path / "curr.json"

        res = _mock_result()
        base_file.write_text(res.model_dump_json())
        curr_file.write_text(res.model_dump_json())

        # We don't strictly need to mock compute_delta if we use real files and logic,
        # but mocking makes it unit test. However, since we write real files,
        # let's let it run through _load_result but mock compute_delta to control output.

        mock_report = DeltaReport(
            added=[],
            removed=[],
            changed=[],
            summary=DeltaSummary(
                added=1, removed=0, changed=0, unchanged=0, baseline_total=0, current_total=1
            ),
            baseline_metadata=res.run_metadata,
            current_metadata=res.run_metadata,
        )

        with patch("domain_scout.delta.compute_delta", return_value=mock_report):
            result = runner.invoke(
                app, ["diff", str(base_file), str(curr_file), "--output", "json"]
            )

            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert data["summary"]["added"] == 1

    def test_diff_missing_file(self, runner: CliRunner) -> None:
        """Test diff command with missing file."""
        result = runner.invoke(app, ["diff", "missing.json", "current.json"])
        assert result.exit_code == 1
        assert "Error: Baseline file not found" in result.stderr


class TestCacheCommand:
    def test_cache_stats(self, runner: CliRunner) -> None:
        """Test cache stats command."""
        # Use patch.dict to ensure duckdb is available in modules if it's already imported
        # But import check is inside the function.
        # We need to mock DuckDBCache class.
        with patch("domain_scout.cache.DuckDBCache") as mock_cache_cls:
            mock_instance = mock_cache_cls.return_value
            mock_instance.__enter__.return_value = mock_instance
            mock_instance.stats.return_value = {
                "cache_dir": "/tmp",
                "ct_entries": 10,
                "rdap_entries": 5,
                "ct_oldest_age_seconds": 100,
                "rdap_oldest_age_seconds": 50,
            }

            result = runner.invoke(app, ["cache", "stats"])

            assert result.exit_code == 0
            assert "CT entries:      10" in result.stdout
            assert "RDAP entries:    5" in result.stdout
            mock_instance.stats.assert_called_once()

    def test_cache_clear(self, runner: CliRunner) -> None:
        """Test cache clear command."""
        with patch("domain_scout.cache.DuckDBCache") as mock_cache_cls:
            mock_instance = mock_cache_cls.return_value
            mock_instance.__enter__.return_value = mock_instance

            result = runner.invoke(app, ["cache", "clear"])

            assert result.exit_code == 0
            assert "Cache cleared." in result.stdout
            mock_instance.clear.assert_called_once()

    def test_cache_missing_duckdb(self, runner: CliRunner) -> None:
        """Test cache command when duckdb is missing."""
        # Simulate missing domain_scout.cache by patching sys.modules
        with patch.dict("sys.modules", {"domain_scout.cache": None}):
            result = runner.invoke(app, ["cache", "stats"])
            assert result.exit_code == 1
            assert "Error: duckdb is not installed" in result.stderr

    def test_cache_locked(self, runner: CliRunner) -> None:
        """Test cache command when database is locked."""
        with patch("domain_scout.cache.DuckDBCache") as mock_cache_cls:
            mock_instance = mock_cache_cls.return_value
            mock_instance.__enter__.side_effect = RuntimeError("Database lock error")

            result = runner.invoke(app, ["cache", "stats"])

            assert result.exit_code == 1
            assert "cache database is locked" in result.stderr
