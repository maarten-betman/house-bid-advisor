import pandas as pd

from bidadvisor.transform.matching import episodes, match_sales

T = pd.Timestamp


def version(funda_id, day, price, status="available", raw="Beschikbaar"):
    return {
        "funda_id": funda_id,
        "valid_from": T(day),
        "status": status,
        "status_raw": raw,
        "vraagprijs": price,
        "prijs_type": "k.k.",
    }


def test_relist_continues_episode_and_sale_is_matched_in_window():
    listings = pd.DataFrame(
        [
            {
                "funda_id": "1000001",
                "bag_vbo_id": "v1",
                "first_seen": T("2025-01-01"),
                "last_seen": T("2025-03-01"),
                "makelaar": "A",
                "woningtype": "tussenwoning",
            },
            {
                "funda_id": "1000002",
                "bag_vbo_id": "v1",
                "first_seen": T("2025-06-01"),
                "last_seen": T("2025-08-01"),
                "makelaar": "B",
                "woningtype": "tussenwoning",
            },
            {
                "funda_id": "1000003",
                "bag_vbo_id": "v2",
                "first_seen": T("2025-01-01"),
                "last_seen": T("2025-02-01"),
                "makelaar": "C",
                "woningtype": "hoekwoning",
            },
        ]
    )
    versions = pd.DataFrame(
        [
            version("1000001", "2025-01-01", 500_000),
            version("1000001", "2025-02-01", 480_000),
            version("1000002", "2025-06-01", 470_000),
            version(
                "1000002",
                "2025-07-01",
                470_000,
                "Verkocht onder voorbehoud",
                "Verkocht onder voorbehoud",
            ),
            version("1000003", "2025-01-01", 400_000),
        ]
    )
    ep = episodes(listings, versions).set_index("episode_id")
    assert ep.loc["1000001", "funda_ids"] == "1000001,1000002"
    assert (ep.loc["1000001", "first_ask"], ep.loc["1000001", "last_ask"]) == (500_000, 470_000)
    assert ep.loc["1000001", "n_price_cuts"] == 2
    assert ep.loc["1000001", "sold_from"] == T("2025-07-01")
    assert pd.isna(ep.loc["1000003", "sold_from"])

    transactions = pd.DataFrame(
        {
            "bag_vbo_id": ["v1", "v1", "v1"],
            "txn_id": ["old", "hit", "late"],
            "sale_date": [T("2024-05-01"), T("2025-09-15"), T("2026-06-01")],
            "koopsom": [300_000, 493_500, 600_000],
        }
    )
    matched = match_sales(ep.reset_index(), transactions)
    assert matched["txn_id"].tolist() == ["hit"]
    assert matched["gap"].iloc[0] == 493_500 / 470_000 - 1
    assert matched["days_on_market"].iloc[0] == (T("2025-07-01") - T("2025-01-01")).days


def test_gap_over_a_year_starts_a_new_episode():
    listings = pd.DataFrame(
        [
            {
                "funda_id": "1",
                "bag_vbo_id": "v",
                "first_seen": T("2023-01-01"),
                "last_seen": T("2023-02-01"),
                "makelaar": None,
                "woningtype": None,
            },
            {
                "funda_id": "2",
                "bag_vbo_id": "v",
                "first_seen": T("2024-06-01"),
                "last_seen": T("2024-07-01"),
                "makelaar": None,
                "woningtype": None,
            },
        ]
    )
    versions = pd.DataFrame([version("1", "2023-01-01", 1), version("2", "2024-06-01", 2)])
    assert len(episodes(listings, versions)) == 2
