import numpy as np
import pandas as pd
import pytest
from synthetic import market

from bidadvisor.model.backtest import rolling_backtest, summarise
from bidadvisor.model.comparables import taxatie_proxy
from bidadvisor.model.hedonic import HedonicModel, conformal_offsets
from bidadvisor.transform.transactions import build_transactions, index_prices


def test_conformal_offsets_are_monotone_and_cover():
    residuals = np.random.default_rng(1).normal(0, 1, 500)
    offsets = conformal_offsets(residuals)
    values = list(offsets.values())
    assert values == sorted(values)
    assert offsets[0.05] < -1.5 and offsets[0.95] > 1.5


@pytest.fixture(scope="module")
def comparables():
    frame, index = market()
    txns = build_transactions(frame)
    return index_prices(txns, index, pd.Timestamp("2026-02-01"), "utrecht"), txns, index


@pytest.mark.parametrize("kind", ["ridge", "lightgbm"])
def test_hedonic_intervals_cover_on_holdout(comparables, kind):
    data, _, _ = comparables
    data = data.loc[~data["is_outlier"]].reset_index(drop=True)
    train, test = data.iloc[:2200], data.iloc[2200:]
    model = HedonicModel(kind=kind).fit(train)
    q = model.predict_quantiles(test)
    actual = test["koopsom_indexed"]
    assert (q.diff(axis=1).iloc[:, 1:] >= 0).all().all()
    assert 0.70 <= actual.between(q["q10"], q["q90"]).mean() <= 0.90
    assert np.median((q["q50"] - actual).abs() / actual) < 0.08


def test_taxatie_proxy_brackets_a_typical_house(comparables):
    data, _, _ = comparables
    subject = data.iloc[0]
    proxy = taxatie_proxy(subject, data.iloc[1:])
    assert proxy["n_comparables"] == 8
    assert proxy["taxatie_p10"] <= proxy["taxatie_p50"] <= proxy["taxatie_p90"]
    assert abs(proxy["taxatie_p50"] / subject["koopsom_indexed"] - 1) < 0.25


def test_rolling_backtest_meets_m1_targets_on_synthetic_market(comparables):
    _, txns, index = comparables
    predictions = rolling_backtest(txns, index, region="utrecht", months=6)
    summary = summarise(predictions)
    assert summary.months == 6
    assert summary.median_ape < 0.08
    assert 0.70 <= summary.coverage_p10_p90 <= 0.90
