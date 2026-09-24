import json
from datetime import UTC, datetime

import pytest

from bidadvisor.listings.source import ListingGone, funda_id_from, snapshot_from_listing, woningtype

funda = pytest.importorskip("funda")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.funda.nl/detail/koop/utrecht/huis-reehorst-13/43117443/", "43117443"),
        ("https://www.funda.nl/detail/koop/utrecht/huis-12-a/43117443/?utm=1#fotos", "43117443"),
        ("43117443", "43117443"),
        (4311744, "4311744"),
    ],
)
def test_funda_id_from(value, expected):
    assert funda_id_from(value) == expected


def test_funda_id_from_rejects_links_without_id():
    with pytest.raises(ValueError):
        funda_id_from("https://www.funda.nl/zoeken/koop?selected_area=utrecht")


@pytest.mark.parametrize(
    ("text", "object_type", "expected"),
    [
        ("Eengezinswoning, tussenwoning", "house", "tussenwoning"),
        ("Eengezinswoning, eindwoning", "house", "hoekwoning"),
        ("Villa, vrijstaande woning", "house", "vrijstaand"),
        ("2-onder-1-kapwoning", "house", "2-onder-1-kap"),
        ("Bovenwoning", "apartment", "appartement"),
        ("Woonboerderij", "house", None),
    ],
)
def test_woningtype(text, object_type, expected):
    assert woningtype(text, object_type=object_type) == expected


def listing(**details):
    return funda.Listing(
        tiny_id="43117443",
        address=funda.Address(postcode="3511 AB", house_number="12", house_number_suffix="A"),
        price=funda.Price(amount=450_000, condition="kosten koper", price_type="vraagprijs"),
        areas=funda.Areas(living=120, plot=140),
        property_details=funda.PropertyDetails(
            object_type="house",
            house_type="Eengezinswoning, hoekwoning",
            construction_year=1965,
            construction_type="existing",
            energy_label="C",
            status="available",
            raw_status="Beschikbaar",
        )
        if not details
        else funda.PropertyDetails(**details),
        location=funda.GeoLocation(latitude=52.09, longitude=5.12),
        urls=funda.Urls(full="https://www.funda.nl/detail/koop/utrecht/x/43117443/"),
        brokers=(funda.Broker(name="Makelaardij Test"),),
        characteristics=(
            funda.CharacteristicSection(
                title="Bouw", items=(funda.Characteristic(label="Bouwjaar", value="1965"),)
            ),
        ),
        raw={"Identifiers": {"TinyId": "43117443"}},
    )


def test_snapshot_contract_with_pinned_pyfunda():
    at = datetime(2026, 9, 24, 4, tzinfo=UTC)
    snap = snapshot_from_listing(listing(), "43117443", at, "pyfunda 3.1.5")
    row = snap.to_row()
    assert row | {"kenmerken_hash": "x", "payload": "x"} == {
        "funda_id": "43117443",
        "fetched_at": at,
        "source_version": "pyfunda 3.1.5",
        "url": "https://www.funda.nl/detail/koop/utrecht/x/43117443/",
        "status": "available",
        "status_raw": "Beschikbaar",
        "vraagprijs": 450_000,
        "prijs_type": "vraagprijs | kosten koper",
        "energielabel": "C",
        "woningtype": "hoekwoning",
        "construction": "existing",
        "pc6": "3511AB",
        "huisnummer": 12,
        "toevoeging": "A",
        "lat": 52.09,
        "lon": 5.12,
        "gebruiksoppervlakte_m2": 120,
        "perceel_m2": 140,
        "bouwjaar": 1965,
        "makelaar": "Makelaardij Test",
        "kenmerken_hash": "x",
        "payload": "x",
    }
    assert json.loads(snap.payload) == {"Identifiers": {"TinyId": "43117443"}}


def test_kenmerken_hash_changes_with_characteristics():
    at = datetime(2026, 9, 24, tzinfo=UTC)
    a = snapshot_from_listing(listing(), "1234567", at, "v")
    changed = listing()
    object.__setattr__(changed, "characteristics", ())
    b = snapshot_from_listing(changed, "1234567", at, "v")
    assert a.kenmerken_hash != b.kenmerken_hash


def test_adapter_disables_retries_and_maps_not_found(monkeypatch):
    from bidadvisor.listings.source import PyFundaSource

    source = PyFundaSource()
    try:
        assert source._client.max_retries == 0
        assert source._client.min_request_interval == 10.0

        def missing(_):
            raise funda.ListingNotFound("gone")

        monkeypatch.setattr(type(source._client), "listing", lambda self, i: missing(i))
        with pytest.raises(ListingGone):
            source.fetch("1234567")
    finally:
        source.close()
