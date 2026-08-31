"""Unit tests for credit snapshot + invoice scan (no live AutoZone credentials)."""

from __future__ import annotations

import json
from pathlib import Path

from azpro_mcp_server.account import parse_credit_snapshot, parse_money, parse_transaction_list
from azpro_mcp_server.client import AzProClient
from azpro_mcp_server.models import CreditSnapshot, InvoiceHit
from azpro_mcp_server.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_parse_money_az_formats():
    fixture = _load("credit_info.json")
    assert parse_money(fixture["currentBalance"]) == 1234.56
    assert parse_money(fixture["lastPaymentAmt"]) == -50.0
    assert parse_money(fixture["openToBuy"]) == 98.76
    tx = _load("transactions.json")
    assert parse_money(tx["data"]["invoices"][1]["totalAmt"]) == -5.0


def test_parse_credit_snapshot_from_fixture():
    fixture = _load("credit_info.json")
    snap = parse_credit_snapshot(fixture)
    assert snap["ok"] is True
    assert snap["balance"] == parse_money(fixture["currentBalance"])
    assert snap["overdue"] == parse_money(fixture["delinquentAmt"])
    assert snap["past_due"] == snap["overdue"]
    assert snap["available_credit"] == parse_money(fixture["openToBuy"])
    assert snap["credit_limit"] == parse_money(fixture["creditLimit"])
    CreditSnapshot.model_validate(
        {k: snap[k] for k in CreditSnapshot.model_fields if k in snap}
    )


def test_parse_transaction_list_limit_and_fields():
    fixture = _load("transactions.json")
    raw_n = len(fixture["data"]["invoices"])
    assert raw_n >= 2
    bounded = parse_transaction_list(fixture, limit=2)
    assert bounded["limit"] == 2
    assert bounded["count"] == 2
    assert len(bounded["items"]) == 2
    assert bounded["has_next_page"] is True

    full = parse_transaction_list(fixture, limit=15)
    assert full["count"] == raw_n
    first_raw = fixture["data"]["invoices"][0]
    first = full["items"][0]
    assert first["id"] == first_raw["invoiceNumber"]
    assert first["date"] == first_raw["invoiceDate"]
    assert first["amount"] == parse_money(first_raw["totalAmt"])
    assert first["type"] == first_raw["invoiceTypeName"]
    assert first["status"] == first_raw["invoiceTypeName"]
    InvoiceHit.model_validate(first)


def _install_fake_http(client: AzProClient, monkeypatch, *, credit, tx, pin="99999999"):
    captured = {"credit_params": None, "tx_params": None}

    def fake_get(url, params=None, timeout=None, headers=None, **kwargs):
        u = str(url)
        if u.endswith("/api/v2/session"):
            return FakeResp({"status": "authenticated"})
        if "sites/header" in u:
            return FakeResp(
                {
                    "userInfo": {"currentPin": pin, "userName": "testshop"},
                    "currentStore": {"storeNumber": "0000"},
                    "currentVehicle": {},
                    "shopInfos": [{"accountNumber": int(pin)}],
                }
            )
        if "payments/customer/credit-info" in u:
            captured["credit_params"] = dict(params or {})
            return FakeResp(credit)
        if u.rstrip("/").endswith("/transactions") or "/transactions?" in u:
            captured["tx_params"] = dict(params or {})
            return FakeResp(tx)
        return FakeResp({"error": u}, 404)

    monkeypatch.setattr(client._session, "get", fake_get)
    monkeypatch.setattr(
        client,
        "_cookie",
        lambda name: "tok" if name == "access_token" else None,
    )
    return captured


def test_client_get_credit_snapshot_uses_shipped_parser(monkeypatch):
    fixture = _load("credit_info.json")
    client = AzProClient(cookies={})
    _install_fake_http(client, monkeypatch, credit=fixture, tx=_load("transactions.json"))
    out = client.get_credit_snapshot()
    expected = parse_credit_snapshot(fixture)
    assert out["ok"] is True
    assert out["logged_in"] is True
    assert out["balance"] == expected["balance"]
    assert out["overdue"] == expected["overdue"]
    assert out["available_credit"] == expected["available_credit"]
    assert out["balance"] == parse_money(fixture["currentBalance"])


def test_client_scan_invoices_honors_limit_and_page_size(monkeypatch):
    fixture = _load("transactions.json")
    client = AzProClient(cookies={})
    captured = _install_fake_http(
        client, monkeypatch, credit=_load("credit_info.json"), tx=fixture
    )
    out = client.scan_invoices(limit=2, days=90)
    assert captured["tx_params"]["pageSize"] == 2
    assert captured["tx_params"]["pin"] == "99999999"
    assert "startDate" in captured["tx_params"]
    assert "endDate" in captured["tx_params"]
    assert out["ok"] is True
    assert out["count"] == 2
    assert out["limit"] == 2
    assert len(out["items"]) == 2
    first_raw = fixture["data"]["invoices"][0]
    assert out["items"][0]["id"] == first_raw["invoiceNumber"]
    assert out["items"][0]["amount"] == parse_money(first_raw["totalAmt"])
    assert out["items"][0]["type"] == first_raw["invoiceTypeName"]


def test_mcp_registers_account_tools():
    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "get_credit_snapshot" in names
    assert "scan_invoices" in names
