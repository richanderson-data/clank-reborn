"""Report formatting for the Beacon demo project."""

from calc import add


def summary(values: list[float]) -> str:
    total = 0.0
    for value in values:
        total = add(total, value)
    return f"{len(values)} values, total={total}"
