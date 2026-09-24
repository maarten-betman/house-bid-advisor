"""The ``ListingSource`` boundary: the only place that knows about pyfunda.

The rest of the app sees ``ListingSnapshot`` rows. Removing Funda means removing
this adapter; nothing else imports pyfunda.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

FUNDA_ID = re.compile(r"(?<!\d)(\d{7,9})(?!\d)")

WONINGTYPES = (
    ("tussenwoning", "tussenwoning"),
    ("hoekwoning", "hoekwoning"),
    ("eindwoning", "hoekwoning"),
    ("onder-1-kap", "2-onder-1-kap"),
    ("onder-één-kap", "2-onder-1-kap"),
    ("onder-een-kap", "2-onder-1-kap"),
    ("vrijstaand", "vrijstaand"),
)


def funda_id_from(value: str | int) -> str:
    """The listing ID from a Funda URL or a bare ID; the last 7–9 digit run in the URL path."""
    text = str(value).strip()
    path = text.split("?", 1)[0].split("#", 1)[0]
    matches = FUNDA_ID.findall(path)
    if not matches:
        raise ValueError(f"No Funda listing ID in {text!r}")
    return matches[-1]


@dataclass(frozen=True)
class ListingSnapshot:
    """One fetch of one listing, flattened to what silver needs; ``payload`` keeps the rest."""

    funda_id: str
    fetched_at: datetime
    source_version: str
    url: str | None
    status: str | None
    status_raw: str | None
    vraagprijs: int | None
    prijs_type: str | None
    energielabel: str | None
    woningtype: str | None
    construction: str | None
    pc6: str | None
    huisnummer: int | None
    toevoeging: str | None
    lat: float | None
    lon: float | None
    gebruiksoppervlakte_m2: int | None
    perceel_m2: int | None
    bouwjaar: int | None
    makelaar: str | None
    kenmerken_hash: str
    payload: str

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


class ListingGone(LookupError):
    """The listing no longer exists on Funda (a clean 404) — not a block, so not a stop."""


class ListingSource(Protocol):
    version: str

    def fetch(self, funda_id: str) -> ListingSnapshot: ...

    def close(self) -> None: ...


def woningtype(*descriptions: str | None, object_type: str | None = None) -> str | None:
    """Map Funda's type wording ('Eengezinswoning, tussenwoning') to the model's five types."""
    if object_type == "apartment":
        return "appartement"
    text = " ".join(d for d in descriptions if d).casefold()
    for needle, label in WONINGTYPES:
        if needle in text:
            return label
    return "appartement" if "appartement" in text else None


def _house_number(value: str | None) -> int | None:
    match = re.match(r"\d+", value or "")
    return int(match.group()) if match else None


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def snapshot_from_listing(
    listing: Any, funda_id: str, fetched_at: datetime, source_version: str
) -> ListingSnapshot:
    """Flatten a pyfunda ``Listing``. Pinned to pyfunda 3.1.x; the contract test guards it."""
    details = listing.property_details
    price = listing.price
    address = listing.address
    prijs_type = " | ".join(p for p in (price.price_type, price.condition) if p) or None
    characteristics = [section.to_dict() for section in listing.characteristics]
    return ListingSnapshot(
        funda_id=funda_id,
        fetched_at=fetched_at,
        source_version=source_version,
        url=listing.urls.full,
        status=details.status,
        status_raw=details.raw_status,
        vraagprijs=price.amount,
        prijs_type=prijs_type,
        energielabel=details.energy_label,
        woningtype=woningtype(details.house_type, object_type=details.object_type),
        construction=details.construction_type,
        pc6=(address.postcode or "").replace(" ", "").upper() or None,
        huisnummer=_house_number(address.house_number),
        toevoeging=address.house_number_suffix,
        lat=listing.location.latitude,
        lon=listing.location.longitude,
        gebruiksoppervlakte_m2=listing.areas.living,
        perceel_m2=listing.areas.plot,
        bouwjaar=details.construction_year,
        makelaar=listing.broker.name if listing.broker else None,
        kenmerken_hash=_hash(characteristics),
        payload=json.dumps(listing.raw, default=str, ensure_ascii=False),
    )


class PyFundaSource:
    """One detail call per ``fetch``, no retries, no fingerprint rotation.

    ``max_retries=0`` makes pyfunda raise on the first failure instead of retrying
    with the next TLS fingerprint, so a block or rate limit stops the run. pyfunda
    still presents one browser-like fingerprint on the first request; that part of
    the terms breach is accepted in the spec's risk table.
    """

    def __init__(self, min_request_interval: float = 10.0, timeout: int = 30):
        from funda import Funda, __version__

        self.version = f"pyfunda {__version__}"
        self._client = Funda(
            timeout=timeout, max_retries=0, min_request_interval=min_request_interval
        )

    def fetch(self, funda_id: str) -> ListingSnapshot:
        from funda import ListingNotFound

        try:
            listing = self._client.listing(funda_id)
        except ListingNotFound as exc:
            raise ListingGone(funda_id) from exc
        return snapshot_from_listing(listing, funda_id, datetime.now(UTC), self.version)

    def close(self) -> None:
        self._client.close()
