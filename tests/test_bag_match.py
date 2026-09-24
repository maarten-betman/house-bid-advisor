import pandas as pd
import pytest

from bidadvisor.bag.match import BagCandidate, match_addresses, match_rate, normalise_toevoeging


@pytest.mark.parametrize(
    ("raw", "key"),
    [("A", "A"), ("a", "A"), ("-1", "1"), ("1-hoog", "1"), (" 1 hg ", "1"), ("", ""), (None, "")],
)
def test_normalise_toevoeging(raw, key):
    assert normalise_toevoeging(raw) == key


def candidate(vbo, letter="", toev="", lat=52.0, lon=5.0):
    return BagCandidate(vbo, "3511AB", 12, letter, toev, "BU03440101", lat, lon)


def test_match_uses_toevoeging_then_coordinates_and_quarantines_the_rest():
    book = {
        ("3511AB", 12): [
            candidate("v0"),
            candidate("vA", "A"),
            candidate("v1", toev="1", lat=52.01),
        ],
        ("3511AB", 99): [],
    }
    rows = pd.DataFrame(
        {
            "pc6": ["3511AB", "3511AB", "3511AB", "3511AB", "3511AB"],
            "huisnummer": [12, 12, 12, 99, 12],
            "toevoeging": ["", "a", "1-hoog", "", "B"],
            "lat": [None, None, None, None, None],
            "lon": [None, None, None, None, None],
        }
    )
    matched, quarantine = match_addresses(rows, lambda pc6, nr: book[(pc6, nr)])
    assert matched["bag_vbo_id"].tolist() == ["v0", "vA", "v1"]
    assert quarantine["reason"].tolist() == ["no_address", "toevoeging_mismatch"]
    assert match_rate(matched, quarantine) == pytest.approx(0.6)


def test_coordinates_break_ties_for_funda_addresses():
    book = {("3511AB", 12): [candidate("vA", "A", lat=52.0), candidate("vB", "B", lat=52.01)]}
    rows = pd.DataFrame(
        {"pc6": ["3511AB"], "huisnummer": [12], "toevoeging": [""], "lat": [52.0099], "lon": [5.0]}
    )
    matched, _ = match_addresses(rows, lambda pc6, nr: book[(pc6, nr)])
    assert matched["bag_vbo_id"].tolist() == ["vB"]
