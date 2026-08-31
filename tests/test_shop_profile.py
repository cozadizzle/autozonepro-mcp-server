"""Local shop profile — no live AutoZone, no real garage identity in-repo."""

from __future__ import annotations

from azpro_mcp_server.server import build_instructions, mcp
from azpro_mcp_server.shop_profile import (
    format_shop_line,
    load_shop_profile,
    profile_complete,
    prompt_shop_profile,
    save_shop_profile,
    suggest_from_session,
)


def test_save_load_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "shop.json"
    monkeypatch.setenv("AZPRO_SHOP_FILE", str(path))
    saved = save_shop_profile(
        {
            "garage_name": "Example Auto LLC",
            "store_number": "1234",
            "address": "1 Main St",
            "city": "Springfield",
            "state": "IL",
            "zip": "62701",
        }
    )
    assert profile_complete(saved)
    loaded = load_shop_profile()
    assert loaded["garage_name"] == "Example Auto LLC"
    assert loaded["store_number"] == "1234"
    assert "Example Auto LLC" in format_shop_line(loaded)
    assert "1234" in format_shop_line(loaded)


def test_suggest_from_az_session_shape():
    status = {
        "shop": {
            "shopName": "Example Auto LLC",
            "shippingAddress": {
                "address1": "1 Main St",
                "city": "Springfield",
                "state": "IL",
                "postalCode": "62701",
            },
        },
        "current_store": {"number": "1234", "address": "9 Store Rd", "city": "Springfield"},
    }
    sug = suggest_from_session(status)
    assert sug["garage_name"] == "Example Auto LLC"
    assert sug["store_number"] == "1234"
    assert sug["address"] == "1 Main St"
    assert sug["city"] == "Springfield"
    assert sug["zip"] == "62701"


def test_first_run_prompt_keeps_suggestions(tmp_path, monkeypatch):
    path = tmp_path / "shop.json"
    monkeypatch.setenv("AZPRO_SHOP_FILE", str(path))
    answers = iter(["", "9999", "", "", "TX", "", ""])

    def fake_input(prompt: str) -> str:
        return next(answers)

    saved = prompt_shop_profile(
        {
            "garage_name": "Bay 2 Motors",
            "store_number": "0001",
            "address": "2 Oak Ave",
            "city": "Austin",
            "state": "TX",
            "zip": "78701",
        },
        input_fn=fake_input,
        print_fn=lambda *a, **k: None,
    )
    assert saved["garage_name"] == "Bay 2 Motors"
    assert saved["store_number"] == "9999"
    assert saved["city"] == "Austin"
    assert saved["state"] == "TX"


def test_instructions_generic_without_named_shop(tmp_path, monkeypatch):
    monkeypatch.setenv("AZPRO_SHOP_FILE", str(tmp_path / "missing.json"))
    text = build_instructions()
    assert "Cozad" not in text
    assert "Port Charlotte" not in text
    assert "shop profile is unset" in text.lower() or "Shop profile is unset" in text
    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "set_shop_profile" in names
    assert "get_shop_profile" in names
