# house-bid-advisor

Decision support for one house buyer: predict the final sale price of a Funda listing
from Kadaster transactions, and turn it into a bid ladder (win chance, maandlasten,
eigen geld per bid). Personal, non-commercial use only; see the technical spec for
the Funda-terms constraints.

This repository currently implements **M1 — Kadaster baseline**: what houses in the
search area actually sell for, without Funda.

## Layout

| Path | What it does |
| --- | --- |
| `src/bidadvisor/kadaster/parse.py` | Koopsominformatie PDF → bronze `kadaster_rows` |
| `src/bidadvisor/bag/match.py` | Address → `bag_vbo_id` via PDOK Locatieserver; quarantine for misses |
| `src/bidadvisor/transform/transactions.py` | Portfolio deals, outlier flags, price indexing, recency weights |
| `src/bidadvisor/model/hedonic.py` | Ridge baseline / LightGBM challenger with split-conformal q05…q95 |
| `src/bidadvisor/model/comparables.py` | k-nearest comparables, also the taxatie proxy |
| `src/bidadvisor/model/backtest.py` | Rolling monthly backtest against the M1 targets |
| `src/bidadvisor/storage/lake.py` | bronze / silver / gold / serving on a local folder |

## Running M1

```bash
uv sync
# 1. Put purchased Koopsominformatie PDFs in data/bronze/kadaster/inbox/
uv run bidadvisor kadaster-ingest
# 2. Match to BAG (exits non-zero below a 98% match rate)
uv run bidadvisor bag-match
# 3. Join BAG attributes: parquet with bag_vbo_id, gebruiksoppervlakte_m2, bouwjaar, lat, lon
#    (optional: woningtype, energielabel)
uv run bidadvisor build --bag-attributes data/reference/bag_attributes.parquet
# 4. Backtest (exits non-zero when the M1 targets are missed)
uv run bidadvisor backtest --index data/reference/cbs_index.csv --region <region> --model ridge
```

The price index CSV has columns `region, month (YYYY-MM), value` and optionally
`published_on`; without it a month counts as published 22 days after it ends.

## Checks

```bash
uv run pytest
uv run ruff check .
```

The model tests run on a synthetic market with a known price formula, so they check
the pipeline, not real-world accuracy. Real accuracy comes from the backtest on
purchased Kadaster data.

## Still to confirm on real data

- **PDF line layout.** `SALE_LINE` in `kadaster/parse.py` is an assumption. Run
  `kadaster-ingest` on the first PDF; unparsed dated lines are printed and make it exit 1.
- **BAG attributes and woningtype.** Floor area and bouwjaar come from the BAG; a
  loader for them, and a woningtype derived from pand geometry, are not built yet.
- **CBS index fetch.** The StatLine table and region codes still need choosing; the
  index is a CSV input until then.
- **Intervals.** Split-conformal calibration is implemented directly rather than
  through MAPIE: the same method, fewer dependencies.
