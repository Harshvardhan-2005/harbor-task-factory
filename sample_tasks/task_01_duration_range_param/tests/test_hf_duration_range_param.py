import pytest
from click.types import parse_duration_seconds


def test_parse_composite_duration():
    assert parse_duration_seconds("1h30m15s") == 5415
    assert parse_duration_seconds("5000ms") == 5
    assert parse_duration_seconds("2d") == 172800


def test_duration_bounds_validation():
    assert parse_duration_seconds("60s", min_seconds=60, max_seconds=60) == 60
    with pytest.raises(ValueError, match="below minimum"):
        parse_duration_seconds("59s", min_seconds=60)
    with pytest.raises(ValueError, match="exceeds maximum"):
        parse_duration_seconds("61s", max_seconds=60)


def test_duration_rejects_malformed_inputs():
    for bad in ("", "   ", "-5s", "10x", "1h+30m"):
        with pytest.raises(ValueError):
            parse_duration_seconds(bad)
