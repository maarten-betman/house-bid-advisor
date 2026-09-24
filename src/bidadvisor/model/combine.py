"""Combine the hedonic and ask-anchored estimates into one final-price distribution.

Both are treated as log-normal. Before ``MIN_STACKER`` matched sales, the log means are
weighted by inverse variance and the spread assumes the two errors correlate (``RHO``):
both lean on the same market, so treating them as independent would give intervals
that are too narrow. From ``MIN_STACKER`` matches, a linear stacker learns the weights
and cross-validated residuals set conformal quantiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import KFold, cross_val_predict

from bidadvisor.model.hedonic import QUANTILES, conformal_offsets

RHO = 0.5
MIN_STACKER = 30
Z10 = NormalDist().inv_cdf(0.90)


def quantile_names() -> list[str]:
    return [f"q{round(q * 100):02d}" for q in QUANTILES]


def lognormal_from_quantiles(quantiles: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Log mean and spread implied by q50 and the q10–q90 width."""
    mu = np.log(quantiles["q50"])
    sd = (np.log(quantiles["q90"]) - np.log(quantiles["q10"])) / (2 * Z10)
    return mu, sd


def quantiles_from_lognormal(mu, sd) -> pd.DataFrame:
    return pd.DataFrame(
        {
            name: np.exp(np.asarray(mu) + NormalDist().inv_cdf(q) * np.asarray(sd))
            for name, q in zip(quantile_names(), QUANTILES, strict=True)
        }
    )


def fixed_weights(mu_h, sd_h, mu_a, sd_a, rho: float = RHO):
    """Inverse-variance weights; spread of the weighted sum under correlation ``rho``."""
    w_h = (1 / sd_h**2) / (1 / sd_h**2 + 1 / sd_a**2)
    w_a = 1 - w_h
    mu = w_h * mu_h + w_a * mu_a
    sd = np.sqrt((w_h * sd_h) ** 2 + (w_a * sd_a) ** 2 + 2 * rho * w_h * w_a * sd_h * sd_a)
    return mu, sd, w_h


@dataclass
class Combiner:
    stacker: LinearRegression | None = None
    offsets: dict[float, float] = field(default_factory=dict)
    n_matched: int = 0

    def fit(self, matched: pd.DataFrame) -> Combiner:
        """``matched``: mu_hedonic, mu_ask (log estimates made before the sale) and koopsom."""
        usable = matched.dropna(subset=["mu_hedonic", "mu_ask", "koopsom"])
        self.n_matched = len(usable)
        if self.n_matched < MIN_STACKER:
            self.stacker, self.offsets = None, {}
            return self
        X = usable[["mu_hedonic", "mu_ask"]].to_numpy()
        y = np.log(usable["koopsom"].astype(float)).to_numpy()
        cv = KFold(5, shuffle=True, random_state=0)
        residuals = y - cross_val_predict(LinearRegression(), X, y, cv=cv)
        self.stacker = LinearRegression().fit(X, y)
        self.offsets = conformal_offsets(residuals)
        return self

    def combine(
        self, mu_h: float | None, sd_h: float | None, mu_a: float | None, sd_a: float | None
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Final-price quantiles and how much each estimate counted."""
        if mu_h is None and mu_a is None:
            raise ValueError("Nothing to combine")
        if mu_a is None:
            return _row(quantiles_from_lognormal([mu_h], [sd_h])), {"hedonic": 1.0, "ask": 0.0}
        if mu_h is None:
            return _row(quantiles_from_lognormal([mu_a], [sd_a])), {"hedonic": 0.0, "ask": 1.0}
        if self.stacker is not None:
            centre = float(self.stacker.predict([[mu_h, mu_a]])[0])
            coef = self.stacker.coef_
            share = float(coef[0] / coef.sum()) if coef.sum() else 0.5
            quantiles = {
                name: float(np.exp(centre + self.offsets[q]))
                for name, q in zip(quantile_names(), QUANTILES, strict=True)
            }
            return quantiles, {"hedonic": share, "ask": 1 - share}
        mu, sd, w_h = fixed_weights(mu_h, sd_h, mu_a, sd_a)
        return _row(quantiles_from_lognormal([mu], [sd])), {"hedonic": w_h, "ask": 1 - w_h}


def _row(frame: pd.DataFrame) -> dict[str, float]:
    return {name: float(value) for name, value in frame.iloc[0].items()}
