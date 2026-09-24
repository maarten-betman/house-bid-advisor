"""M1 pipeline commands: Kadaster PDFs → BAG match → transactions → rolling backtest."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

from bidadvisor.bag.match import match_addresses, match_rate, pdok_lookup
from bidadvisor.kadaster.parse import filter_scope, parse_pdf
from bidadvisor.model.backtest import rolling_backtest, summarise
from bidadvisor.storage.lake import Lake
from bidadvisor.transform.transactions import build_transactions

MIN_MATCH_RATE = 0.98
BAG_ATTRIBUTES = ["bag_vbo_id", "gebruiksoppervlakte_m2", "bouwjaar", "lat", "lon"]


def kadaster_ingest(args: argparse.Namespace) -> int:
    lake = Lake(args.lake)
    inbox = Path(args.inbox)
    processed = inbox.parent / "processed"
    processed.mkdir(exist_ok=True)
    frames, unparsed = [], 0
    for pdf in sorted(inbox.glob("*.pdf")):
        result = parse_pdf(pdf)
        frames.append(result.rows)
        unparsed += len(result.unparsed)
        for line in result.unparsed:
            print(f"unparsed [{pdf.name}]: {line}", file=sys.stderr)
        shutil.move(pdf, processed / pdf.name)
    if not frames:
        print("No new PDFs")
        return 0
    try:
        existing = lake.read_table("bronze", "kadaster_rows")
    except FileNotFoundError:
        existing = pd.DataFrame()
    rows = pd.concat([existing, *frames], ignore_index=True).drop_duplicates(
        ["pc6", "huisnummer", "toevoeging", "sale_date", "koopsom"]
    )
    lake.write_table("bronze", "kadaster_rows", rows)
    print(f"{sum(len(f) for f in frames)} rows parsed, {unparsed} lines unparsed")
    return 1 if unparsed else 0


def bag_match(args: argparse.Namespace) -> int:
    lake = Lake(args.lake)
    rows = filter_scope(lake.read_table("bronze", "kadaster_rows"))
    matched, quarantine = match_addresses(rows, pdok_lookup())
    lake.write_table("silver", "kadaster_matched", matched)
    lake.write_table("silver", "bag_quarantine", quarantine)
    rate = match_rate(matched, quarantine)
    print(f"BAG match rate {rate:.1%} ({len(matched)} matched, {len(quarantine)} quarantined)")
    return 0 if rate >= MIN_MATCH_RATE else 1


def build(args: argparse.Namespace) -> int:
    lake = Lake(args.lake)
    matched = lake.read_table("silver", "kadaster_matched")
    attributes = pd.read_parquet(args.bag_attributes)
    missing = set(BAG_ATTRIBUTES) - set(attributes.columns)
    if missing:
        raise SystemExit(f"BAG attributes file lacks columns: {sorted(missing)}")
    sales = matched.merge(attributes, on="bag_vbo_id", how="inner")
    transactions = build_transactions(sales)
    lake.write_table("silver", "transaction", transactions)
    outliers = int(transactions["is_outlier"].sum())
    print(f"{len(transactions)} transactions, {outliers} flagged outliers")
    return 0


def backtest(args: argparse.Namespace) -> int:
    lake = Lake(args.lake)
    transactions = lake.read_table("silver", "transaction")
    index = pd.read_csv(args.index)
    predictions = rolling_backtest(
        transactions, index, region=args.region, months=args.months, kind=args.model
    )
    summary = summarise(predictions)
    lake.write_table("gold", f"backtest_{args.model}", predictions)
    lake.write_json("gold", f"backtest_{args.model}.json", summary.to_dict())
    print(summary)
    return 0 if summary.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bidadvisor")
    parser.add_argument("--lake", default="data", help="Local lake root (default: ./data)")
    commands = parser.add_subparsers(required=True)

    ingest = commands.add_parser("kadaster-ingest", help="Parse PDFs from an inbox folder")
    ingest.add_argument("--inbox", default="data/bronze/kadaster/inbox")
    ingest.set_defaults(run=kadaster_ingest)

    match = commands.add_parser("bag-match", help="Resolve Kadaster rows to BAG IDs via PDOK")
    match.set_defaults(run=bag_match)

    build_cmd = commands.add_parser("build", help="Join BAG attributes, build transactions")
    build_cmd.add_argument("--bag-attributes", required=True, help=f"Parquet with {BAG_ATTRIBUTES}")
    build_cmd.set_defaults(run=build)

    bt = commands.add_parser("backtest", help="Rolling monthly backtest of the hedonic model")
    bt.add_argument("--index", required=True, help="CSV: region, month, value[, published_on]")
    bt.add_argument("--region", required=True)
    bt.add_argument("--months", type=int, default=24)
    bt.add_argument("--model", choices=["ridge", "lightgbm"], default="ridge")
    bt.set_defaults(run=backtest)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
