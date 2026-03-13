"""Unit tests for the scorer module."""

from __future__ import annotations

from domain_scout.scorer import _isotonic_interpolate


def test_isotonic_interpolate_below_min() -> None:
    """Test when x is less than or equal to the minimum x_val."""
    x_vals = [0.0, 0.5, 1.0]
    y_vals = [0.1, 0.4, 0.9]

    # x is exactly at the minimum
    assert _isotonic_interpolate(0.0, x_vals, y_vals) == 0.1
    # x is below the minimum
    assert _isotonic_interpolate(-0.5, x_vals, y_vals) == 0.1


def test_isotonic_interpolate_above_max() -> None:
    """Test when x is greater than or equal to the maximum x_val."""
    x_vals = [0.0, 0.5, 1.0]
    y_vals = [0.1, 0.4, 0.9]

    # x is exactly at the maximum
    assert _isotonic_interpolate(1.0, x_vals, y_vals) == 0.9
    # x is above the maximum
    assert _isotonic_interpolate(1.5, x_vals, y_vals) == 0.9


def test_isotonic_interpolate_exact_match() -> None:
    """Test when x matches one of the x_vals exactly."""
    x_vals = [0.0, 0.5, 1.0]
    y_vals = [0.1, 0.4, 0.9]

    assert _isotonic_interpolate(0.5, x_vals, y_vals) == 0.4


def test_isotonic_interpolate_linear() -> None:
    """Test linear interpolation between points."""
    x_vals = [0.0, 0.5, 1.0]
    y_vals = [0.0, 0.5, 1.0]

    # x is exactly halfway between 0.0 and 0.5
    # t = (0.25 - 0.0) / 0.5 = 0.5
    # y = 0.0 + 0.5 * (0.5 - 0.0) = 0.25
    assert _isotonic_interpolate(0.25, x_vals, y_vals) == 0.25

    # x is halfway between 0.5 and 1.0
    assert _isotonic_interpolate(0.75, x_vals, y_vals) == 0.75


def test_isotonic_interpolate_zero_dx() -> None:
    """Test when dx is 0 (duplicate x_vals) to ensure no ZeroDivisionError."""
    x_vals = [0.0, 0.5, 0.5, 1.0]
    y_vals = [0.1, 0.4, 0.6, 0.9]

    # Due to the dx else 0.0 check, t will be 0.0
    # y = y_vals[i] + 0.0 = y_vals[i]
    assert _isotonic_interpolate(0.5, x_vals, y_vals) == 0.4
