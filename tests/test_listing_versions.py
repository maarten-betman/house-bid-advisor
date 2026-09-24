import pandas as pd

from bidadvisor.transform.listings import merge_versions, upsert_listings


def snap(day, price, status="available", funda_id="1234567", **extra):
    return {
        "funda_id": funda_id,
        "fetched_at": pd.Timestamp(day, tz="UTC"),
        "status": status,
        "status_raw": status,
        "vraagprijs": price,
        "prijs_type": "k.k.",
        "energielabel": "C",
        "kenmerken_hash": "h1",
    } | extra


def run(days):
    history = None
    for batch in days:
        history = merge_versions(history, pd.DataFrame(batch))
    return history


def test_unchanged_snapshots_add_no_rows():
    history = run([[snap("2026-09-01", 450_000)], [snap("2026-09-02", 450_000)]])
    assert len(history) == 1
    assert history.iloc[0]["is_current"]


def test_price_cut_closes_old_version():
    history = run(
        [
            [snap("2026-09-01", 450_000)],
            [snap("2026-09-10", 435_000)],
            [snap("2026-09-20", 435_000, status="negotiations")],
        ]
    )
    assert history["vraagprijs"].tolist() == [450_000, 435_000, 435_000]
    assert history["is_current"].tolist() == [False, False, True]
    assert history["valid_to"].dt.strftime("%Y-%m-%d").tolist()[:2] == ["2026-09-10", "2026-09-20"]
    assert pd.isna(history["valid_to"].iloc[2])


def test_same_day_rerun_overwrites_that_days_row():
    history = run([[snap("2026-09-01", 450_000)], [snap("2026-09-01", 449_000)]])
    assert history["vraagprijs"].tolist() == [449_000]


def test_listings_not_in_todays_batch_are_untouched():
    history = run(
        [
            [snap("2026-09-01", 1, funda_id="a"), snap("2026-09-01", 2, funda_id="b")],
            [snap("2026-09-02", 3, funda_id="a")],
        ]
    )
    current = history.loc[history["is_current"]].set_index("funda_id")["vraagprijs"]
    assert current.to_dict() == {"a": 3, "b": 2}


def test_upsert_keeps_first_seen_and_bag_id():
    first = upsert_listings(None, pd.DataFrame([snap("2026-09-01", 1, url="u1", makelaar="A")]))
    first["bag_vbo_id"] = "0344010000000001"
    second = upsert_listings(first, pd.DataFrame([snap("2026-09-05", 1, url="u2", makelaar=None)]))
    row = second.iloc[0]
    assert (row["url"], row["makelaar"], row["bag_vbo_id"]) == ("u2", "A", "0344010000000001")
    assert str(row["first_seen"].date()) == "2026-09-01"
    assert str(row["last_seen"].date()) == "2026-09-05"
