"""Taxateur-style check: the k nearest comparable sales, scaled to the subject's floor area.

Its P50 and spread also serve as the taxatie proxy T in the bid optimizer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# One unit of distance ≈ 1 km, 25% size difference, 25 years age difference.
SCALE = {"km": 1.0, "log_area": np.log(1.25), "bouwjaar": 25.0}
TYPE_PENALTY = 3.0


def _km(lat1, lon1, lat2, lon2):
    dy = (lat2 - lat1) * 111.32
    dx = (lon2 - lon1) * 111.32 * np.cos(np.radians((lat1 + lat2) / 2))
    return np.hypot(dx, dy)


def nearest(subject: pd.Series, comparables: pd.DataFrame, k: int = 8) -> pd.DataFrame:
    """The ``k`` closest non-outlier sales by distance, type, size and age."""
    pool = comparables
    if "is_outlier" in pool:
        pool = pool.loc[~pool["is_outlier"].astype(bool)]
    distance = np.sqrt(
        (_km(subject["lat"], subject["lon"], pool["lat"], pool["lon"]) / SCALE["km"]) ** 2
        + (
            (np.log(pool["gebruiksoppervlakte_m2"]) - np.log(subject["gebruiksoppervlakte_m2"]))
            / SCALE["log_area"]
        )
        ** 2
        + ((pool["bouwjaar"] - subject["bouwjaar"]) / SCALE["bouwjaar"]) ** 2
    )
    if "woningtype" in pool and pd.notna(subject.get("woningtype")):
        distance = distance + TYPE_PENALTY * (pool["woningtype"] != subject["woningtype"])
    return pool.assign(distance=distance).nsmallest(k, "distance")


def taxatie_proxy(subject: pd.Series, comparables: pd.DataFrame, k: int = 8) -> dict[str, float]:
    """P10/P50/P90 of the subject's value from its nearest comparables' indexed price per m²."""
    near = nearest(subject, comparables, k)
    per_m2 = near["koopsom_indexed"] / near["gebruiksoppervlakte_m2"]
    p10, p50, p90 = np.quantile(per_m2, [0.1, 0.5, 0.9]) * subject["gebruiksoppervlakte_m2"]
    return {
        "taxatie_p10": float(p10),
        "taxatie_p50": float(p50),
        "taxatie_p90": float(p90),
        "n_comparables": int(len(near)),
    }
