"""Silver ``transaction`` and gold ``comparable`` rules: portfolio deals, outliers, indexing."""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

# CBS publishes a month's index a few weeks after it ends; used when no publication date is given.
DEFAULT_PUBLICATION_LAG = pd.Timedelta(days=22)


def txn_id(bag_vbo_id: str, sale_date, koopsom: int) -> str:
    raw = f"{bag_vbo_id}|{pd.Timestamp(sale_date).date()}|{koopsom}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def portfolio_deals(frame: pd.DataFrame) -> pd.Series:
    """True where the same koopsom and date appear on several BAG IDs (one deed, several homes)."""
    ids_per_deed = frame.groupby(["sale_date", "koopsom"])["bag_vbo_id"].transform("nunique")
    return ids_per_deed > 1


def outliers(frame: pd.DataFrame, low: float = 0.5, high: float = 2.0) -> pd.Series:
    """True where price per m² is under ``low`` or over ``high`` × the PC4 × type × year median."""
    per_m2 = frame["koopsom"] / frame["gebruiksoppervlakte_m2"]
    group = [
        frame["pc6"].str[:4],
        frame.get("woningtype", pd.Series("all", index=frame.index)).fillna("all"),
        pd.to_datetime(frame["sale_date"]).dt.year,
    ]
    median = per_m2.groupby(group).transform("median")
    ratio = per_m2 / median
    return (ratio < low) | (ratio > high) | per_m2.isna()


def build_transactions(sales: pd.DataFrame) -> pd.DataFrame:
    """BAG-matched Kadaster rows joined to BAG attributes → silver ``transaction``.

    Portfolio deals are dropped; outliers stay, flagged, for audit.
    """
    frame = sales.copy()
    frame["sale_date"] = pd.to_datetime(frame["sale_date"])
    frame = frame.drop_duplicates(["bag_vbo_id", "sale_date", "koopsom"])
    frame = frame.loc[~portfolio_deals(frame)].copy()
    frame["txn_id"] = [
        txn_id(b, d, k)
        for b, d, k in zip(frame["bag_vbo_id"], frame["sale_date"], frame["koopsom"], strict=True)
    ]
    frame["is_outlier"] = outliers(frame)
    return frame.reset_index(drop=True)


def published_index(index: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Index rows (region, month, value[, published_on]) that were public on ``as_of``."""
    frame = index.copy()
    frame["month"] = pd.to_datetime(frame["month"]).dt.to_period("M")
    if "published_on" in frame:
        published = pd.to_datetime(frame["published_on"])
    else:
        published = frame["month"].dt.end_time.dt.normalize() + DEFAULT_PUBLICATION_LAG
    return frame.loc[published <= as_of]


def index_prices(
    transactions: pd.DataFrame, index: pd.DataFrame, as_of: pd.Timestamp, region: str
) -> pd.DataFrame:
    """koopsom_indexed = koopsom × index(latest published month) / index(sale month).

    Sales after ``as_of`` and sales whose month has no published index yet are left out.
    """
    known = published_index(index.loc[index["region"] == region], as_of)
    if known.empty:
        raise ValueError(f"No index values for {region!r} published by {as_of.date()}")
    by_month = known.set_index("month")["value"].sort_index()
    latest_month = by_month.index.max()

    frame = transactions.loc[pd.to_datetime(transactions["sale_date"]) <= as_of].copy()
    frame["index_month"] = pd.to_datetime(frame["sale_date"]).dt.to_period("M")
    sale_index = frame["index_month"].map(by_month)
    frame = frame.loc[sale_index.notna()].copy()
    frame["koopsom_indexed"] = (
        frame["koopsom"] * by_month[latest_month] / sale_index.loc[frame.index]
    ).round()
    frame["index_to"] = latest_month
    return frame


def recency_weights(sale_dates: pd.Series, as_of: pd.Timestamp, half_life_months=12.0):
    age_months = (as_of - pd.to_datetime(sale_dates)).dt.days / 30.4375
    return np.power(0.5, age_months.clip(lower=0) / half_life_months).to_numpy()
