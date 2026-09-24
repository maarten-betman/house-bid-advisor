from datetime import date

import pandas as pd

from bidadvisor.kadaster.parse import filter_scope, parse_lines


def test_parses_sale_lines_with_and_without_postcode():
    lines = [
        "Koopsominformatie",
        "Postcode: 3511 AB",
        "12 03-02-2025 € 425.000 130 m2",
        "12 A 15-06-2024 € 389.500,00",
        "3512CD 7 1-hoog 01-12-2023 312.000",
    ]
    result = parse_lines(lines, "test.pdf")
    rows = result.rows.to_dict("records")
    assert result.unparsed == []
    assert rows[0] | {"perceel_m2": 130} == {
        "pc6": "3511AB",
        "huisnummer": 12,
        "toevoeging": "",
        "sale_date": date(2025, 2, 3),
        "koopsom": 425_000,
        "perceel_m2": 130,
        "source_file": "test.pdf",
    }
    assert (rows[1]["toevoeging"], rows[1]["koopsom"]) == ("A", 389_500)
    assert pd.isna(rows[1]["perceel_m2"])
    assert (rows[2]["pc6"], rows[2]["toevoeging"], rows[2]["huisnummer"]) == ("3512CD", "1-hoog", 7)


def test_dated_lines_that_do_not_parse_are_reported():
    result = parse_lines(["12 03-02-2025 onbekend"], "x.pdf")
    assert result.rows.empty
    assert result.unparsed == ["12 03-02-2025 onbekend"]


def test_scope_keeps_kadaster_bounds():
    rows = parse_lines(
        ["3511AB 1 01-01-2002 300.000", "3511AB 2 01-01-2010 4.000", "3511AB 3 01-01-2010 250.000"],
        "x.pdf",
    ).rows
    assert filter_scope(rows)["huisnummer"].tolist() == [3]
