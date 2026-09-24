"""Listing episodes and their Kadaster sale: the labelled data the ask model and stacker learn from.

Rules from the spec:
- a new funda_id on the same bag_vbo_id within 12 months continues one episode (relist);
- the sale window opens at the first "verkocht (onder voorbehoud)" snapshot, or at
  first_seen for a listing added after that status, and runs 240 days;
- the nearest Kadaster sale on the same bag_vbo_id inside the window wins.
"""

from __future__ import annotations

import pandas as pd

RELIST_GAP = pd.Timedelta(days=365)
SALE_WINDOW = pd.Timedelta(days=240)


def is_sold_status(status: pd.Series, status_raw: pd.Series) -> pd.Series:
    raw = status_raw.fillna("").str.casefold()
    return status.eq("sold") | raw.str.contains("verkocht", regex=False)


def episodes(listings: pd.DataFrame, versions: pd.DataFrame) -> pd.DataFrame:
    """One row per sales episode with its asks, price cuts and first sold date."""
    frame = listings.dropna(subset=["bag_vbo_id"]).sort_values(["bag_vbo_id", "first_seen"])
    new_episode = (frame["bag_vbo_id"] != frame["bag_vbo_id"].shift()) | (
        frame["first_seen"] - frame["last_seen"].shift() > RELIST_GAP
    )
    frame = frame.assign(episode_id=frame["funda_id"].where(new_episode).ffill())

    versions = versions.merge(frame[["funda_id", "episode_id"]], on="funda_id")
    versions = versions.sort_values(["episode_id", "valid_from"])
    sold = versions.loc[is_sold_status(versions["status"], versions["status_raw"])]
    asks = versions.dropna(subset=["vraagprijs"])
    before_sold = asks.merge(
        sold.groupby("episode_id")["valid_from"].min().rename("sold_from"),
        on="episode_id",
        how="left",
    )
    before_sold = before_sold.loc[
        before_sold["sold_from"].isna() | (before_sold["valid_from"] <= before_sold["sold_from"])
    ]
    cuts = before_sold.groupby("episode_id")["vraagprijs"].agg(lambda s: int((s.diff() < 0).sum()))

    head = frame.groupby("episode_id").agg(
        bag_vbo_id=("bag_vbo_id", "first"),
        start=("first_seen", "min"),
        funda_ids=("funda_id", lambda s: ",".join(s)),
        makelaar=("makelaar", "last"),
        woningtype=("woningtype", "last"),
    )
    head["first_ask"] = before_sold.groupby("episode_id")["vraagprijs"].first()
    head["last_ask"] = before_sold.groupby("episode_id")["vraagprijs"].last()
    head["prijs_type"] = before_sold.groupby("episode_id")["prijs_type"].last()
    head["n_price_cuts"] = cuts
    head["sold_from"] = sold.groupby("episode_id")["valid_from"].min()
    head["n_price_cuts"] = head["n_price_cuts"].fillna(0).astype(int)
    return head.reset_index()


def match_sales(episode_frame: pd.DataFrame, transactions: pd.DataFrame) -> pd.DataFrame:
    """Episodes that sold, with koopsom, sale_date and days on market until sold."""
    sold = episode_frame.dropna(subset=["sold_from"])
    candidates = sold.merge(
        transactions[["bag_vbo_id", "txn_id", "sale_date", "koopsom"]], on="bag_vbo_id"
    )
    window_start = candidates[["sold_from", "start"]].max(axis=1)
    sale_date = pd.to_datetime(candidates["sale_date"])
    inside = (sale_date >= window_start) & (sale_date <= window_start + SALE_WINDOW)
    candidates = candidates.loc[inside].assign(
        distance=(sale_date - window_start).loc[inside].dt.days
    )
    matched = candidates.sort_values("distance").drop_duplicates("episode_id")
    matched = matched.assign(
        days_on_market=(matched["sold_from"] - matched["start"]).dt.days,
        gap=matched["koopsom"] / matched["last_ask"] - 1,
    )
    return matched.drop(columns="distance").sort_values("episode_id", ignore_index=True)
