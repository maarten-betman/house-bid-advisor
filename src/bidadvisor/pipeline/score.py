"""Daily scoring: one ``serving/scores/{key}.json`` per watchlisted listing, plus gold history.

``key`` is the listing's bag_vbo_id, or ``funda-<id>`` while its address has no BAG match.
Each component is optional: with no Kadaster model yet a listing is still scored from
its asking price, and the payload says so in ``warnings``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from bidadvisor.listings.watchlist import Watchlist
from bidadvisor.model.ask import AskModel
from bidadvisor.model.backtest import MIN_COMPARABLES
from bidadvisor.model.combine import Combiner, lognormal_from_quantiles, quantile_names
from bidadvisor.model.comparables import taxatie_proxy
from bidadvisor.pipeline.train import BAG_ATTRIBUTES, load_model
from bidadvisor.storage.lake import Lake

HEDONIC_REQUIRED = ["gebruiksoppervlakte_m2", "bouwjaar", "lat", "lon", "pc6"]
BAG_COLUMNS = ["gebruiksoppervlakte_m2", "bouwjaar", "lat", "lon"]


def score_key(bag_vbo_id, funda_id: str) -> str:
    return str(bag_vbo_id) if isinstance(bag_vbo_id, str) and bag_vbo_id else f"funda-{funda_id}"


def listing_state(listings: pd.DataFrame, versions: pd.DataFrame, as_of: pd.Timestamp):
    """Per listing: the version valid on ``as_of``, days on market and price cuts so far."""
    known = versions.loc[pd.to_datetime(versions["valid_from"]) <= as_of].sort_values(
        ["funda_id", "valid_from"]
    )
    current = known.groupby("funda_id").tail(1).set_index("funda_id")
    cuts = known.groupby("funda_id")["vraagprijs"].agg(lambda s: int((s.diff() < 0).sum()))
    state = listings.set_index("funda_id").join(
        current[["status", "status_raw", "vraagprijs", "prijs_type", "energielabel"]]
    )
    state["n_price_cuts"] = cuts.reindex(state.index).fillna(0).astype(int)
    state["days_on_market"] = (as_of - pd.to_datetime(state["first_seen"])).dt.days
    return state


def _with_bag_attributes(row: pd.Series, attributes: pd.DataFrame | None, warnings: list):
    if attributes is not None and isinstance(row.get("bag_vbo_id"), str):
        match = attributes.loc[attributes["bag_vbo_id"] == row["bag_vbo_id"]]
        if len(match):
            for column in BAG_COLUMNS:
                row[column] = match.iloc[0][column]
            return row
    warnings.append("Funda floor area used; the model was trained on BAG gebruiksoppervlakte")
    return row


def score_listing(row, hedonic, hedonic_meta, comparables, ask, combiner) -> dict:
    warnings: list[str] = []
    frame = row.to_frame().T.reset_index(drop=True)
    result: dict = {"hedonic": None, "ask": None, "taxatie": None}

    mu_h = sd_h = None
    missing = [c for c in HEDONIC_REQUIRED if pd.isna(row.get(c))]
    if hedonic is None:
        warnings.append("No hedonic model yet: estimate rests on the asking price alone")
    elif missing:
        warnings.append(f"Hedonic estimate skipped, missing {', '.join(missing)}")
    else:
        quantiles = hedonic.predict_quantiles(frame)
        mu, sd = lognormal_from_quantiles(quantiles)
        mu_h, sd_h = float(mu.iloc[0]), float(sd.iloc[0])
        result["hedonic"] = {k: float(v) for k, v in quantiles.iloc[0].items()}
        if not hedonic_meta.get("trusted", False):
            warnings.append(
                f"Only {hedonic_meta.get('n_comparables')} comparables (< {MIN_COMPARABLES}): "
                "intervals not trusted; widen the region"
            )
    if comparables is not None and not missing:
        result["taxatie"] = taxatie_proxy(row, comparables)

    mu_a = sd_a = None
    if pd.isna(row.get("vraagprijs")):
        warnings.append("No asking price (e.g. 'prijs op aanvraag'): ask-anchored estimate skipped")
    else:
        if result["hedonic"]:
            frame["hedonic_q50"] = result["hedonic"]["q50"]
        estimate = ask.predict(frame).iloc[0]
        mu_a, sd_a = float(estimate["mu_log"]), float(estimate["sd_log"])
        result["ask"] = {"gap_mean": float(estimate["gap_mean"])}

    if (
        row.get("status") in ("sold", "negotiations")
        or "verkocht" in str(row.get("status_raw") or "").casefold()
    ):
        warnings.append(f"Listing status is {row.get('status_raw') or row.get('status')}")
    if mu_h is None and mu_a is None:
        return result | {"final_price": None, "weights": None, "warnings": warnings}

    final, weights = combiner.combine(mu_h, sd_h, mu_a, sd_a)
    return result | {
        "final_price": final,
        "weights": weights,
        "warnings": warnings,
        "_mu": {"mu_hedonic": mu_h, "sd_hedonic": sd_h, "mu_ask": mu_a, "sd_ask": sd_a},
    }


def score_watchlist(lake: Lake, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    as_of = pd.Timestamp(now).tz_convert(None).normalize()
    try:
        listings = lake.read_table("silver", "listing")
        versions = lake.read_table("silver", "listing_version")
    except FileNotFoundError:
        return []
    state = listing_state(listings, versions, as_of)
    hedonic = load_model(lake, "hedonic")
    meta = lake.read_json("gold", "models/hedonic.json") if hedonic is not None else {}
    ask = load_model(lake, "ask") or AskModel()
    combiner = load_model(lake, "combiner") or Combiner()
    comparables = _read_optional(lake, "gold", "comparable")
    attributes_path = lake.path("bronze", BAG_ATTRIBUTES)
    attributes = pd.read_parquet(attributes_path) if attributes_path.exists() else None

    payloads, history = [], []
    for entry in Watchlist(lake).active():
        if entry.funda_id not in state.index:
            continue  # added since the last Funda fetch
        warnings: list[str] = []
        row = _with_bag_attributes(state.loc[entry.funda_id].copy(), attributes, warnings)
        row["funda_id"] = entry.funda_id
        scored = score_listing(row, hedonic, meta, comparables, ask, combiner)
        key = score_key(row.get("bag_vbo_id"), entry.funda_id)
        if key.startswith("funda-"):
            warnings.append("Address not matched to BAG yet; scored under its Funda ID")
        mu = scored.pop("_mu", {})
        scored = _rounded(scored)
        payload = {
            "score_key": key,
            "bag_vbo_id": row.get("bag_vbo_id") if not key.startswith("funda-") else None,
            "funda_id": entry.funda_id,
            "scored_at": now.isoformat(timespec="seconds"),
            "model_version": {
                "hedonic": meta.get("version"),
                "ask": f"nvm-q2-2026+{ask.n_matched}-matched",
                "combiner": "stacker" if combiner.stacker is not None else "fixed-weights",
            },
            "listing": {
                "url": row.get("url"),
                "status": row.get("status_raw") or row.get("status"),
                "vraagprijs": _num(row.get("vraagprijs")),
                "prijs_type": row.get("prijs_type"),
                "woningtype": row.get("woningtype"),
                "gebruiksoppervlakte_m2": _num(row.get("gebruiksoppervlakte_m2")),
                "days_on_market": _num(row.get("days_on_market")),
                "n_price_cuts": _num(row.get("n_price_cuts")),
            },
            **scored,
            "warnings": warnings + scored["warnings"],
        }
        lake.write_json("serving", f"scores/{key}.json", payload)
        payloads.append(payload)
        history.append(_history_row(payload, mu))

    if history:
        lake.write_table("gold", f"listing_score/{as_of:%Y-%m-%d}", pd.DataFrame(history))
    return payloads


def _num(value):
    if value is None or pd.isna(value):
        return None
    return float(value) if isinstance(value, float | np.floating) else int(value)


def _read_optional(lake: Lake, layer: str, name: str) -> pd.DataFrame | None:
    try:
        return lake.read_table(layer, name)
    except FileNotFoundError:
        return None


def _history_row(payload: dict, mu: dict) -> dict:
    final = payload["final_price"] or {}
    taxatie = payload["taxatie"] or {}
    return {
        "funda_id": payload["funda_id"],
        "score_key": payload["score_key"],
        "scored_at": payload["scored_at"],
        "model_version": payload["model_version"]["hedonic"],
        **{name: final.get(name) for name in quantile_names()},
        "hedonic_q50": (payload["hedonic"] or {}).get("q50"),
        "mu_hedonic": mu.get("mu_hedonic"),
        "mu_ask": mu.get("mu_ask"),
        "taxatie_p10": taxatie.get("taxatie_p10"),
        "taxatie_p50": taxatie.get("taxatie_p50"),
        "taxatie_p90": taxatie.get("taxatie_p90"),
        "n_comparables": taxatie.get("n_comparables"),
        "warnings": " | ".join(payload["warnings"]),
    }


def _rounded(scored: dict) -> dict:
    """Whole euros in the served JSON; the gap and weights keep their precision."""
    out = dict(scored)
    for name in ("final_price", "hedonic"):
        if out.get(name):
            out[name] = {k: round(v) for k, v in out[name].items()}
    if out.get("taxatie"):
        out["taxatie"] = {
            k: (round(v) if k.startswith("taxatie_") else v) for k, v in out["taxatie"].items()
        }
    if out.get("ask"):
        out["ask"] = {"gap_mean": round(out["ask"]["gap_mean"], 4)}
    if out.get("weights"):
        out["weights"] = {k: round(v, 3) for k, v in out["weights"].items()}
    return out
