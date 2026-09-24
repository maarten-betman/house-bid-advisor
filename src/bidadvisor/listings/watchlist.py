"""The watchlist: the only listings the app ever fetches, at most ``MAX_ACTIVE`` at a time."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from bidadvisor.listings.source import funda_id_from
from bidadvisor.storage.lake import Lake

MAX_ACTIVE = 50
WATCHLIST = "watchlist.json"


class WatchlistFull(ValueError):
    pass


@dataclass
class Entry:
    funda_id: str
    url: str
    added_at: str
    removed_at: str | None = None

    @property
    def active(self) -> bool:
        return self.removed_at is None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Watchlist:
    def __init__(self, lake: Lake):
        self.lake = lake
        payload = (
            lake.read_json("serving", WATCHLIST)
            if lake.exists("serving", WATCHLIST)
            else {"listings": []}
        )
        self.entries = [Entry(**item) for item in payload["listings"]]

    def active(self) -> list[Entry]:
        return [e for e in self.entries if e.active]

    def add(self, url: str) -> Entry:
        """Add a Funda link; re-adding a removed listing reactivates it."""
        funda_id = funda_id_from(url)
        for entry in self.entries:
            if entry.funda_id == funda_id:
                if not entry.active:
                    self._check_room()
                    entry.removed_at = None
                return entry
        self._check_room()
        entry = Entry(funda_id=funda_id, url=url, added_at=_now())
        self.entries.append(entry)
        return entry

    def remove(self, funda_id: str) -> bool:
        for entry in self.entries:
            if entry.funda_id == funda_id and entry.active:
                entry.removed_at = _now()
                return True
        return False

    def save(self) -> None:
        self.lake.write_json("serving", WATCHLIST, {"listings": [asdict(e) for e in self.entries]})

    def _check_room(self) -> None:
        if len(self.active()) >= MAX_ACTIVE:
            raise WatchlistFull(f"Watchlist already holds {MAX_ACTIVE} listings; remove one first")
