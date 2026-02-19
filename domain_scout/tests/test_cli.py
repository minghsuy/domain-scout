"""Tests for CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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
def mock_configure_logging():
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
        with patch("domain_scout.cli.Scout") as MockScout:
            instance = MockScout.return_value
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
        with patch("domain_scout.cli.Scout") as MockScout:
            instance = MockScout.return_value
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
            # MockScout constructor called with config
            assert MockScout.call_count == 1
            config = MockScout.call_args.kwargs.get("config")
            assert config is not None
            assert config.deep_mode is True
            # deep mode bumps timeout to at least 180
            assert config.total_timeout == 180

    def test_scout_json_output(self, runner: CliRunner) -> None:
        """Test scout command with JSON output."""
        with patch("domain_scout.cli.Scout") as MockScout:
            instance = MockScout.return_value
            instance.discover.return_value = _mock_result()

            result = runner.invoke(
                app, ["scout", "--name", "TestCorp", "--output", "json"]
            )

            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert data["entity"]["company_name"] == "TestCorp"

    def test_scout_keyboard_interrupt(self, runner: CliRunner) -> None:
        """Test handling of KeyboardInterrupt."""
        with patch("domain_scout.cli.Scout") as MockScout:
            instance = MockScout.return_value
            instance.discover.side_effect = KeyboardInterrupt()

            result = runner.invoke(app, ["scout", "--name", "TestCorp"])

            assert result.exit_code == 1
            assert "Aborted" in result.stderr

    def test_scout_with_cache(self, runner: CliRunner) -> None:
        """Test scout command with cache enabled."""
        with (
            patch("domain_scout.cli.Scout") as MockScout,
            patch("domain_scout.cache.DuckDBCache") as MockCache,
        ):
            instance = MockScout.return_value
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
            MockCache.assert_called_once_with(cache_dir="/tmp/cache")
            # Verify cache passed to Scout
            scout_kwargs = MockScout.call_args.kwargs
            assert scout_kwargs["cache"] == MockCache.return_value
            # Verify cache closed
            MockCache.return_value.close.assert_called_once()


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
            patch("uvicorn.run") as mock_run,
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
            result = runner.invoke(app, ["diff", str(base_file), str(curr_file), "--output", "json"])

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
        with patch("domain_scout.cache.DuckDBCache") as MockCache:
            mock_instance = MockCache.return_value
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
        with patch("domain_scout.cache.DuckDBCache") as MockCache:
            mock_instance = MockCache.return_value
            mock_instance.__enter__.return_value = mock_instance

            result = runner.invoke(app, ["cache", "clear"])

            assert result.exit_code == 0
            assert "Cache cleared." in result.stdout
            mock_instance.clear.assert_called_once()

    def test_cache_missing_duckdb(self, runner: CliRunner) -> None:
        """Test cache command when duckdb is missing."""
        # This is tricky because `domain_scout.cli` imports `DuckDBCache` inside functions.
        # We need to mock `domain_scout.cache` import to fail.
        # However, `domain_scout.cli` tries to import `DuckDBCache` from `domain_scout.cache`.
        # If `domain_scout.cache` handles ImportError internally and sets duckdb=None,
        # then importing `DuckDBCache` still works but `DuckDBCache` instantiation raises ImportError.

        # Let's check `domain_scout/cli.py` again.
        # try: from domain_scout.cache import DuckDBCache; except ImportError: ...

        # And `domain_scout/cache.py` has `try: import duckdb; except ImportError: duckdb = None`.
        # And `DuckDBCache.__init__` raises ImportError if `duckdb is None`.

        # So `domain_scout.cache` can always be imported.
        # But `cli.py` does:
        # try:
        #     from domain_scout.cache import DuckDBCache
        # except ImportError:
        #     typer.echo(...)

        # Wait, if `domain_scout.cache` is importable, `from domain_scout.cache import DuckDBCache` works.
        # The ImportError inside `cli.py` is caught if `domain_scout.cache` itself fails to import, or if `DuckDBCache` is not in it.
        # Actually `DuckDBCache` is defined in `domain_scout.cache` regardless of `duckdb` presence.
        # So the `except ImportError` in `cli.py` catches nothing unless `domain_scout.cache` is missing or broken.

        # Let's re-read `cli.py`.
        #     try:
        #         from domain_scout.cache import DuckDBCache
        #     except ImportError:
        #         typer.echo("Error: duckdb is not installed...", err=True)

        # Since `domain_scout.cache` is part of the package, the import should succeed unless `duckdb` is a hard dependency at module level (it's not).
        # So the `try...except ImportError` block in `cli.py` might be unreachable unless `domain_scout.cache` behaves differently?
        # Ah, maybe the intent was `DuckDBCache` is only available if `duckdb` is installed?
        # But `domain_scout/cache.py` defines `DuckDBCache` class anyway.

        # Maybe I should simulate `ImportError` by patching `sys.modules` or using `side_effect` on import?
        # `patch.dict('sys.modules', {'domain_scout.cache': None})`?

        # Actually, let's verify if `cli.py` logic is correct.
        # If `duckdb` is not installed, `domain_scout.cache` imports fine, `DuckDBCache` is defined.
        # So `from domain_scout.cache import DuckDBCache` succeeds.
        # Then we enter `with DuckDBCache(...)` which calls `__init__`.
        # `__init__` raises `ImportError` if `duckdb` is None.
        # But `cli.py` wraps the import in try/except, NOT the instantiation (wait, no).

        # `cli.py`:
        # try:
        #     from domain_scout.cache import DuckDBCache
        # except ImportError:
        #     ... raise Exit(1)

        # try:
        #     with DuckDBCache(...) as cache:
        #         ...

        # So if `duckdb` is missing, `__init__` raises `ImportError`.
        # The second `try...except` block catches `Exception`.

        # Wait, `DuckDBCache.__init__` raises `ImportError`.
        # The second `try` block:
        # try:
        #     with DuckDBCache(...) as cache:
        #         stats = cache.stats()
        # except Exception as exc:
        #     if "lock" in str(exc)...
        #     raise

        # So `ImportError` from `__init__` would be caught by `except Exception`, not match "lock", and raise.
        # So the CLI would crash with traceback instead of nice message?

        # If I want to test the `try: import ... except ImportError` block, I need to make the import fail.
        # To do that, I can use `sys.modules`.

        with patch.dict("sys.modules", {"domain_scout.cache": None}):
            # We need to make sure `from domain_scout.cache` fails.
            # If I set it to None, it raises ModuleNotFoundError.
            # But the code catches ImportError (which ModuleNotFoundError inherits from).

            # However, since `domain_scout.cli` is already imported, it might have cached lookups?
            # No, the import is inside the function.

            # But `pytest` might have already imported `domain_scout.cache` via other tests.
            # Using `patch.dict` on `sys.modules` works for subsequent imports.

            result = runner.invoke(app, ["cache", "stats"])
            assert result.exit_code == 1
            assert "Error: duckdb is not installed" in result.stderr

    def test_cache_locked(self, runner: CliRunner) -> None:
        """Test cache command when database is locked."""
        with patch("domain_scout.cache.DuckDBCache") as MockCache:
            mock_instance = MockCache.return_value
            mock_instance.__enter__.side_effect = RuntimeError("Database lock error")

            result = runner.invoke(app, ["cache", "stats"])

            assert result.exit_code == 1
            assert "cache database is locked" in result.stderr
