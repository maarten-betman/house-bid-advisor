import numpy as np
import pandas as pd
import pytest

from bidadvisor.model.ask import AskModel, prior
from bidadvisor.model.combine import Combiner, fixed_weights


def test_priors_follow_nvm_figures():
    mean, sd = prior("tussenwoning")
    assert mean == 0.059
    assert sd == pytest.approx(0.059 / 0.8416, abs=1e-3)
    assert prior("vrijstaand")[1] == 0.08  # 48% above asking: normal spread undefined, fallback
    assert prior(None) == prior("all")


def matched(n, gap=0.10, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "woningtype": "tussenwoning",
            "gap": gap + rng.normal(0, 0.02, n),
            "vraagprijs": 400_000,
            "days_on_market": rng.integers(5, 60, n),
            "n_price_cuts": 0,
            "prijs_type": "vraagprijs | kosten koper",
            "makelaar": rng.choice(["A", "B"], n),
        }
    )


def test_gap_moves_from_prior_toward_matches_by_empirical_bayes():
    model = AskModel().fit(matched(10))
    expected = (10 * 0.059 + matched(10)["gap"].sum()) / 20
    assert model.type_mean["tussenwoning"] == pytest.approx(expected)
    assert model.coefficients == {}  # adjusters wait for 30 matches
    assert AskModel().fit(matched(40)).coefficients


def test_unfitted_ask_model_predicts_from_prior():
    frame = pd.DataFrame(
        {
            "woningtype": ["tussenwoning"],
            "vraagprijs": [400_000],
            "days_on_market": [10],
            "n_price_cuts": [0],
        }
    )
    estimate = AskModel().predict(frame).iloc[0]
    assert np.exp(estimate["mu_log"]) == pytest.approx(400_000 * 1.059)


def test_fixed_weights_trust_the_tighter_estimate_and_keep_spread_honest():
    mu, sd, w_h = fixed_weights(np.log(500_000), 0.05, np.log(520_000), 0.10)
    assert w_h == pytest.approx(0.8)
    assert np.log(500_000) < mu < np.log(520_000)
    independent = np.sqrt((0.8 * 0.05) ** 2 + (0.2 * 0.10) ** 2)
    assert independent < sd < 0.8 * 0.05 + 0.2 * 0.10  # between independence and rho = 1


def test_combiner_single_component_and_stacker():
    q, w = Combiner().combine(None, None, np.log(400_000), 0.07)
    assert w == {"hedonic": 0.0, "ask": 1.0}
    assert q["q05"] < q["q50"] < q["q95"]
    with pytest.raises(ValueError):
        Combiner().combine(None, None, None, None)

    rng = np.random.default_rng(3)
    truth = np.log(rng.uniform(300_000, 700_000, 200))
    frame = pd.DataFrame(
        {
            "mu_hedonic": truth + rng.normal(0, 0.05, 200),
            "mu_ask": truth + rng.normal(0, 0.1, 200),
            "koopsom": np.exp(truth),
        }
    )
    combiner = Combiner().fit(frame)
    assert combiner.stacker is not None
    q, w = combiner.combine(np.log(500_000), 0.05, np.log(500_000), 0.1)
    assert w["hedonic"] > w["ask"]
    assert list(q.values()) == sorted(q.values())
