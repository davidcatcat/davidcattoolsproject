"""Tiny synthetic module: #120 live proof.

Demonstrates that the gw-agent route handles code content and writes
only the allowed path (gw_agent_live_c.py).
"""


def add_two(a, b):
    """Return the sum of a and b."""
    return a + b


if __name__ == "__main__":
    print(add_two(2, 3))
