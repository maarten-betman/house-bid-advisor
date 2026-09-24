import pytest

from bidadvisor import cli
from bidadvisor.listings import source as source_module
from bidadvisor.listings.nightly import STOP_MARKER
from bidadvisor.listings.watchlist import Watchlist
from bidadvisor.storage.lake import Lake


class Broken:
    version = "broken"

    def fetch(self, funda_id):
        raise RuntimeError("429 Too Many Requests")

    def close(self):
        pass


@pytest.fixture
def posts(monkeypatch):
    sent = []
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://ntfy.example/topic")
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/ping/abc")
    monkeypatch.setattr("httpx.post", lambda url, content, timeout: sent.append(url))
    monkeypatch.setattr(cli, "pdok_lookup", lambda: None)
    return sent


def test_quiet_night_pings_start_and_success(tmp_path, posts):
    assert cli.main(["--lake", str(tmp_path), "nightly"]) == 0
    assert posts == ["https://hc.example/ping/abc/start", "https://hc.example/ping/abc"]


def test_funda_failure_alerts_and_pings_fail(tmp_path, posts, monkeypatch):
    monkeypatch.setattr(source_module, "PyFundaSource", Broken)
    wl = Watchlist(Lake(tmp_path))
    wl.add("https://www.funda.nl/detail/koop/utrecht/huis-1/43000001/")
    wl.save()
    assert cli.main(["--lake", str(tmp_path), "nightly"]) == 1
    assert Lake(tmp_path).exists("bronze", STOP_MARKER)
    assert posts[0].endswith("/start") and posts[-1].endswith("/fail")
    assert posts.count("https://ntfy.example/topic") == 2  # the stop, then the run summary
