"""Tests for the pure sensor helper functions."""

from __future__ import annotations

import pytest

from custom_components.pcloud_backup.sensor import _bytes_to_human_readable


@pytest.mark.parametrize("value", [None, -1, -1024, "not-a-number", object()])
def test_invalid_values_return_none(value: object) -> None:
    """None, negative and non-numeric inputs have no display value."""
    assert _bytes_to_human_readable(value) == (None, "B")


def test_zero_bytes() -> None:
    """Zero is reported as 0 B."""
    assert _bytes_to_human_readable(0) == (0.0, "B")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, (1.0, "B")),
        (1023, (1023.0, "B")),
        (1024, (1.0, "KiB")),
        (1536, (1.5, "KiB")),
        (1024**2 - 1, (1024.0, "KiB")),
        (1024**2, (1.0, "MiB")),
        (5 * 1024**3, (5.0, "GiB")),
        (2 * 1024**4, (2.0, "TiB")),
        (3 * 1024**5, (3.0, "PiB")),
        # Values beyond the largest unit stay in PiB instead of overflowing.
        (4 * 1024**6, (4096.0, "PiB")),
    ],
)
def test_binary_unit_boundaries(value: int, expected: tuple[float, str]) -> None:
    """Values are scaled with binary (1024) steps and rounded to 2 decimals."""
    assert _bytes_to_human_readable(value) == expected


@pytest.mark.parametrize(("value", "expected"), [("2048", (2.0, "KiB")), (1536.9, (1.5, "KiB"))])
def test_numeric_strings_and_floats_are_accepted(
    value: str | float, expected: tuple[float, str]
) -> None:
    """Numeric strings and floats are coerced to whole bytes first."""
    assert _bytes_to_human_readable(value) == expected
