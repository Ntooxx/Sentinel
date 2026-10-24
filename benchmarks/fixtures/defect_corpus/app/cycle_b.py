"""Half of a deliberate import cycle. See cycle_a.py."""

import app.cycle_a


def beta() -> str:
    return "beta" + app.cycle_a.alpha()