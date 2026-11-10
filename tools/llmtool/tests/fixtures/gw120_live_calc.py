"""Fixture: clamp helper used by gw120_live_calc_check tests."""


def clamp(value, low, high):
    """Return value limited to the range [low, high].

    Raises ValueError when low > high.
    """
    if low > high:
        raise ValueError("low must be less than or equal to high")
    if value < low:
        return low
    if value > high:
        return high
    return value
