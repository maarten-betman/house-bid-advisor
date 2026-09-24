from datetime import UTC, datetime

import pytest

from bidadvisor.bag.match import BagCandidate
from bidadvisor.listings.nightly import STOP_MARKER, run_funda_step
from bidadvisor.listings.source import ListingGone, ListingSnapshot
from bidadvisor.listings.watchlist import MAX_ACTIVE, Watchlist, WatchlistFull
from bidadvisor.storage.lake import Lake


def url(n: int) -> str:
    return f"https://www.funda.nl/detail/koop/utrecht/huis-{n}/{43000000 + n}/"


def test_watchlist_add_dedupe_remove_reactivate_and_persist(tmp_path):
    lake = Lake(tmp_path)
    wl = Watchlist(lake)
    wl.add(url(1))
    wl.add(url(1))
    wl.add(url(2))
    assert wl.remove("43000001")
    wl.save()
    reloaded = Watchlist(lake)
    assert [e.funda_id for e in reloaded.active()] == ["43000002"]
    reloaded.add(url(1))
    assert len(reloaded.active()) == 2


def test_watchlist_caps_active_listings(tmp_path):
    wl = Watchlist(Lake(tmp_path))
    for n in range(MAX_ACTIVE):
        wl.add(url(n))
    with pytest.raises(WatchlistFull):
        wl.add(url(99))


def snapshot(funda_id: str, price=450_000) -> ListingSnapshot:
    return ListingSnapshot(
        funda_id=funda_id,
        fetched_at=datetime(2026, 9, 24, 4, tzinfo=UTC),
        source_version="fake",
        url=f"u/{funda_id}",
        status="available",
        status_raw="Beschikbaar",
        vraagprijs=price,
        prijs_type="k.k.",
        energielabel="C",
        woningtype="tussenwoning",
        construction="existing",
        pc6="3511AB",
        huisnummer=12,
        toevoeging="",
        lat=52.0,
        lon=5.0,
        gebruiksoppervlakte_m2=120,
        perceel_m2=140,
        bouwjaar=1965,
        makelaar="M",
        kenmerken_hash="h",
        payload="{}",
    )


class FakeSource:
    version = "fake"

    def __init__(self, fail_on=(), gone=()):
        self.calls, self.fail_on, self.gone, self.closed = [], set(fail_on), set(gone), False

    def fetch(self, funda_id):
        self.calls.append(funda_id)
        if funda_id in self.gone:
            raise ListingGone(funda_id)
        if funda_id in self.fail_on:
            raise RuntimeError("403 Forbidden")
        return snapshot(funda_id)

    def close(self):
        self.closed = True


@pytest.fixture
def lake(tmp_path):
    lake = Lake(tmp_path)
    wl = Watchlist(lake)
    for n in (1, 2, 3):
        wl.add(url(n))
    wl.save()
    return lake


def test_run_fetches_paced_and_writes_bronze_and_silver(lake):
    source, sleeps = FakeSource(), []
    lookup = lambda pc6, nr: [BagCandidate("vbo-1", pc6, nr, "", "", None, 52.0, 5.0)]  # noqa: E731
    result = run_funda_step(lake, lambda: source, lookup=lookup, sleep=sleeps.append)
    assert (result.status, result.fetched, result.exit_code) == ("ok", 3, 0)
    assert sleeps == [10.0, 10.0]
    assert source.closed
    raw = lake.read_tables("bronze", "funda_listing_raw")
    assert set(raw.columns) == {"fetched_at", "funda_id", "pyfunda_version", "payload"}
    assert len(lake.read_table("silver", "listing_version")) == 3
    assert lake.read_table("silver", "listing")["bag_vbo_id"].tolist() == ["vbo-1"] * 3


def test_error_trips_stop_marker_and_later_runs_skip(lake):
    alerts = []
    source = FakeSource(fail_on={"43000002"})
    result = run_funda_step(lake, lambda: source, sleep=lambda s: None, alert=alerts.append)
    assert (result.status, result.exit_code, result.fetched) == ("stopped", 1, 1)
    assert source.calls == ["43000001", "43000002"]  # nothing after the failure
    marker = lake.read_json("bronze", STOP_MARKER)
    assert marker["funda_id"] == "43000002" and "403" in marker["error"]
    assert len(lake.read_table("silver", "listing_version")) == 1  # what was fetched is kept

    untouched = FakeSource()
    again = run_funda_step(lake, lambda: untouched, sleep=lambda s: None, alert=alerts.append)
    assert (again.status, again.exit_code, untouched.calls) == ("skipped", 0, [])
    assert len(alerts) == 2

    lake.delete("bronze", STOP_MARKER)
    resumed = run_funda_step(lake, FakeSource, sleep=lambda s: None, alert=alerts.append)
    assert resumed.status == "ok"


def test_gone_listing_is_reported_not_a_stop(lake):
    alerts = []
    result = run_funda_step(
        lake, lambda: FakeSource(gone={"43000003"}), sleep=lambda s: None, alert=alerts.append
    )
    assert (result.status, result.fetched, result.gone) == ("ok", 2, ["43000003"])
    assert not lake.exists("bronze", STOP_MARKER)
    assert "43000003" in alerts[0]


def test_second_run_same_day_appends_raw_but_not_versions(lake):
    for _ in range(2):
        run_funda_step(lake, FakeSource, sleep=lambda s: None)
    assert len(lake.read_tables("bronze", "funda_listing_raw")) == 6
    assert len(lake.read_table("silver", "listing_version")) == 3


def test_cli_simulate_error_trips_marker_and_resume_clears(lake, capsys):
    from bidadvisor.cli import main

    assert main(["--lake", str(lake.root), "funda-run", "--simulate-error", "--no-bag"]) == 1
    assert lake.exists("bronze", STOP_MARKER)
    assert main(["--lake", str(lake.root), "funda-resume"]) == 0
    assert not lake.exists("bronze", STOP_MARKER)
