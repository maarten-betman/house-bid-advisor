"""Hedonic price model: log koopsom_indexed from house features, with split-conformal quantiles.

A point model (ridge baseline or LightGBM challenger) predicts the log price; the most
recent sales, held out, calibrate signed residual quantiles. Adding those to a new
prediction gives q05…q95. Calibrating on the newest sales keeps intervals honest when
the market shifts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler, TargetEncoder

QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
BOUWJAAR_BANDS = [0, 1906, 1931, 1945, 1960, 1971, 1981, 1991, 2001, 2011, 2100]

NUMERIC = ["log_area", "log_plot", "has_plot", "lat", "lon"]
CATEGORICAL = ["woningtype", "bouwjaar_band", "energielabel"]
AREA = ["pc4"]

Kind = Literal["ridge", "lightgbm"]


def features(frame: pd.DataFrame) -> pd.DataFrame:
    """Model inputs from gold ``comparable`` or listing columns; optional columns may be missing."""
    out = pd.DataFrame(index=frame.index)
    out["log_area"] = np.log(frame["gebruiksoppervlakte_m2"].astype(float))
    plot = frame.get("perceel_m2", pd.Series(np.nan, index=frame.index)).astype(float)
    out["has_plot"] = plot.gt(0).astype(float)
    out["log_plot"] = np.log1p(plot.fillna(0))
    out["lat"] = frame["lat"].astype(float)
    out["lon"] = frame["lon"].astype(float)
    out["woningtype"] = frame.get("woningtype", pd.Series("onbekend", index=frame.index))
    out["bouwjaar_band"] = pd.cut(
        frame["bouwjaar"].astype(float), BOUWJAAR_BANDS, right=False
    ).astype(str)
    out["energielabel"] = frame.get("energielabel", pd.Series("onbekend", index=frame.index))
    out["pc4"] = frame["pc6"].str[:4] if "pc6" in frame else frame["pc4"].astype(str)
    for column in CATEGORICAL + AREA:
        out[column] = out[column].fillna("onbekend").astype(str)
    return out


def _point_model(kind: Kind) -> Pipeline:
    categorical = OneHotEncoder(handle_unknown="ignore", min_frequency=5)
    area = TargetEncoder(target_type="continuous", cv=KFold(5, shuffle=True, random_state=0))
    numeric = make_pipeline(SimpleImputer(strategy="median"), StandardScaler())
    prep = ColumnTransformer(
        [("num", numeric, NUMERIC), ("cat", categorical, CATEGORICAL), ("area", area, AREA)]
    )
    if kind == "ridge":
        return Pipeline([("prep", prep), ("model", Ridge(alpha=1.0))])
    from lightgbm import LGBMRegressor

    return Pipeline(
        [
            ("prep", prep),
            (
                "model",
                LGBMRegressor(
                    objective="quantile",
                    alpha=0.5,
                    n_estimators=400,
                    learning_rate=0.03,
                    num_leaves=15,
                    min_child_samples=20,
                    subsample=0.8,
                    subsample_freq=1,
                    colsample_bytree=0.8,
                    random_state=0,
                    verbose=-1,
                ),
            ),
        ]
    )


def conformal_offsets(residuals: np.ndarray, quantiles=QUANTILES) -> dict[float, float]:
    """Signed residual quantiles with the split-conformal finite-sample correction."""
    n = len(residuals)
    offsets = {}
    for q in quantiles:
        if q >= 0.5:
            level = min(1.0, np.ceil((n + 1) * q) / n)
            offsets[q] = float(np.quantile(residuals, level, method="higher"))
        else:
            level = max(0.0, np.floor((n + 1) * q) / n)
            offsets[q] = float(np.quantile(residuals, level, method="lower"))
    return offsets


@dataclass
class HedonicModel:
    kind: Kind = "ridge"
    calibration_share: float = 0.2
    pipeline: Pipeline | None = None
    offsets: dict[float, float] = field(default_factory=dict)
    n_train: int = 0
    n_calibration: int = 0

    def fit(
        self, comparables: pd.DataFrame, sample_weight: np.ndarray | None = None
    ) -> HedonicModel:
        """Fit on all but the newest ``calibration_share`` of sales; calibrate on those."""
        order = np.argsort(pd.to_datetime(comparables["sale_date"]).to_numpy(), kind="stable")
        n_cal = max(20, int(len(order) * self.calibration_share))
        if len(order) - n_cal < 50:
            raise ValueError(f"Too few comparables to fit ({len(order)})")
        train_idx, cal_idx = order[:-n_cal], order[-n_cal:]
        X = features(comparables)
        y = np.log(comparables["koopsom_indexed"].astype(float)).to_numpy()
        weights = None if sample_weight is None else np.asarray(sample_weight)[train_idx]

        self.pipeline = _point_model(self.kind)
        self.pipeline.fit(
            X.iloc[train_idx],
            y[train_idx],
            **({"model__sample_weight": weights} if weights is not None else {}),
        )
        residuals = y[cal_idx] - self.pipeline.predict(X.iloc[cal_idx])
        self.offsets = conformal_offsets(residuals)
        self.n_train, self.n_calibration = len(train_idx), len(cal_idx)
        return self

    def predict_quantiles(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Price quantiles in euros, columns q05…q95, monotone by construction."""
        if self.pipeline is None:
            raise RuntimeError("Model is not fitted")
        centre = self.pipeline.predict(features(frame))
        return pd.DataFrame(
            {f"q{round(q * 100):02d}": np.exp(centre + off) for q, off in self.offsets.items()},
            index=frame.index,
        )
