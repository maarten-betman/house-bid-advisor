"""Rolling-origin backtest: for each month t, index and train on sales before t, test on month t."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from bidadvisor.model.hedonic import HedonicModel, Kind
from bidadvisor.transform.transactions import index_prices, recency_weights

MIN_COMPARABLES = 300


@dataclass(frozen=True)
class Targets:
    """M1 acceptance thresholds from the spec."""

    max_median_ape: float = 0.08
    p10_p90: tuple[float, float] = (0.75, 0.85)
    p05_p95: tuple[float, float] = (0.85, 0.95)


@dataclass(frozen=True)
class Summary:
    months: int
    n_test: int
    median_ape: float
    coverage_p10_p90: float
    coverage_p05_p95: float
    passed: bool

    def to_dict(self) -> dict:
        return asdict(self)


def rolling_backtest(
    transactions: pd.DataFrame,
    index: pd.DataFrame,
    region: str,
    months: int = 24,
    kind: Kind = "ridge",
    end: pd.Period | None = None,
) -> pd.DataFrame:
    """Per-sale predictions for the last ``months`` months, using only what each origin knew."""
    usable = transactions.loc[~transactions["is_outlier"].astype(bool)].copy()
    usable["month"] = pd.to_datetime(usable["sale_date"]).dt.to_period("M")
    last = end or usable["month"].max()
    results = []
    for month in pd.period_range(last - months + 1, last, freq="M"):
        origin = month.start_time
        test = usable.loc[usable["month"] == month]
        history = usable.loc[usable["month"] < month]
        if test.empty or len(history) < MIN_COMPARABLES:
            continue
        train = index_prices(history, index, as_of=origin, region=region)
        weights = recency_weights(train["sale_date"], origin)
        model = HedonicModel(kind=kind).fit(train.reset_index(drop=True), weights)
        predicted = model.predict_quantiles(test)
        results.append(
            pd.concat([test[["txn_id", "sale_date", "koopsom"]], predicted], axis=1).assign(
                month=month, n_train=len(train)
            )
        )
    if not results:
        raise ValueError("No month had enough history to backtest")
    return pd.concat(results, ignore_index=True)


def summarise(predictions: pd.DataFrame, targets: Targets | None = None) -> Summary:
    targets = targets or Targets()
    actual = predictions["koopsom"]
    ape = (predictions["q50"] - actual).abs() / actual
    inner = actual.between(predictions["q10"], predictions["q90"]).mean()
    outer = actual.between(predictions["q05"], predictions["q95"]).mean()
    median_ape = float(np.median(ape))
    passed = (
        median_ape <= targets.max_median_ape
        and targets.p10_p90[0] <= inner <= targets.p10_p90[1]
        and targets.p05_p95[0] <= outer <= targets.p05_p95[1]
    )
    return Summary(
        months=int(predictions["month"].nunique()),
        n_test=len(predictions),
        median_ape=median_ape,
        coverage_p10_p90=float(inner),
        coverage_p05_p95=float(outer),
        passed=bool(passed),
    )
