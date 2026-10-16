"""
Statement unit guard.

Statements are stored in thousands (Yahoo's table unit), but some scraped quarters came back in
full units (x1000) or billions (x0.000001). Such a period is detected by comparing it to reference
periods of the same statement (other quarters, annual rows): consistent ratios are ~0.25-1, others
sit near a power of 1000.
"""

import math
from statistics import median
from typing import Iterable, Optional

# Per-share values and rates are never scaled by the table unit.
_UNSCALED_MARKERS = ("eps", "rate")
# Max distance (in powers of 1000) from the snapped power: ratio within ~16x of it.
_SNAP_TOLERANCE = 0.4


def _is_scaled(key: str) -> bool:
    lowered = key.lower()
    return not any(marker in lowered for marker in _UNSCALED_MARKERS)


def _number(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    return None


def scale_ratio(metrics: dict, references: Iterable[dict]) -> Optional[float]:
    """Median magnitude ratio of `metrics` vs references over shared scaled keys; None if no overlap."""
    per_reference = []
    for reference in references:
        if not reference or reference is metrics:
            continue
        ratios = []
        for key, value in metrics.items():
            if not _is_scaled(key):
                continue
            a, b = _number(value), _number(reference.get(key))
            if a and b:
                ratios.append(abs(a) / abs(b))
        if ratios:
            per_reference.append(median(ratios))
    return median(per_reference) if per_reference else None


def unit_multiplier(metrics: dict, references: Iterable[dict]) -> float:
    """Factor that brings `metrics` to the references' unit (thousands); 1 if consistent or unknown."""
    ratio = scale_ratio(metrics, references)
    if not ratio:
        return 1
    power = math.log(ratio, 1000)
    snapped = round(power)
    if snapped == 0 or abs(power - snapped) > _SNAP_TOLERANCE:
        return 1
    return 1000.0**-snapped


def rescale(metrics: dict, multiplier: float) -> dict:
    """Multiply scaled numeric values; EPS, rates and non-numeric values are kept as-is."""
    result = {}
    for key, value in metrics.items():
        number = _number(value)
        if number is None or not _is_scaled(key):
            result[key] = value
            continue
        scaled = round(number * multiplier, 3)
        result[key] = int(scaled) if scaled.is_integer() else scaled
    return result


def normalize_periods(items: list[dict], references: list[dict]) -> list[dict]:
    """
    Rescale mis-scaled items (`{"period_end_quarter", "metrics"}`) to thousands. Each item is
    compared against `references` plus the other items in the batch.
    """
    batch = [item.get("metrics") or {} for item in items]
    normalized = []
    for item, metrics in zip(items, batch):
        multiplier = unit_multiplier(metrics, [*references, *batch])
        if multiplier != 1:
            item = {**item, "metrics": rescale(metrics, multiplier)}
        normalized.append(item)
    return normalized
