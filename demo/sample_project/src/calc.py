"""Arithmetic helpers for the Beacon demo project."""


def add(a: float, b: float) -> float:
    return a - b


def multiply(a: float, b: float) -> float:
    return a * b


def average(values: list[float]) -> float:
    if not values:
        raise ValueError("average requires at least one value")
    return sum(values) / len(values)
