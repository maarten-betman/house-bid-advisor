"""Medallion layout (bronze, silver, gold, serving) on a local folder.

The same relative paths map one-to-one onto the ADLS Gen2 containers later; only this
class needs an Azure backend then.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

LAYERS = ("bronze", "silver", "gold", "serving")


class Lake:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path(self, layer: str, name: str) -> Path:
        if layer not in LAYERS:
            raise ValueError(f"Unknown layer {layer!r}")
        return self.root / layer / name

    def write_table(self, layer: str, name: str, frame: pd.DataFrame) -> Path:
        target = self.path(layer, f"{name}.parquet")
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
        return target

    def read_table(self, layer: str, name: str) -> pd.DataFrame:
        return pd.read_parquet(self.path(layer, f"{name}.parquet"))

    def write_json(self, layer: str, name: str, payload: dict) -> Path:
        target = self.path(layer, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, default=str))
        return target

    def read_json(self, layer: str, name: str) -> dict:
        return json.loads(self.path(layer, name).read_text())

    def exists(self, layer: str, name: str) -> bool:
        return self.path(layer, name).exists()

    def delete(self, layer: str, name: str) -> None:
        self.path(layer, name).unlink(missing_ok=True)

    def read_tables(self, layer: str, folder: str) -> pd.DataFrame:
        """Concatenate every Parquet file under ``layer/folder`` (append-only partitions)."""
        files = sorted(self.path(layer, folder).glob("*.parquet"))
        if not files:
            raise FileNotFoundError(self.path(layer, folder))
        return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
