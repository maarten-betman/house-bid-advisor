"""Alerts and run heartbeats, configured by environment variables.

- ``ALERT_WEBHOOK_URL``: every alert is POSTed there as plain text (ntfy.sh topics,
  Slack/Discord-compatible relays, or anything that accepts a text body).
- ``HEALTHCHECK_URL``: a dead-man's switch such as healthchecks.io. The nightly run
  pings ``/start``, then the URL itself on success or ``/fail`` on failure, so a run
  that never starts (host down, image broken) still raises an alert.

Unset variables turn the corresponding call into a no-op; alerts still go to stderr.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Literal

import httpx


@dataclass(frozen=True)
class Ops:
    alert_url: str | None = None
    healthcheck_url: str | None = None

    @classmethod
    def from_env(cls) -> Ops:
        return cls(os.environ.get("ALERT_WEBHOOK_URL"), os.environ.get("HEALTHCHECK_URL"))

    def alert(self, message: str) -> None:
        print(f"ALERT: {message}", file=sys.stderr)
        if self.alert_url:
            self._post(self.alert_url, f"house-bid-advisor: {message}")

    def ping(self, state: Literal["start", "success", "fail"], body: str = "") -> None:
        if not self.healthcheck_url:
            return
        base = self.healthcheck_url.rstrip("/")
        self._post(base if state == "success" else f"{base}/{state}", body)

    @staticmethod
    def _post(url: str, body: str) -> None:
        try:
            httpx.post(url, content=body.encode(), timeout=10)
        except httpx.HTTPError as exc:  # alerting must never take the run down
            print(f"Could not reach {url}: {exc}", file=sys.stderr)
