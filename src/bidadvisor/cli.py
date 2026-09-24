"""Pipeline commands: Kadaster baseline (M1) and the Funda watchlist (M2)."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

from bidadvisor.bag.match import match_addresses, match_rate, pdok_lookup
from bidadvisor.kadaster.parse import filter_scope, parse_pdf
from bidadvisor.listings.nightly import STOP_MARKER, run_funda_step, stop_marker
from bidadvisor.listings.watchlist import Watchlist
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


def watch_add(args: argparse.Namespace) -> int:
    watchlist = Watchlist(Lake(args.lake))
    entry = watchlist.add(args.url)
    watchlist.save()
    print(f"Watching {entry.funda_id}; fetched on the next nightly run")
    return 0


def watch_remove(args: argparse.Namespace) -> int:
    watchlist = Watchlist(Lake(args.lake))
    removed = watchlist.remove(args.funda_id)
    watchlist.save()
    print("Removed" if removed else f"{args.funda_id} is not on the watchlist")
    return 0 if removed else 1


def watch_list(args: argparse.Namespace) -> int:
    for entry in Watchlist(Lake(args.lake)).active():
        print(f"{entry.funda_id}  added {entry.added_at}  {entry.url}")
    return 0


class _SimulatedFailure:
    """Stands in for Funda to prove the stop path without making a request."""

    version = "simulated"

    def fetch(self, funda_id: str):
        raise RuntimeError(f"simulated failure for {funda_id}")

    def close(self) -> None:
        pass


def funda_run(args: argparse.Namespace) -> int:
    if args.simulate_error:
        open_source = _SimulatedFailure
    else:
        from bidadvisor.listings.source import PyFundaSource

        open_source = PyFundaSource
    lookup = None if args.no_bag else pdok_lookup()
    result = run_funda_step(Lake(args.lake), open_source, lookup=lookup)
    print(result)
    return result.exit_code


def funda_resume(args: argparse.Namespace) -> int:
    lake = Lake(args.lake)
    marker = stop_marker(lake)
    if marker is None:
        print("No stop marker; Funda is enabled")
        return 0
    print(f"Clearing stop marker: {marker}")
    lake.delete("bronze", STOP_MARKER)
    return 0


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

    add = commands.add_parser("watch-add", help="Add a Funda listing link to the watchlist")
    add.add_argument("url")
    add.set_defaults(run=watch_add)

    remove = commands.add_parser("watch-remove", help="Stop tracking a listing")
    remove.add_argument("funda_id")
    remove.set_defaults(run=watch_remove)

    commands.add_parser("watch-list", help="Show active watchlist").set_defaults(run=watch_list)

    run = commands.add_parser("funda-run", help="Nightly Funda step for the watchlist")
    run.add_argument("--no-bag", action="store_true", help="Skip PDOK BAG matching")
    run.add_argument(
        "--simulate-error", action="store_true", help="Trip the stop marker without calling Funda"
    )
    run.set_defaults(run=funda_run)

    resume = commands.add_parser("funda-resume", help="Show and remove the Funda stop marker")
    resume.set_defaults(run=funda_resume)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
