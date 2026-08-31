"""Pure JSON → AutoZone Pro credit snapshot and invoice/receipt rows (no HTTP)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_SCAN_LIMIT = 15
MAX_SCAN_LIMIT = 50


def parse_money(value: Any) -> Optional[float]:
    """Parse AZ Pro money (`$1,009.71`, `-$500.00`, `(12.00)`, `158.33`) to float."""
    if value is None or value is True or value is False:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or s in ("-", "--", "N/A", "n/a"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("$", "").replace(",", "").replace("(", "").replace(")", "").strip()
    if not s or s in ("-", "--"):
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    if neg:
        n = -abs(n)
    return n


def _first_money(src: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        if k not in src or src.get(k) in (None, ""):
            continue
        parsed = parse_money(src.get(k))
        if parsed is not None:
            return parsed
    return None


def parse_credit_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Map `/ecomm/b2b/v1/payments/customer/credit-info` JSON to numeric account fields.

    AutoZone Pro UI labels: total balance, past due, available credit (open-to-buy).
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid credit payload"}
    src: Any = payload
    if isinstance(payload.get("data"), dict) and "currentBalance" not in payload:
        src = payload["data"]
    if isinstance(src.get("creditInfo"), dict):
        src = src["creditInfo"]
    if not isinstance(src, dict):
        return {"ok": False, "error": "invalid credit payload"}

    overdue = _first_money(src, "delinquentAmt", "overdue", "pastDue")
    available = _first_money(src, "openToBuy", "availableCredit")
    balance = _first_money(src, "currentBalance")
    return {
        "ok": balance is not None or available is not None or overdue is not None,
        "balance": balance,
        "overdue": overdue,
        "past_due": overdue,
        "available_credit": available,
        "credit_limit": _first_money(src, "creditLimit"),
        "amt_due_current": _first_money(src, "amtDueCurrent"),
        "last_payment_amount": _first_money(src, "lastPaymentAmt", "lastPayment"),
        "last_payment_date": src.get("lastPaymentDate") or None,
        "credit_status": src.get("creditStatus"),
        "account_type": src.get("accountTypeDesc"),
        "delinquent_days": src.get("delinquentDays"),
        "source": "payments/customer/credit-info",
    }


def _clamp_limit(limit: Any, default: int = DEFAULT_SCAN_LIMIT) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, MAX_SCAN_LIMIT))


def parse_transaction_list(payload: Dict[str, Any], limit: int = DEFAULT_SCAN_LIMIT) -> Dict[str, Any]:
    """Map `/ecomm/b2b/v1/transactions` JSON to a bounded invoice/receipt list."""
    cap = _clamp_limit(limit)
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "items": [],
            "count": 0,
            "has_next_page": False,
            "limit": cap,
            "error": "invalid transactions payload",
            "source": "transactions",
        }
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        data = {}
    rows = data.get("invoices") or data.get("transactions") or []
    if not isinstance(rows, list):
        rows = []

    items: List[Dict[str, Any]] = []
    for row in rows[:cap]:
        if not isinstance(row, dict):
            continue
        itype = (row.get("invoiceTypeName") or row.get("invoiceTypeCD") or "") or None
        vehicles = row.get("vehicles") or []
        vehicle = None
        if isinstance(vehicles, list):
            vehicle = next((str(v).strip() for v in vehicles if str(v).strip()), None)
        po = str(row.get("purchaseOrderNumber") or "").strip() or None
        pn = str(row.get("partNumber") or "").strip() or None
        ident = str(row.get("invoiceNumber") or row.get("invoiceId") or "").strip() or None
        items.append(
            {
                "id": ident,
                "invoice_id": str(row.get("invoiceId") or "") or None,
                "date": row.get("invoiceDate") or row.get("submittedDate"),
                "amount": parse_money(row.get("totalAmt")),
                "type": itype,
                "status": itype,
                "store_number": str(row.get("storeNumber") or "") or None,
                "po": po,
                "part_number": pn,
                "vehicle": vehicle,
            }
        )
    return {
        "ok": bool(items),
        "items": items,
        "count": len(items),
        "has_next_page": bool(data.get("hasNextPage")),
        "limit": cap,
        "source": "transactions",
    }
