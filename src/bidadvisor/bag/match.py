"""Resolve addresses to a BAG verblijfsobject ID (``bag_vbo_id``).

Rules from the spec: exact postcode and huisnummer only, toevoeging normalised,
coordinates as tie-breaker for Funda addresses, misses to quarantine.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pandas as pd

LOCATIESERVER = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
FLOOR_WORDS = re.compile(r"(HOOG|HG|H|VERD|ETAGE|ET)$")


def normalise_toevoeging(value: str | None) -> str:
    """Map 'A', 'a', '-1', '1-hoog', ' 1 hg ' to a comparable key ('A', 'A', '1', '1', '1')."""
    if not value:
        return ""
    key = re.sub(r"[^A-Z0-9]", "", value.upper())
    if re.fullmatch(r"\d+[A-Z]+", key):
        key = FLOOR_WORDS.sub("", key)
    return key


@dataclass(frozen=True)
class BagCandidate:
    bag_vbo_id: str
    pc6: str
    huisnummer: int
    huisletter: str
    toevoeging: str
    buurt_code: str | None
    lat: float | None
    lon: float | None

    @property
    def key(self) -> str:
        return normalise_toevoeging(f"{self.huisletter}{self.toevoeging}")


Lookup = Callable[[str, int], list[BagCandidate]]


def _point(wkt: str | None) -> tuple[float | None, float | None]:
    if not wkt:
        return None, None
    lon, lat = (float(v) for v in wkt.removeprefix("POINT(").removesuffix(")").split())
    return lat, lon


def pdok_lookup(client: httpx.Client | None = None) -> Lookup:
    """Return a lookup that asks PDOK for every address object on a postcode + huisnummer."""
    http = client or httpx.Client(timeout=10)

    def lookup(pc6: str, huisnummer: int) -> list[BagCandidate]:
        response = http.get(
            LOCATIESERVER,
            params={
                "q": f"{pc6} {huisnummer}",
                "fq": ["type:adres", f"postcode:{pc6}", f"huisnummer:{huisnummer}"],
                "fl": "adresseerbaarobject_id postcode huisnummer huisletter "
                "huisnummertoevoeging buurtcode centroide_ll",
                "rows": 50,
            },
        )
        response.raise_for_status()
        candidates = []
        for doc in response.json()["response"]["docs"]:
            lat, lon = _point(doc.get("centroide_ll"))
            candidates.append(
                BagCandidate(
                    bag_vbo_id=doc["adresseerbaarobject_id"],
                    pc6=doc["postcode"],
                    huisnummer=int(doc["huisnummer"]),
                    huisletter=doc.get("huisletter", ""),
                    toevoeging=doc.get("huisnummertoevoeging", ""),
                    buurt_code=doc.get("buurtcode"),
                    lat=lat,
                    lon=lon,
                )
            )
        return candidates

    return lookup


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def resolve(
    candidates: list[BagCandidate],
    pc6: str,
    huisnummer: int,
    toevoeging: str | None,
    near: tuple[float, float] | None = None,
) -> tuple[BagCandidate | None, str]:
    """Pick one candidate or explain why none; the reason goes to quarantine."""
    exact = [c for c in candidates if c.pc6 == pc6 and c.huisnummer == huisnummer]
    if not exact:
        return None, "no_address"
    key = normalise_toevoeging(toevoeging)
    same_key = [c for c in exact if c.key == key]
    if len(same_key) == 1:
        return same_key[0], "ok"
    pool = same_key or exact
    if near is not None and all(c.lat is not None for c in pool):
        best = min(pool, key=lambda c: _distance_m(near[0], near[1], c.lat, c.lon))
        return best, "ok_nearest"
    if not same_key:
        return None, "toevoeging_mismatch"
    return None, "ambiguous"


def match_addresses(rows: pd.DataFrame, lookup: Lookup) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add BAG columns to rows with pc6/huisnummer/toevoeging; return (matched, quarantine).

    Optional ``lat``/``lon`` columns act as tie-breaker (Funda addresses).
    """
    cache: dict[tuple[str, int], list[BagCandidate]] = {}
    matched, quarantine = [], []
    for row in rows.to_dict("records"):
        address = (row["pc6"], int(row["huisnummer"]))
        if address not in cache:
            cache[address] = lookup(*address)
        near = (row["lat"], row["lon"]) if pd.notna(row.get("lat")) else None
        hit, reason = resolve(cache[address], *address, row.get("toevoeging"), near)
        if hit is None:
            quarantine.append({**row, "reason": reason})
            continue
        matched.append(
            {
                **row,
                "bag_vbo_id": hit.bag_vbo_id,
                "buurt_code": hit.buurt_code,
                "bag_lat": hit.lat,
                "bag_lon": hit.lon,
            }
        )
    return pd.DataFrame(matched), pd.DataFrame(quarantine)


def match_rate(matched: pd.DataFrame, quarantine: pd.DataFrame) -> float:
    total = len(matched) + len(quarantine)
    return len(matched) / total if total else 1.0
