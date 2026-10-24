"""Half of a deliberate import cycle. See cycle_b.py.

The mutual imports are legal Python - both names are only touched at call
time - but the module graph still contains a cycle.
"""

import app.cycle_b


def alpha() -> str:
    return "alpha" + app.cycle_b.beta()