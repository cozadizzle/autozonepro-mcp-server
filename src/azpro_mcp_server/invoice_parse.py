"""Parse AutoZone Pro commercial invoice / return PDFs (text-extractable)."""

from __future__ import annotations

import base64
import io
import re
from typing import Any, Dict, List, Optional

from .account import parse_money

_INVOICE_NO = re.compile(
    r"(?:Return\s+Invoice\s+Number|Invoice\s+Number)\s*:\s*([0-9]+)",
    re.I,
)
_ORIGINAL_NO = re.compile(r"Original\s+Invoice\s+Number\s*:\s*([0-9]+)", re.I)
_ORDER_DATE = re.compile(
    r"Order\s+Date\s*:\s*(\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2}\s*[AP]M)?)",
    re.I,
)
_DUE_DATE = re.compile(r"Invoice\s+Due\s+Date\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)
_STORE = re.compile(r"(?:AutoZone\s+)?Store\s+(\d+)", re.I)
_REGISTER = re.compile(r"Register\s+Number\s*:\s*(\S+)", re.I)
_CUSTOMER = re.compile(r"Customer\s*#\s*:\s*(\S+)", re.I)
_SUBTOTAL = re.compile(r"Subtotal\s+(-?\$?-?[\d,]+\.\d{2})", re.I)
_TAX = re.compile(r"\bTax\s+(-?\$?-?[\d,]+\.\d{2})", re.I)
_TOTAL_DUE = re.compile(r"Total\s+Due\s+(-?\$?-?[\d,]+\.\d{2})", re.I)
_FOOTER_AZC = re.compile(
    r"AZC\s+Savings\s+Piece\s+Count\s+Page\s+Total\s+"
    r"(-?\$?-?[\d,]+\.\d{2})\s+(\d+)\s+\d+\s+of\s+\d+\s+(-?\$?-?[\d,]+\.\d{2})",
    re.I,
)
_FOOTER_PIECE = re.compile(
    r"Piece\s+Count\s+Page\s+Total\s+"
    r"(-?\$?-?[\d,]+\.\d{2}\s+)?(\d+)\s+\d+\s+of\s+\d+\s+(-?\$?-?[\d,]+\.\d{2})",
    re.I,
)
_BARCODE_ID = re.compile(r"\b(\d{11,20})C\b")
_SKU = re.compile(r"SKU-(\d+)", re.I)
_MONEY4 = re.compile(
    r"^(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})$"
)
_VEHICLE = re.compile(r"^(\d{4}\s+[A-Za-z].+)$")
_QTY_DESC = re.compile(r"^(-?\d+)\s+(.+)$")
_MONEY = r"(-?[\d,]+\.\d{2})"
_INLINE_ITEM = re.compile(
    rf"^(?:UR|O)?\s*(\S+)\s+(-?\d+)\s+(.+?)\s+{_MONEY}\s+{_MONEY}\s+{_MONEY}\s+{_MONEY}$",
    re.I,
)
_QTY_DESC_MONEY = re.compile(
    rf"^(-?\d+)\s+(.+?)\s+{_MONEY}\s+{_MONEY}\s+{_MONEY}\s+{_MONEY}$"
)
_RETURN_LINE = re.compile(
    r"^(?:UR|O)\s+(\S+)\s+(-?\d+)\s+(.+)$",
    re.I,
)
_PART_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,24}$")
_STOP_ITEMS = re.compile(
    r"^(For assistance|MSDS can be ordered|Subtotal\b|cmst)",
    re.I,
)


def invoice_date_from_id(invoice_id: str) -> Optional[str]:
    """AZ invoiceId is often {invoiceNumber}{MMDDYY}."""
    s = (invoice_id or "").strip()
    if len(s) < 6 or not s[-6:].isdigit():
        return None
    mm, dd, yy = s[-6:-4], s[-4:-2], s[-2:]
    try:
        month, day, year = int(mm), int(dd), 2000 + int(yy)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return None


def to_date_only(value: Any) -> Optional[str]:
    """ISO or date-only → YYYY-MM-DD (same rule as AZ Pro UI)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    if re.match(r"^\d{4}-\d{2}-\d{2}T", s):
        return s[:10]
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # MDY when first > 12 would be invalid; AZ uses MDY on the PDF.
        month, day = a, b
        if month > 12 and day <= 12:
            month, day = b, a
        return f"{y:04d}-{month:02d}-{day:02d}"
    return None


def decode_pdf_payload(payload: Any) -> bytes:
    """JSON `{data: base64}` or raw PDF bytes → PDF bytes."""
    if isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
        if raw.startswith(b"%PDF"):
            return raw
        try:
            return base64.b64decode(raw)
        except Exception as e:
            raise ValueError(f"not a PDF payload: {e}") from e
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            data = data.get("content") or data.get("base64") or data.get("data")
        if isinstance(data, str):
            return base64.b64decode(data)
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
    if isinstance(payload, str):
        s = payload.strip()
        if s.startswith("%PDF"):
            return s.encode("latin-1")
        return base64.b64decode(s)
    raise ValueError("unsupported PDF payload")


def extract_pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts: List[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _first(rx: re.Pattern[str], text: str) -> Optional[str]:
    m = rx.search(text)
    return m.group(1).strip() if m else None


def _doc_kind(text: str) -> str:
    if re.search(r"Commercial\s+Return", text, re.I):
        return "RETURN"
    if re.search(r"Commercial\s+Invoice", text, re.I):
        return "INVOICE"
    if re.search(r"\bPayment\b", text, re.I) and "Invoice Number" not in text:
        return "PAYMENT"
    return "UNKNOWN"


def _flush(cur: Optional[dict], lines_out: List[dict], vehicle: Optional[str]) -> None:
    if not cur or not cur.get("part_number"):
        return
    desc = re.sub(r"\s+", " ", (cur.get("description") or "")).strip()
    sku_m = _SKU.search(desc)
    sku = cur.get("sku") or (sku_m.group(1) if sku_m else None)
    if sku_m:
        desc = _SKU.sub("", desc).strip()
    lines_out.append(
        {
            "part_number": cur.get("part_number"),
            "line_code": cur.get("line_code"),
            "sku": sku,
            "qty": cur.get("qty"),
            "description": desc or None,
            "list_price": cur.get("list_price"),
            "cost": cur.get("cost"),
            "core": cur.get("core"),
            "total": cur.get("total"),
            "vehicle": cur.get("vehicle") or vehicle,
        }
    )


def _parse_items(text: str) -> tuple[List[dict], Optional[str]]:
    raw_lines = [ln.strip() for ln in text.splitlines()]
    start = None
    for i, ln in enumerate(raw_lines):
        if ln.lower() == "items" or ln.lower().startswith("items"):
            start = i + 1
            break
    if start is None:
        return [], None

    vehicle: Optional[str] = None
    items: List[dict] = []
    cur: Optional[dict] = None
    i = start
    while i < len(raw_lines):
        ln = raw_lines[i]
        if not ln:
            i += 1
            continue
        if _STOP_ITEMS.search(ln):
            break
        if re.match(r"^(?:O\s+)?Part\s*#", ln, re.I):
            i += 1
            continue
        if re.match(r"^No vehicle given", ln, re.I):
            vehicle = None
            i += 1
            continue
        if _VEHICLE.match(ln) and not _MONEY4.match(ln):
            vehicle = ln
            i += 1
            continue

        inline = _INLINE_ITEM.match(ln)
        if inline:
            _flush(cur, items, vehicle)
            desc = inline.group(3)
            sku_m = _SKU.search(desc)
            items.append(
                {
                    "part_number": inline.group(1),
                    "line_code": None,
                    "sku": sku_m.group(1) if sku_m else None,
                    "qty": int(inline.group(2)),
                    "description": _SKU.sub("", desc).strip() or None,
                    "list_price": parse_money(inline.group(4)),
                    "cost": parse_money(inline.group(5)),
                    "core": parse_money(inline.group(6)),
                    "total": parse_money(inline.group(7)),
                    "vehicle": vehicle,
                }
            )
            cur = None
            i += 1
            continue

        qdm = _QTY_DESC_MONEY.match(ln)
        if qdm and cur and cur.get("part_number"):
            desc = qdm.group(2)
            sku_m = _SKU.search(desc)
            cur["qty"] = int(qdm.group(1))
            cur["description"] = desc
            if sku_m:
                cur["sku"] = sku_m.group(1)
            cur["list_price"] = parse_money(qdm.group(3))
            cur["cost"] = parse_money(qdm.group(4))
            cur["core"] = parse_money(qdm.group(5))
            cur["total"] = parse_money(qdm.group(6))
            _flush(cur, items, vehicle)
            cur = None
            i += 1
            continue

        if ln.upper().startswith("DEAL:") and cur:
            cur["description"] = ((cur.get("description") or "") + " " + ln).strip()
            i += 1
            continue

        ret = _RETURN_LINE.match(ln)
        if ret:
            _flush(cur, items, vehicle)
            desc = ret.group(3)
            sku_m = _SKU.search(desc)
            cur = {
                "part_number": ret.group(1),
                "qty": int(ret.group(2)),
                "description": desc,
                "sku": sku_m.group(1) if sku_m else None,
                "vehicle": vehicle,
            }
            i += 1
            continue

        money = _MONEY4.match(ln)
        if money and cur:
            cur["list_price"] = parse_money(money.group(1))
            cur["cost"] = parse_money(money.group(2))
            cur["core"] = parse_money(money.group(3))
            cur["total"] = parse_money(money.group(4))
            _flush(cur, items, vehicle)
            cur = None
            i += 1
            continue

        sku_only = _SKU.fullmatch(ln) or (ln.upper().startswith("SKU-") and _SKU.search(ln))
        if sku_only and cur:
            m = _SKU.search(ln)
            if m:
                cur["sku"] = m.group(1)
            i += 1
            continue

        qd = _QTY_DESC.match(ln)
        if qd and cur and cur.get("part_number") and cur.get("qty") is None:
            cur["qty"] = int(qd.group(1))
            cur["description"] = qd.group(2)
            i += 1
            continue

        if cur and cur.get("part_number") and cur.get("qty") is not None and not money:
            # description continuation
            if not _PART_TOKEN.match(ln):
                cur["description"] = ((cur.get("description") or "") + " " + ln).strip()
                i += 1
                continue

        if cur and cur.get("part_number") and cur.get("qty") is None and _PART_TOKEN.match(ln):
            if ln != cur.get("part_number"):
                cur["line_code"] = ln
            i += 1
            continue

        if _PART_TOKEN.match(ln):
            _flush(cur, items, vehicle)
            cur = {"part_number": ln, "vehicle": vehicle}
            i += 1
            continue

        i += 1

    _flush(cur, items, vehicle)
    return items, vehicle


def parse_invoice_text(text: str) -> Dict[str, Any]:
    """Turn extracted PDF text into a structured invoice (no network)."""
    text = text or ""
    kind = _doc_kind(text)
    lines, vehicle = _parse_items(text)
    invoice_number = _first(_INVOICE_NO, text)
    barcode = _first(_BARCODE_ID, text)
    invoice_id = barcode[:-1] if barcode and barcode.endswith("C") else barcode
    subtotal = parse_money(_first(_SUBTOTAL, text))
    tax = parse_money(_first(_TAX, text))
    total = parse_money(_first(_TOTAL_DUE, text))
    if total is None:
        # footer "Total" last money on the page
        moneys = re.findall(r"(-?\$?-?[\d,]+\.\d{2})", text)
        if moneys:
            total = parse_money(moneys[-1])
    azc = None
    piece = None
    footer_azc = _FOOTER_AZC.search(text)
    if footer_azc:
        azc = parse_money(footer_azc.group(1))
        piece = int(footer_azc.group(2))
        if total is None:
            total = parse_money(footer_azc.group(3))
    else:
        footer_pc = _FOOTER_PIECE.search(text)
        if footer_pc:
            piece = int(footer_pc.group(2))
            if total is None:
                total = parse_money(footer_pc.group(3))
    return {
        "ok": bool(invoice_number or lines or total is not None),
        "kind": kind,
        "invoice_number": invoice_number,
        "invoice_id": invoice_id,
        "original_invoice_number": _first(_ORIGINAL_NO, text),
        "order_date": _first(_ORDER_DATE, text),
        "due_date": _first(_DUE_DATE, text),
        "store_number": _first(_STORE, text),
        "register_number": _first(_REGISTER, text),
        "customer_number": _first(_CUSTOMER, text),
        "vehicle": vehicle or next((ln.get("vehicle") for ln in lines if ln.get("vehicle")), None),
        "lines": lines,
        "line_count": len(lines),
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "azc_savings": azc,
        "piece_count": piece,
        "source": "pdf",
    }


def parse_invoice_pdf(pdf_bytes: bytes) -> Dict[str, Any]:
    parsed = parse_invoice_text(extract_pdf_text(pdf_bytes))
    parsed["pdf_bytes"] = len(pdf_bytes)
    return parsed
