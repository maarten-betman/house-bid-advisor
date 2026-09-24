"""Silver ``listing`` (one row per listing) and ``listing_version`` (SCD2, one row per change)."""

from __future__ import annotations

import pandas as pd

TRACKED = ["status", "status_raw", "vraagprijs", "prijs_type", "energielabel", "kenmerken_hash"]
VERSION_COLUMNS = ["funda_id", "valid_from", "valid_to", "is_current", *TRACKED]
LISTING_COLUMNS = [
    "funda_id",
    "bag_vbo_id",
    "url",
    "makelaar",
    "woningtype",
    "construction",
    "first_seen",
    "last_seen",
    "pc6",
    "huisnummer",
    "toevoeging",
    "lat",
    "lon",
    "gebruiksoppervlakte_m2",
    "perceel_m2",
    "bouwjaar",
]


def _same(a, b) -> bool:
    return (pd.isna(a) and pd.isna(b)) or a == b


def merge_versions(existing: pd.DataFrame | None, snapshots: pd.DataFrame) -> pd.DataFrame:
    """Apply today's snapshots to the SCD2 history.

    A changed listing closes its current row (``valid_to`` = snapshot date) and opens a new
    one. A same-day rerun overwrites that day's row instead of adding another. Listings
    not in ``snapshots`` are left alone.
    """
    history = (
        pd.DataFrame(columns=VERSION_COLUMNS) if existing is None else existing.copy()
    ).astype({"is_current": bool})
    new_rows = []
    for snap in snapshots.to_dict("records"):
        day = pd.Timestamp(snap["fetched_at"]).tz_localize(None).normalize()
        current = history.index[(history["funda_id"] == snap["funda_id"]) & history["is_current"]]
        values = {c: snap.get(c) for c in TRACKED}
        if len(current):
            row = current[0]
            if all(_same(history.at[row, c], values[c]) for c in TRACKED):
                continue
            if pd.Timestamp(history.at[row, "valid_from"]) == day:
                for column, value in values.items():
                    history.at[row, column] = value
                continue
            history.at[row, "valid_to"] = day
            history.at[row, "is_current"] = False
        new_rows.append(
            {"funda_id": snap["funda_id"], "valid_from": day, "valid_to": pd.NaT}
            | {"is_current": True}
            | values
        )
    if new_rows:
        history = pd.concat([history, pd.DataFrame(new_rows)], ignore_index=True)
    history["valid_from"] = pd.to_datetime(history["valid_from"])
    history["valid_to"] = pd.to_datetime(history["valid_to"])
    return history[VERSION_COLUMNS].sort_values(["funda_id", "valid_from"], ignore_index=True)


def upsert_listings(existing: pd.DataFrame | None, snapshots: pd.DataFrame) -> pd.DataFrame:
    """Latest non-null attributes per listing; ``first_seen`` and ``bag_vbo_id`` are kept."""
    fresh = snapshots.copy()
    fresh["seen"] = pd.to_datetime(fresh["fetched_at"]).dt.tz_localize(None).dt.normalize()
    fresh = fresh.assign(first_seen=fresh["seen"], last_seen=fresh["seen"])
    fresh = fresh.reindex(columns=LISTING_COLUMNS)
    if existing is None or existing.empty:
        return fresh.sort_values("funda_id", ignore_index=True)
    old = existing.set_index("funda_id")
    new = fresh.set_index("funda_id")
    merged = new.combine_first(old)
    both = old.index.intersection(new.index)
    merged.loc[both, "first_seen"] = old.loc[both, "first_seen"]
    merged.loc[both, "bag_vbo_id"] = old.loc[both, "bag_vbo_id"].combine_first(
        new.loc[both, "bag_vbo_id"]
    )
    return merged.reset_index()[LISTING_COLUMNS].sort_values("funda_id", ignore_index=True)
