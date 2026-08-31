"""Unit tests that do not require live AutoZone Pro credentials."""

from azpro_mcp_server.models import PartHit, PartSearchResult, VehicleSummary


def test_models_roundtrip():
    v = VehicleSummary(year="2017", make="Cadillac", model="XT5", is_current=True)
    assert v.model_dump()["make"] == "Cadillac"
    p = PartHit(
        part_number="D1896",
        brand="Duralast",
        description="Pads",
        cost=28.28,
        list_price=64.49,
        store_qty=1,
    )
    r = PartSearchResult(query="D1896", total=1, parts=[p], cheapest=p)
    assert r.parts[0].part_number == "D1896"
    assert r.cheapest.cost == 28.28
