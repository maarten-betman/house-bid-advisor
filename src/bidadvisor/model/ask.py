"""Ask-anchored estimate: final price = vraagprijs × (1 + gap).

The gap per woningtype starts at the NVM figure and moves toward matched watchlist
sales by empirical Bayes (the prior counts as ``prior_strength`` sales). Adjusters
(asking vs. hedonic P50, days on market, price cuts, 'vanaf' pricing) and a shrunk
makelaar effect switch on from ``min_for_adjusters`` matched sales; before that they
are zero rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

# NVM Marktoverzicht Q2 2026, national: (asking-to-sale gap, share sold above asking).
NVM_PRIORS: dict[str, tuple[float, float]] = {
    "tussenwoning": (0.059, 0.80),
    "hoekwoning": (0.044, 0.73),
    "2-onder-1-kap": (0.036, 0.68),
    "vrijstaand": (0.006, 0.48),
    "appartement": (0.056, 0.73),
    "all": (0.046, 0.71),
}
MIN_SD, MAX_SD, FALLBACK_SD = 0.04, 0.10, 0.08
ADJUSTERS = ["ask_vs_hedonic", "log_days_on_market", "n_price_cuts", "is_vanaf"]


def prior(woningtype: str | None) -> tuple[float, float]:
    """Mean gap and its spread; the spread follows from the share above asking under normality."""
    mean, share_above = NVM_PRIORS.get(woningtype or "all", NVM_PRIORS["all"])
    z = NormalDist().inv_cdf(share_above)
    sd = mean / z if z > 0.1 else FALLBACK_SD
    return mean, float(np.clip(sd, MIN_SD, MAX_SD))


def adjuster_frame(frame: pd.DataFrame) -> pd.DataFrame:
    ask = frame["vraagprijs"].astype(float)
    hedonic = frame.get("hedonic_q50", pd.Series(np.nan, index=frame.index)).astype(float)
    prijs_type = frame.get("prijs_type", pd.Series("", index=frame.index)).fillna("")
    return pd.DataFrame(
        {
            "ask_vs_hedonic": np.log(ask / hedonic).fillna(0.0),
            "log_days_on_market": np.log1p(frame["days_on_market"].astype(float).clip(lower=0)),
            "n_price_cuts": frame["n_price_cuts"].astype(float),
            "is_vanaf": prijs_type.str.contains("vanaf", case=False).astype(float),
        },
        index=frame.index,
    )


@dataclass
class AskModel:
    prior_strength: float = 10.0
    min_for_adjusters: int = 30
    makelaar_shrinkage: float = 5.0
    type_mean: dict[str, float] = field(default_factory=dict)
    type_sd: dict[str, float] = field(default_factory=dict)
    coefficients: dict[str, float] = field(default_factory=dict)
    makelaar_effect: dict[str, float] = field(default_factory=dict)
    n_matched: int = 0

    def fit(self, matched: pd.DataFrame) -> AskModel:
        """``matched``: woningtype, gap, vraagprijs (last ask), days_on_market, n_price_cuts,
        prijs_type, makelaar, and optionally hedonic_q50 from the score before the sale."""
        self.n_matched = len(matched)
        k = self.prior_strength
        types = matched["woningtype"].fillna("all") if len(matched) else pd.Series(dtype=str)
        for woningtype in NVM_PRIORS:
            mean0, sd0 = prior(woningtype)
            gaps = matched.loc[types == woningtype, "gap"].astype(float)
            n = len(gaps)
            self.type_mean[woningtype] = (k * mean0 + gaps.sum()) / (k + n)
            spread = ((gaps - self.type_mean[woningtype]) ** 2).sum()
            self.type_sd[woningtype] = float(np.sqrt((k * sd0**2 + spread) / (k + n)))

        self.coefficients, self.makelaar_effect = {}, {}
        if self.n_matched < self.min_for_adjusters:
            return self
        residual = matched["gap"].astype(float) - types.map(self._mean)
        features = adjuster_frame(matched)
        ridge = Ridge(alpha=1.0).fit(features, residual)
        self.coefficients = dict(zip(ADJUSTERS, map(float, ridge.coef_), strict=True))
        self.coefficients["intercept"] = float(ridge.intercept_)
        left = residual - ridge.predict(features)
        by_makelaar = left.groupby(matched["makelaar"].fillna("onbekend")).agg(["sum", "count"])
        self.makelaar_effect = (
            by_makelaar["sum"] / (by_makelaar["count"] + self.makelaar_shrinkage)
        ).to_dict()
        return self

    def _mean(self, woningtype: str) -> float:
        return self.type_mean.get(woningtype, self.type_mean.get("all", prior(None)[0]))

    def _sd(self, woningtype: str) -> float:
        return self.type_sd.get(woningtype, self.type_sd.get("all", prior(None)[1]))

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Log-space mean and spread of the final price, plus the expected gap."""
        if not self.type_mean:
            self.fit(pd.DataFrame(columns=["woningtype", "gap"]))
        types = frame["woningtype"].fillna("all")
        gap = types.map(self._mean).astype(float)
        if self.coefficients:
            features = adjuster_frame(frame)
            gap += self.coefficients["intercept"] + sum(
                features[name] * self.coefficients[name] for name in ADJUSTERS
            )
            makelaar = frame.get("makelaar", pd.Series(None, index=frame.index))
            gap += makelaar.fillna("onbekend").map(self.makelaar_effect).fillna(0.0)
        sd = types.map(self._sd).astype(float)
        return pd.DataFrame(
            {
                "gap_mean": gap,
                "mu_log": np.log(frame["vraagprijs"].astype(float)) + np.log1p(gap),
                "sd_log": sd / (1 + gap),
            },
            index=frame.index,
        )
