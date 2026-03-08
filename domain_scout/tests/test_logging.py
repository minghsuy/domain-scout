"""Tests for the logging configuration."""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock, patch

from domain_scout._logging import configure_logging


def test_configure_logging_defaults() -> None:
    """Test configure_logging with default arguments."""
    with (
        patch("structlog.configure") as mock_configure,
        patch("structlog.PrintLoggerFactory") as mock_factory,
        patch("structlog.make_filtering_bound_logger") as mock_make_filtering,
    ):
        mock_factory_instance = MagicMock()
        mock_factory.return_value = mock_factory_instance
        mock_wrapper = MagicMock()
        mock_make_filtering.return_value = mock_wrapper

        configure_logging()

        mock_factory.assert_called_once_with(file=sys.stderr)
        mock_make_filtering.assert_called_once_with(logging.WARNING)

        mock_configure.assert_called_once()
        kwargs = mock_configure.call_args.kwargs
        assert kwargs["logger_factory"] == mock_factory_instance
        assert kwargs["wrapper_class"] == mock_wrapper
        assert len(kwargs["processors"]) == 3


def test_configure_logging_no_stderr() -> None:
    """Test configure_logging with stderr=False."""
    with (
        patch("structlog.configure") as mock_configure,
        patch("structlog.PrintLoggerFactory") as mock_factory,
        patch("structlog.make_filtering_bound_logger") as mock_make_filtering,
    ):
        mock_wrapper = MagicMock()
        mock_make_filtering.return_value = mock_wrapper

        configure_logging(stderr=False)

        mock_factory.assert_not_called()
        mock_make_filtering.assert_called_once_with(logging.WARNING)

        mock_configure.assert_called_once()
        kwargs = mock_configure.call_args.kwargs
        assert kwargs["logger_factory"] is None
        assert kwargs["wrapper_class"] == mock_wrapper


def test_configure_logging_custom_level() -> None:
    """Test configure_logging with a custom log level."""
    with (
        patch("structlog.configure") as mock_configure,
        patch("structlog.PrintLoggerFactory") as mock_factory,
        patch("structlog.make_filtering_bound_logger") as mock_make_filtering,
    ):
        mock_factory_instance = MagicMock()
        mock_factory.return_value = mock_factory_instance
        mock_wrapper = MagicMock()
        mock_make_filtering.return_value = mock_wrapper

        configure_logging(level=logging.DEBUG)

        mock_factory.assert_called_once_with(file=sys.stderr)
        mock_make_filtering.assert_called_once_with(logging.DEBUG)

        mock_configure.assert_called_once()
        kwargs = mock_configure.call_args.kwargs
        assert kwargs["logger_factory"] == mock_factory_instance
        assert kwargs["wrapper_class"] == mock_wrapper
