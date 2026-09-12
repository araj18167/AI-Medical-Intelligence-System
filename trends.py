

from typing import List, Optional


def calculate_trend(values: List[float], threshold_pct: float = 3.0) -> str:
    """
    Compares the first and last recorded value to judge overall direction.
    threshold_pct = how much % change counts as a real trend vs just noise.
    """
    if len(values) < 2:
        return "insufficient_data"

    first = values[0]
    last = values[-1]

    if first == 0:
        return "insufficient_data"

    change_pct = ((last - first) / first) * 100

    if change_pct > threshold_pct:
        return "rising"
    elif change_pct < -threshold_pct:
        return "falling"
    else:
        return "stable"


def build_metric_trend(values: List[float]) -> dict:
    """Packages a list of values into a trend summary."""
    return {
        "values": values,
        "latest": values[-1] if values else None,
        "trend": calculate_trend(values)
    }
