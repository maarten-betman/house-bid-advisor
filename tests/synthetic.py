"""A synthetic market with a known price formula, for model and backtest tests."""

import numpy as np
import pandas as pd

TYPES = {"tussenwoning": 0.0, "hoekwoning": 0.04, "2-onder-1-kap": 0.12, "vrijstaand": 0.25}


def market(n: int = 3000, start: str = "2019-01-01", months: int = 84, seed: int = 0):
    """Sales with log price = f(features) + monthly market level + noise, and its price index."""
    rng = np.random.default_rng(seed)
    month_idx = rng.integers(0, months, n)
    level = np.cumsum(rng.normal(0.004, 0.006, months))
    pc4 = rng.choice(["3511", "3512", "3521", "3531"], n)
    pc4_effect = pd.Series({"3511": 0.25, "3512": 0.15, "3521": 0.0, "3531": -0.1})[pc4].to_numpy()
    woningtype = rng.choice(list(TYPES), n, p=[0.45, 0.2, 0.2, 0.15])
    area = rng.lognormal(np.log(120), 0.25, n).round()
    plot = (area * rng.uniform(1.0, 3.0, n)).round()
    bouwjaar = rng.integers(1900, 2020, n)
    log_price = (
        np.log(4200)
        + np.log(area)
        + 0.08 * np.log(plot / area)
        + pd.Series(TYPES)[woningtype].to_numpy()
        + pc4_effect
        + 0.0015 * (bouwjaar - 1960)
        + level[month_idx]
        + rng.normal(0, 0.07, n)
    )
    dates = pd.to_datetime(start) + pd.to_timedelta(
        month_idx * 30.4375 + rng.uniform(0, 28, n), unit="D"
    )
    frame = pd.DataFrame(
        {
            "bag_vbo_id": [f"0344010000{i:06d}" for i in range(n)],
            "pc6": [p + "AB" for p in pc4],
            "sale_date": dates.normalize(),
            "koopsom": np.exp(log_price).round(-3).astype(int),
            "perceel_m2": plot.astype(int),
            "gebruiksoppervlakte_m2": area,
            "bouwjaar": bouwjaar,
            "woningtype": woningtype,
            "lat": 52.09 + rng.normal(0, 0.02, n),
            "lon": 5.12 + rng.normal(0, 0.03, n),
        }
    )
    index = pd.DataFrame(
        {
            "region": "utrecht",
            "month": pd.period_range(start, periods=months, freq="M").astype(str),
            "value": 100 * np.exp(level),
        }
    )
    return frame, index
