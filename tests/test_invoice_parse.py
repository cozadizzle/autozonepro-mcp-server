"""Invoice PDF parse + tally (synthetic fixtures only — no live shop PDFs)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import NameObject

from azpro_mcp_server.account import (
    normalize_invoice_type,
    parse_annual_sales,
    parse_transaction_list,
)
from azpro_mcp_server.invoice_parse import (
    decode_pdf_payload,
    extract_pdf_text,
    invoice_date_from_id,
    parse_invoice_pdf,
    parse_invoice_text,
    to_date_only,
)
from azpro_mcp_server.invoice_tally import search_items, tally_items
from azpro_mcp_server.models import InvoiceHit, InvoiceLine, ParsedInvoice
from azpro_mcp_server.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"


def _txt(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _minimal_pdf(text: str) -> bytes:
    """One-page PDF with Helvetica text (pypdf-extractable)."""
    # Build via pypdf canvas-less: wrap existing writer + page content stream.
    from pypdf.generic import ArrayObject, DictionaryObject, DecodedStreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    lines = text.splitlines()
    y = 760
    cmds = ["BT", "/F1 10 Tf"]
    for ln in lines[:80]:
        safe = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        cmds.append(f"1 0 0 1 36 {y} Tm ({safe}) Tj")
        y -= 12
        if y < 40:
            break
    cmds.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(cmds).encode("latin-1", errors="replace"))
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    resources = DictionaryObject()
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = font
    resources[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = stream
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_parse_synthetic_invoice_lines_and_totals():
    parsed = parse_invoice_text(_txt("synthetic_invoice.txt"))
    assert parsed["ok"] is True
    assert parsed["kind"] == "INVOICE"
    assert parsed["invoice_number"] == "00000000001"
    assert parsed["invoice_id"] == "00000000001011526"
    assert parsed["store_number"] == "00000"
    assert parsed["vehicle"] == "2019 Example Make Model"
    assert parsed["subtotal"] == 138.13
    assert parsed["tax"] == 9.67
    assert parsed["total"] == 147.80
    assert parsed["azc_savings"] == 8.47
    assert parsed["piece_count"] == 4
    assert parsed["line_count"] == 3
    first = parsed["lines"][0]
    assert first["part_number"] == "604-970"
    assert first["line_code"] == "BDAC1267"
    assert first["sku"] == "000395135"
    assert first["qty"] == 1
    assert first["cost"] == 34.64
    assert first["list_price"] == 61.31
    assert first["total"] == 34.64
    second = parsed["lines"][1]
    assert second["part_number"] == "773775"
    assert second["qty"] == 2
    assert second["total"] == 71.26
    snack = parsed["lines"][2]
    assert snack["part_number"] == "JLB88967"
    assert snack["qty"] == 1
    assert snack["cost"] == 3.19
    assert snack["total"] == 3.19
    InvoiceLine.model_validate(first)
    ParsedInvoice.model_validate(
        {k: parsed[k] for k in ParsedInvoice.model_fields if k in parsed}
    )


def test_parse_synthetic_return_negative_qty():
    parsed = parse_invoice_text(_txt("synthetic_return.txt"))
    assert parsed["kind"] == "RETURN"
    assert parsed["invoice_number"] == "00000000002"
    assert parsed["original_invoice_number"] == "00000000001"
    assert parsed["total"] == -56.71
    assert parsed["line_count"] == 1
    ln = parsed["lines"][0]
    assert ln["part_number"] == "43166DG"
    assert ln["qty"] == -1
    assert ln["cost"] == 53.00
    assert ln["total"] == -53.00


def test_parse_invoice_pdf_roundtrip_synthetic():
    raw = _minimal_pdf(_txt("synthetic_invoice.txt"))
    assert raw.startswith(b"%PDF")
    parsed = parse_invoice_pdf(raw)
    assert parsed["ok"] is True
    assert parsed["invoice_number"] == "00000000001"
    assert parsed["line_count"] >= 1
    # extract_text should not crash
    assert "Commercial Invoice" in extract_pdf_text(raw)


def test_decode_pdf_payload_base64_and_raw():
    raw = b"%PDF-1.4 fake"
    import base64

    wrapped = {"data": base64.b64encode(raw).decode("ascii")}
    assert decode_pdf_payload(wrapped).startswith(b"%PDF")
    assert decode_pdf_payload(raw).startswith(b"%PDF")


def test_invoice_date_from_id_and_to_date_only():
    assert invoice_date_from_id("00000000001011526") == "2026-01-15"
    assert to_date_only("2026-08-24T17:07:31Z") == "2026-08-24"
    assert to_date_only("01/15/2026 02:58 PM") == "2026-01-15"


def test_tally_monthly_and_search():
    fixture = json.loads((FIXTURES / "transactions.json").read_text(encoding="utf-8"))
    items = parse_transaction_list(fixture, limit=15)["items"]
    tallied = tally_items(items, period="month")
    assert tallied["count"] == len(items)
    assert tallied["buckets"]
    assert any(b["period"].startswith("2026-01") for b in tallied["buckets"])
    hits = search_items(items, "TESTPN1")
    assert hits and hits[0]["part_number"] == "TESTPN1"
    InvoiceHit.model_validate(hits[0])


def test_parse_annual_sales_fixture():
    fixture = json.loads((FIXTURES / "annual_sales.json").read_text(encoding="utf-8"))
    parsed = parse_annual_sales(fixture)
    assert parsed["ok"] is True
    assert parsed["year"] == 2026
    assert parsed["total_sales"] == 300.5
    assert parsed["excludes_core"] is True
    assert parsed["monthly"][0]["label"] == "January"


def test_normalize_invoice_type_ui_aliases():
    assert normalize_invoice_type("INVOICE") == "CMSTINVC"
    assert normalize_invoice_type("return") == "CMSTRETN"
    assert normalize_invoice_type("ALL") is None


def test_mcp_registers_invoice_tools():
    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "get_invoice" in names
    assert "search_invoices" in names
    assert "tally_invoices" in names
    assert "get_annual_purchases" in names
