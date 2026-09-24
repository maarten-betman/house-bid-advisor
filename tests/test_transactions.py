import pandas as pd
import pytest

from bidadvisor.transform.transactions import build_transactions, index_prices


def sales(**overrides):
    base = {
        "bag_vbo_id": ["a", "b", "c", "d", "e"],
        "pc6": ["3511AB"] * 5,
        "sale_date": ["2025-03-01"] * 2 + ["2025-04-01", "2025-05-01", "2025-06-01"],
        "koopsom": [900_000, 900_000, 400_000, 420_000, 1_500_000],
        "gebruiksoppervlakte_m2": [100, 100, 100, 100, 100],
    }
    return pd.DataFrame(base | overrides)


def test_portfolio_deals_drop_and_outliers_flag():
    result = build_transactions(sales())
    assert set(result["bag_vbo_id"]) == {"c", "d", "e"}
    assert result.set_index("bag_vbo_id")["is_outlier"].to_dict() == {
        "c": False,
        "d": False,
        "e": True,
    }


def test_indexing_uses_only_published_index_values():
    txns = build_transactions(sales()).query("~is_outlier")
    index = pd.DataFrame(
        {
            "region": "r",
            "month": ["2025-04", "2025-05", "2025-06"],
            "value": [100.0, 110.0, 121.0],
        }
    )
    # On 1 July only April and May are published (≈22-day lag), so May is the latest.
    result = index_prices(txns, index, as_of=pd.Timestamp("2025-07-01"), region="r")
    assert result.set_index("bag_vbo_id")["koopsom_indexed"].to_dict() == {
        "c": pytest.approx(440_000),
        "d": pytest.approx(420_000),
    }
    assert str(result["index_to"].iloc[0]) == "2025-05"
