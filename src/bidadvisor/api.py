"""HTTP API for the Bieden tab. Authentication happens in front of it (Caddy basic auth).

GET    /api/scores/{key}           latest score (key = bag_vbo_id, or funda-<id> before BAG match)
GET    /api/watchlist              watched listings with status and last score time
POST   /api/watchlist              {"url": ...} → 202; fetched on the next nightly run
DELETE /api/watchlist/{funda_id}   stop tracking
GET    /api/health                 unauthenticated liveness check
"""

from __future__ import annotations

import os
import re

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bidadvisor.listings.watchlist import Watchlist, WatchlistFull
from bidadvisor.storage.lake import Lake

SCORE_KEY = re.compile(r"^(\d{16}|funda-\d{7,9})$")


class AddListing(BaseModel):
    url: str


def create_app(lake_root: str | None = None) -> FastAPI:
    lake = Lake(lake_root or os.environ.get("BIDADVISOR_LAKE", "/data"))
    app = FastAPI(title="house-bid-advisor", docs_url=None, redoc_url=None)
    origins = [o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/scores/{key}")
    def score(key: str) -> dict:
        if not SCORE_KEY.match(key):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown score key")
        if not lake.exists("serving", f"scores/{key}.json"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not scored yet")
        return lake.read_json("serving", f"scores/{key}.json")

    @app.get("/api/watchlist")
    def watchlist() -> list[dict]:
        latest = _latest_scores(lake)
        return [
            {
                "funda_id": entry.funda_id,
                "url": entry.url,
                "added_at": entry.added_at,
                **latest.get(
                    entry.funda_id, {"status": None, "score_key": None, "scored_at": None}
                ),
            }
            for entry in Watchlist(lake).active()
        ]

    @app.post("/api/watchlist", status_code=status.HTTP_202_ACCEPTED)
    def add(body: AddListing) -> dict:
        wl = Watchlist(lake)
        try:
            entry = wl.add(body.url)
        except WatchlistFull as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        wl.save()
        return {"funda_id": entry.funda_id, "fetched": "on the next nightly run"}

    @app.delete("/api/watchlist/{funda_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove(funda_id: str) -> Response:
        wl = Watchlist(lake)
        if not wl.remove(funda_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not on the watchlist")
        wl.save()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


def _latest_scores(lake: Lake) -> dict[str, dict]:
    folder = lake.path("serving", "scores")
    out: dict[str, dict] = {}
    for path in folder.glob("*.json") if folder.exists() else []:
        payload = lake.read_json("serving", f"scores/{path.name}")
        previous = out.get(payload["funda_id"])
        if previous is None or payload["scored_at"] > previous["scored_at"]:
            out[payload["funda_id"]] = {
                "status": payload["listing"]["status"],
                "score_key": payload["score_key"],
                "scored_at": payload["scored_at"],
            }
    return out
