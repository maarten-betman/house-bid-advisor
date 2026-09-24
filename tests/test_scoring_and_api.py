from datetime import UTC, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from synthetic import market

from bidadvisor.api import create_app
from bidadvisor.bag.match import BagCandidate
from bidadvisor.listings.nightly import persist
from bidadvisor.listings.watchlist import Watchlist
from bidadvisor.pipeline.score import score_watchlist
from bidadvisor.pipeline.train import INDEX_CSV, train_feedback, train_hedonic
from bidadvisor.storage.lake import Lake
from bidadvisor.transform.transactions import build_transactions

NOW = datetime(2026, 2, 3, 4, tzinfo=UTC)


def snapshot_rows(houses: pd.DataFrame, funda_ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "funda_id": funda_ids,
            "fetched_at": NOW,
            "source_version": "fake",
            "url": [f"https://www.funda.nl/detail/koop/x/y/{i}/" for i in funda_ids],
            "status": "available",
            "status_raw": "Beschikbaar",
            "vraagprijs": (houses["koopsom"].to_numpy() * 0.95).round(-3),
            "prijs_type": "vraagprijs | kosten koper",
            "energielabel": "C",
            "woningtype": houses["woningtype"].to_numpy(),
            "construction": "existing",
            "pc6": houses["pc6"].to_numpy(),
            "huisnummer": 1,
            "toevoeging": "",
            "lat": houses["lat"].to_numpy(),
            "lon": houses["lon"].to_numpy(),
            "gebruiksoppervlakte_m2": houses["gebruiksoppervlakte_m2"].to_numpy(),
            "perceel_m2": houses["perceel_m2"].to_numpy(),
            "bouwjaar": houses["bouwjaar"].to_numpy(),
            "makelaar": "M",
            "kenmerken_hash": "h",
            "payload": "{}",
        }
    )


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    lake = Lake(tmp_path_factory.mktemp("lake"))
    frame, index = market()
    lake.write_table("silver", "transaction", build_transactions(frame))
    path = lake.path("bronze", INDEX_CSV)
    path.parent.mkdir(parents=True)
    index.to_csv(path, index=False)
    meta = train_hedonic(lake, "utrecht", pd.Timestamp("2026-02-01"), backtest_months=3)
    assert meta["trusted"] and meta["backtest"]["months"] == 3

    houses = frame.iloc[:2]
    ids = ["43000001", "43000002"]
    wl = Watchlist(lake)
    for i in ids:
        wl.add(f"https://www.funda.nl/detail/koop/x/y/{i}/")
    wl.save()
    bag = dict(zip(ids, houses["bag_vbo_id"], strict=True))
    rows = snapshot_rows(houses, ids)
    lookup_calls = iter(bag.values())

    def lookup(pc6, nr):
        return [BagCandidate(next(lookup_calls), pc6, nr, "", "", None, 52.0, 5.0)]

    persist(lake, rows, lookup)
    train_feedback(lake)
    return lake


def test_every_watchlisted_listing_is_scored(lake):
    payloads = score_watchlist(lake, now=NOW)
    assert len(payloads) == 2
    for p in payloads:
        q = list(p["final_price"].values())
        assert q == sorted(q)
        assert p["hedonic"] and p["ask"] and p["taxatie"]["n_comparables"] == 8
        assert 0 < p["weights"]["hedonic"] < 1
        assert lake.exists("serving", f"scores/{p['score_key']}.json")
        assert p["score_key"] == p["bag_vbo_id"]
    history = lake.read_tables("gold", "listing_score")
    assert len(history) == 2 and history["q50"].notna().all()


def test_listing_without_bag_match_is_scored_under_funda_id(lake, tmp_path):
    bare = Lake(tmp_path)
    wl = Watchlist(bare)
    wl.add("https://www.funda.nl/detail/koop/x/y/43000009/")
    wl.save()
    rows = snapshot_rows(market(n=1)[0], ["43000009"])
    persist(bare, rows)
    (payload,) = score_watchlist(bare, now=NOW)
    assert payload["score_key"] == "funda-43000009"
    assert payload["hedonic"] is None and payload["final_price"]["q50"] > 0
    assert any("asking price alone" in w for w in payload["warnings"])


@pytest.fixture
def client(lake):
    score_watchlist(lake, now=NOW)
    return TestClient(create_app(str(lake.root)))


def test_api_scores_and_watchlist(client, lake):
    assert client.get("/api/health").json() == {"status": "ok"}
    listed = client.get("/api/watchlist").json()
    assert {item["status"] for item in listed} == {"Beschikbaar"}
    key = listed[0]["score_key"]
    assert client.get(f"/api/scores/{key}").json()["score_key"] == key
    assert client.get("/api/scores/0000000000000000").status_code == 404
    assert client.get("/api/scores/..%2Fwatchlist").status_code == 404


def test_api_add_and_remove(client):
    response = client.post(
        "/api/watchlist", json={"url": "https://www.funda.nl/detail/koop/x/y/43000077/"}
    )
    assert (response.status_code, response.json()["funda_id"]) == (202, "43000077")
    assert client.post("/api/watchlist", json={"url": "https://example.com"}).status_code == 422
    assert client.delete("/api/watchlist/43000077").status_code == 204
    assert client.delete("/api/watchlist/43000077").status_code == 404
