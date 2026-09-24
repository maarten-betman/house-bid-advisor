"""Parse Kadaster Koopsominformatie PDFs into bronze ``kadaster_rows``.

The PDF layout is not publicly documented. The line pattern below assumes one sale
per text line in the order postcode, huisnummer, toevoeging, datum, koopsom,
perceeloppervlakte, with the postcode either on the line or in a preceding header.
Check it against the first purchased PDF (open question in the spec) and adjust
``SALE_LINE`` — the tests pin the behaviour so a change shows up immediately.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

PC6 = r"(?P<pc6>\d{4}\s?[A-Z]{2})"
SALE_LINE = re.compile(
    rf"^(?:{PC6}\s+)?"
    r"(?P<huisnummer>\d{1,5})\s*"
    r"(?P<toevoeging>(?!\d{2}-\d{2}-\d{4})[A-Za-z0-9\-]{1,6})?\s+"
    r"(?P<date>\d{2}-\d{2}-\d{4})\s+"
    r"(?:€\s*)?(?P<koopsom>\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{2}|,-)?"
    r"(?:\s+(?P<perceel>\d{1,3}(?:\.\d{3})*|\d+)\s*(?:m2|m²)?)?\s*$"
)
HEADER_LINE = re.compile(rf"^Postcode:?\s*{PC6}\b", re.IGNORECASE)

COLUMNS = [
    "pc6",
    "huisnummer",
    "toevoeging",
    "sale_date",
    "koopsom",
    "perceel_m2",
    "source_file",
]


@dataclass(frozen=True)
class ParseResult:
    rows: pd.DataFrame
    unparsed: list[str]


def _int(value: str | None) -> int | None:
    return int(value.replace(".", "")) if value else None


def _pc6(value: str) -> str:
    return value.replace(" ", "").upper()


def parse_lines(lines: Iterable[str], source_file: str) -> ParseResult:
    """Turn extracted text lines into rows; lines that look like sales but don't parse are kept."""
    rows: list[dict] = []
    unparsed: list[str] = []
    current_pc6: str | None = None
    for raw in lines:
        line = " ".join(raw.split())
        if not line:
            continue
        if header := HEADER_LINE.match(line):
            current_pc6 = _pc6(header["pc6"])
            continue
        match = SALE_LINE.match(line)
        if not match:
            if re.search(r"\d{2}-\d{2}-\d{4}", line):
                unparsed.append(line)
            continue
        pc6 = _pc6(match["pc6"]) if match["pc6"] else current_pc6
        if pc6 is None:
            unparsed.append(line)
            continue
        rows.append(
            {
                "pc6": pc6,
                "huisnummer": int(match["huisnummer"]),
                "toevoeging": match["toevoeging"] or "",
                "sale_date": datetime.strptime(match["date"], "%d-%m-%Y").date(),
                "koopsom": _int(match["koopsom"]),
                "perceel_m2": _int(match["perceel"]),
                "source_file": source_file,
            }
        )
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame["perceel_m2"] = frame["perceel_m2"].astype("Int64")
    return ParseResult(frame, unparsed)


def parse_pdf(path: Path) -> ParseResult:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        lines = [line for page in pdf.pages for line in (page.extract_text() or "").splitlines()]
    return parse_lines(lines, source_file=path.name)


def filter_scope(rows: pd.DataFrame, since: date = date(2003, 1, 1)) -> pd.DataFrame:
    """Kadaster's own bounds: private sales since 2003, €5,000 to €5,000,000."""
    keep = (pd.to_datetime(rows["sale_date"]) >= pd.Timestamp(since)) & rows["koopsom"].between(
        5_000, 5_000_000
    )
    return rows.loc[keep].reset_index(drop=True)
