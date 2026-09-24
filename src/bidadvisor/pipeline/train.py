"""Training: hedonic model on Kadaster comparables; ask model and combiner on matched sales."""

from __future__ import annotations

import pickle
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from bidadvisor.model.ask import AskModel
from bidadvisor.model.backtest import MIN_COMPARABLES, rolling_backtest, summarise
from bidadvisor.model.combine import Combiner
from bidadvisor.model.hedonic import HedonicModel, Kind
from bidadvisor.storage.lake import Lake
from bidadvisor.transform.matching import episodes, match_sales
from bidadvisor.transform.transactions import index_prices, recency_weights

INDEX_CSV = "reference/cbs_index.csv"
BAG_ATTRIBUTES = "reference/bag_attributes.parquet"


def _read(lake: Lake, layer: str, name: str) -> pd.DataFrame | None:
    try:
        return lake.read_table(layer, name)
    except FileNotFoundError:
        return None


def save_model(lake: Lake, name: str, model) -> None:
    path = lake.path("gold", f"models/{name}.pkl")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(model))
    tmp.replace(path)


def load_model(lake: Lake, name: str):
    """Our own pickles, written by ``save_model``; never load one from elsewhere."""
    path = lake.path("gold", f"models/{name}.pkl")
    return pickle.loads(path.read_bytes()) if path.exists() else None


def load_index(lake: Lake) -> pd.DataFrame | None:
    path = lake.path("bronze", INDEX_CSV)
    return pd.read_csv(path) if path.exists() else None


def train_hedonic(
    lake: Lake,
    region: str,
    as_of: pd.Timestamp,
    kind: Kind = "ridge",
    backtest_months: int = 24,
) -> dict:
    """Fit on every usable sale indexed to ``as_of``; keep comparables for the taxatie proxy."""
    transactions = lake.read_table("silver", "transaction")
    index = load_index(lake)
    if index is None:
        raise FileNotFoundError(lake.path("bronze", INDEX_CSV))
    usable = transactions.loc[~transactions["is_outlier"].astype(bool)]
    comparables = index_prices(usable, index, as_of=as_of, region=region).reset_index(drop=True)
    weights = recency_weights(comparables["sale_date"], as_of)
    model = HedonicModel(kind=kind).fit(comparables, weights)
    version = f"{kind}-{as_of:%Y%m%d}-n{len(comparables)}"
    save_model(lake, "hedonic", model)
    lake.write_table("gold", "comparable", comparables)

    meta = {
        "version": version,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "n_comparables": len(comparables),
        "trusted": len(comparables) >= MIN_COMPARABLES,
        "backtest": None,
    }
    try:
        predictions = rolling_backtest(
            transactions, index, region=region, months=backtest_months, kind=kind
        )
        meta["backtest"] = summarise(predictions).to_dict()
    except ValueError as exc:  # too little history for any backtest month
        meta["backtest"] = {"skipped": str(exc)}
    lake.write_json("gold", "models/hedonic.json", meta)
    return meta


def matched_with_estimates(lake: Lake) -> pd.DataFrame:
    """Sold episodes joined to the last score made before the sale (no leakage)."""
    listings = _read(lake, "silver", "listing")
    versions = _read(lake, "silver", "listing_version")
    transactions = _read(lake, "silver", "transaction")
    if listings is None or versions is None or transactions is None:
        return pd.DataFrame()
    matched = match_sales(episodes(listings, versions), transactions)
    if matched.empty:
        return matched
    try:
        scores = lake.read_tables("gold", "listing_score")
    except FileNotFoundError:
        scores = pd.DataFrame(columns=["funda_id", "scored_at"])
    scores = scores.assign(scored_at=pd.to_datetime(scores["scored_at"], utc=True))
    rows = []
    for episode in matched.to_dict("records"):
        ids = episode["funda_ids"].split(",")
        cutoff = pd.Timestamp(episode["sold_from"]).tz_localize("UTC")
        before = scores.loc[scores["funda_id"].isin(ids) & (scores["scored_at"] < cutoff)]
        last = before.sort_values("scored_at").iloc[-1] if len(before) else {}
        rows.append(
            episode
            | {
                "vraagprijs": episode["last_ask"],
                "hedonic_q50": last.get("hedonic_q50", np.nan),
                "mu_hedonic": last.get("mu_hedonic", np.nan),
                "mu_ask": last.get("mu_ask", np.nan),
            }
        )
    return pd.DataFrame(rows)


def train_feedback(lake: Lake) -> dict:
    """Refit the ask model and combiner from matched watchlist sales (cheap; runs nightly)."""
    matched = matched_with_estimates(lake)
    ask = AskModel().fit(matched if len(matched) else pd.DataFrame(columns=["woningtype", "gap"]))
    combiner = Combiner().fit(matched) if len(matched) else Combiner()
    save_model(lake, "ask", ask)
    save_model(lake, "combiner", combiner)
    if len(matched):
        lake.write_table("gold", "matched_listing", matched)
    return {"n_matched": len(matched), "stacker": combiner.stacker is not None}
