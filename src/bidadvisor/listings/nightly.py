"""Nightly Funda step: one detail call per watchlisted listing, and any error stops Funda.

A stop writes ``bronze/funda/STOP.json`` and returns a non-zero status. Later runs skip
Funda entirely until someone reads the marker and removes it (``bidadvisor funda-resume``).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import pandas as pd

from bidadvisor.bag.match import Lookup, match_addresses
from bidadvisor.listings.source import ListingGone, ListingSnapshot, ListingSource
from bidadvisor.listings.watchlist import MAX_ACTIVE, Watchlist
from bidadvisor.storage.lake import Lake
from bidadvisor.transform.listings import merge_versions, upsert_listings

STOP_MARKER = "funda/STOP.json"
REQUEST_INTERVAL_S = 10.0


@dataclass
class RunResult:
    status: Literal["ok", "skipped", "stopped"]
    fetched: int = 0
    gone: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def exit_code(self) -> int:
        return 1 if self.status == "stopped" else 0


def _stderr_alert(message: str) -> None:
    print(f"ALERT: {message}", file=sys.stderr)


def _read(lake: Lake, layer: str, name: str) -> pd.DataFrame | None:
    try:
        return lake.read_table(layer, name)
    except FileNotFoundError:
        return None


def stop_marker(lake: Lake) -> dict | None:
    return lake.read_json("bronze", STOP_MARKER) if lake.exists("bronze", STOP_MARKER) else None


def run_funda_step(
    lake: Lake,
    open_source: Callable[[], ListingSource],
    *,
    lookup: Lookup | None = None,
    sleep: Callable[[float], None] = time.sleep,
    alert: Callable[[str], None] = _stderr_alert,
    interval: float = REQUEST_INTERVAL_S,
) -> RunResult:
    if (marker := stop_marker(lake)) is not None:
        alert(f"Funda skipped: stop marker from {marker['stopped_at']} ({marker['error']})")
        return RunResult("skipped", error=marker["error"])

    entries = Watchlist(lake).active()[:MAX_ACTIVE]
    snapshots: list[ListingSnapshot] = []
    result = RunResult("ok")
    source = open_source()
    try:
        for position, entry in enumerate(entries):
            if position:
                sleep(interval)
            try:
                snapshots.append(source.fetch(entry.funda_id))
            except ListingGone:
                result.gone.append(entry.funda_id)
            except Exception as exc:  # any other failure stops Funda until a human looks
                result.status, result.error = "stopped", f"{type(exc).__name__}: {exc}"
                lake.write_json(
                    "bronze",
                    STOP_MARKER,
                    {
                        "stopped_at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "funda_id": entry.funda_id,
                        "error": result.error,
                        "source_version": getattr(source, "version", None),
                    },
                )
                alert(f"Funda stopped on listing {entry.funda_id}: {result.error}")
                break
    finally:
        source.close()

    result.fetched = len(snapshots)
    if result.gone:
        alert(f"Listings no longer on Funda: {', '.join(result.gone)}")
    if snapshots:
        persist(lake, pd.DataFrame([s.to_row() for s in snapshots]), lookup)
    return result


def persist(lake: Lake, snapshots: pd.DataFrame, lookup: Lookup | None = None) -> None:
    """Bronze raw payloads (append-only, one file per day), then silver SCD2 and listing."""
    day = pd.Timestamp(snapshots["fetched_at"].max()).strftime("%Y-%m-%d")
    raw = snapshots[["fetched_at", "funda_id", "source_version", "payload"]].rename(
        columns={"source_version": "pyfunda_version"}
    )
    previous = _read(lake, "bronze", f"funda_listing_raw/{day}")
    if previous is not None:
        raw = pd.concat([previous, raw], ignore_index=True)
    lake.write_table("bronze", f"funda_listing_raw/{day}", raw)

    versions = merge_versions(_read(lake, "silver", "listing_version"), snapshots)
    lake.write_table("silver", "listing_version", versions)

    listings = upsert_listings(_read(lake, "silver", "listing"), snapshots)
    if lookup is not None:
        listings = attach_bag_ids(lake, listings, lookup)
    lake.write_table("silver", "listing", listings)


def attach_bag_ids(lake: Lake, listings: pd.DataFrame, lookup: Lookup) -> pd.DataFrame:
    """Resolve listings still without a BAG ID; misses go to ``silver/listing_quarantine``."""
    todo = listings.loc[listings["bag_vbo_id"].isna() & listings["pc6"].notna()]
    if todo.empty:
        return listings
    matched, quarantine = match_addresses(
        todo[["funda_id", "pc6", "huisnummer", "toevoeging", "lat", "lon"]], lookup
    )
    if not quarantine.empty:
        lake.write_table("silver", "listing_quarantine", quarantine)
    if matched.empty:
        return listings
    ids = matched.set_index("funda_id")["bag_vbo_id"]
    out = listings.copy()
    out["bag_vbo_id"] = out["bag_vbo_id"].fillna(out["funda_id"].map(ids))
    return out
