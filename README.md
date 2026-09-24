# house-bid-advisor

Decision support for one house buyer: predict the final sale price of a Funda listing
from Kadaster transactions, and turn it into a bid ladder (win chance, maandlasten,
eigen geld per bid). Personal, non-commercial use only; see the technical spec for
the Funda-terms constraints.

Implemented so far:

- **M1 — Kadaster baseline**: what houses in the search area actually sell for, without Funda.
- **M2 — Funda watchlist**: daily snapshots of the listings you choose, with a stop marker.
- **M3 — Scoring and API**: ask-anchored estimate, combiner, serving JSON, authenticated API.

## Layout

| Path | What it does |
| --- | --- |
| `src/bidadvisor/kadaster/parse.py` | Koopsominformatie PDF → bronze `kadaster_rows` |
| `src/bidadvisor/bag/match.py` | Address → `bag_vbo_id` via PDOK Locatieserver; quarantine for misses |
| `src/bidadvisor/transform/transactions.py` | Portfolio deals, outlier flags, price indexing, recency weights |
| `src/bidadvisor/model/hedonic.py` | Ridge baseline / LightGBM challenger with split-conformal q05…q95 |
| `src/bidadvisor/model/comparables.py` | k-nearest comparables, also the taxatie proxy |
| `src/bidadvisor/model/backtest.py` | Rolling monthly backtest against the M1 targets |
| `src/bidadvisor/listings/source.py` | `ListingSource` boundary; the only module that imports pyfunda |
| `src/bidadvisor/listings/watchlist.py` | `serving/watchlist.json`, at most 50 active listings |
| `src/bidadvisor/listings/nightly.py` | Nightly Funda step: paced fetches, stop marker, persistence |
| `src/bidadvisor/transform/listings.py` | Silver `listing` and SCD2 `listing_version` |
| `src/bidadvisor/transform/matching.py` | Listing episodes (relists) matched to their Kadaster sale |
| `src/bidadvisor/model/ask.py` | Ask-anchored estimate: NVM prior gap, empirical Bayes, adjusters |
| `src/bidadvisor/model/combine.py` | Inverse-variance combination; stacker + conformal from 30 matches |
| `src/bidadvisor/pipeline/train.py` | Train hedonic (+ backtest), ask model and combiner |
| `src/bidadvisor/pipeline/score.py` | Score the watchlist → `serving/scores/{key}.json` |
| `src/bidadvisor/api.py` | FastAPI: scores and watchlist |
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

## Running M2 (Funda watchlist)

pyfunda is an optional extra (`uv sync --extra funda`); M1 runs without it.

```bash
uv run bidadvisor watch-add "https://www.funda.nl/detail/koop/<city>/<slug>/<id>/"
uv run bidadvisor watch-list
uv run bidadvisor funda-run            # nightly: one detail call per listing, 10 s apart
uv run bidadvisor funda-run --simulate-error --no-bag   # prove the stop path, no Funda call
uv run bidadvisor funda-resume         # show and remove the stop marker
```

Guardrails, as in the spec's risk table:

- Only watchlisted listings are fetched: no search, similar-listings or price-history calls.
- At most 50 active listings, one `listing()` call each per run, 10 seconds apart.
- pyfunda runs with `max_retries=0`, so it neither retries nor rotates TLS fingerprints.
- Any error writes `bronze/funda/STOP.json`, alerts and exits 1. Later runs skip Funda until
  the marker is removed. A clean 404 (listing taken offline) is reported, not a stop.

## Running M3 (scoring and API)

Reference data in the lake:

- `bronze/reference/cbs_index.csv`: `region, month (YYYY-MM), value[, published_on]`
- `bronze/reference/bag_attributes.parquet`: `bag_vbo_id, gebruiksoppervlakte_m2, bouwjaar,
  lat, lon` (optional `woningtype`, `energielabel`)

```bash
uv run bidadvisor build                        # silver transactions from matched Kadaster rows
INDEX_REGION=<region> uv run bidadvisor train  # hedonic model + 24-month backtest
uv run bidadvisor score                        # one JSON per watchlisted listing
uv run bidadvisor serve                        # API on :8000 (no auth: put Caddy in front)
```

A listing is scored even before any Kadaster model exists, from its asking price and
the NVM priors; the payload's `warnings` say which parts are missing.

| Method | Path | Does |
| --- | --- | --- |
| GET | `/api/scores/{key}` | Latest score; `key` = `bag_vbo_id`, or `funda-<id>` before a BAG match |
| GET | `/api/watchlist` | Watched listings with status, score key and last score time |
| POST | `/api/watchlist` | `{"url": "<funda link>"}` → 202; fetched on the next nightly run |
| DELETE | `/api/watchlist/{funda_id}` | Stops tracking a listing |
| GET | `/api/health` | Liveness, no login |

Score payload: `final_price` (q05…q95, the distribution the Bieden tab's win curve
interpolates), `hedonic`, `ask.gap_mean`, `weights`, `taxatie` (k-nearest P10/P50/P90),
`listing` (asking price, status, days on market, price cuts), `model_version`, `warnings`.

## Deploying

A DigitalOcean Droplet runs the `nightly` command at 04:00 UTC and the API behind Caddy: see
[deploy/README.md](deploy/README.md). Locally the same image runs with
`docker build -t bidadvisor . && docker run --rm -v "$PWD/data:/data" bidadvisor`.

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
