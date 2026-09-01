"""Search and period totals over AutoZone Pro transaction rows (pure)."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .account import INVOICE_TYPE_ALIASES, parse_money

MONTH_NAMES = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def parse_item_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        month, day = a, b
        if month > 12:
            month, day = b, a
        try:
            return date(y, month, day)
        except ValueError:
            return None
    return None


def period_key(d: date, period: str) -> str:
    p = (period or "month").strip().lower()
    if p in ("year", "yearly", "annual"):
        return f"{d.year:04d}"
    if p in ("day", "daily"):
        return d.isoformat()
    return f"{d.year:04d}-{d.month:02d}"


def in_window(
    d: date,
    *,
    year: Optional[int] = None,
    month: Optional[int] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> bool:
    if year and d.year != int(year):
        return False
    if month and d.month != int(month):
        return False
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def item_haystack(item: Dict[str, Any], parsed: Optional[Dict[str, Any]] = None) -> str:
    parts = [
        item.get("id"),
        item.get("invoice_id"),
        item.get("po"),
        item.get("part_number"),
        item.get("vehicle"),
        item.get("type"),
        item.get("status"),
    ]
    if parsed:
        parts.append(parsed.get("invoice_number"))
        parts.append(parsed.get("vehicle"))
        for ln in parsed.get("lines") or []:
            parts.extend(
                [ln.get("part_number"), ln.get("line_code"), ln.get("description"), ln.get("sku")]
            )
    return " ".join(str(p) for p in parts if p).lower()


def match_query(item: Dict[str, Any], query: str, parsed: Optional[Dict[str, Any]] = None) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    return q in item_haystack(item, parsed)


def tally_items(
    items: Iterable[Dict[str, Any]],
    *,
    period: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
    invoice_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Bucket signed ticket totals (scan amounts). Returns do not include PDF lines."""
    itype = (invoice_type or "").strip().upper()
    buckets: Dict[str, Dict[str, Any]] = {}
    skipped = 0
    matched = 0
    grand = 0.0
    by_type: Dict[str, float] = {}
    for item in items:
        d = parse_item_date(item.get("date"))
        if d is None or not in_window(d, year=year, month=month, start=start, end=end):
            skipped += 1
            continue
        row_type = (item.get("type") or item.get("status") or "").upper() or "UNKNOWN"
        row_cd = (item.get("type_cd") or "").upper()
        if itype and itype not in ("ALL", "*"):
            want = INVOICE_TYPE_ALIASES.get(itype, itype)
            if row_type != itype and row_cd != itype and row_cd != want and row_type != want:
                skipped += 1
                continue
        amt = item.get("amount")
        if not isinstance(amt, (int, float)):
            amt = parse_money(amt) or 0.0
        amt = float(amt)
        key = period_key(d, period)
        b = buckets.setdefault(
            key,
            {
                "period": key,
                "count": 0,
                "total": 0.0,
                "invoices": 0.0,
                "returns": 0.0,
                "payments": 0.0,
                "other": 0.0,
                "n": 0,
            },
        )
        b["count"] += 1
        b["n"] += 1
        b["total"] += amt
        if row_type == "INVOICE":
            b["invoices"] += amt
        elif row_type == "RETURN":
            b["returns"] += amt
        elif row_type == "PAYMENT":
            b["payments"] += amt
        else:
            b["other"] += amt
        by_type[row_type] = by_type.get(row_type, 0.0) + amt
        grand += amt
        matched += 1

    rows = [buckets[k] for k in sorted(buckets)]
    for b in rows:
        for fld in ("total", "invoices", "returns", "payments", "other"):
            b[fld] = round(float(b[fld]), 2)
    return {
        "ok": True,
        "period": period,
        "count": matched,
        "skipped": skipped,
        "grand_total": round(grand, 2),
        "by_type": {k: round(v, 2) for k, v in sorted(by_type.items())},
        "buckets": rows,
        "source": "transactions",
    }


def search_items(
    items: Iterable[Dict[str, Any]],
    query: str,
    *,
    limit: int = 15,
) -> List[Dict[str, Any]]:
    cap = max(1, min(int(limit or 15), 50))
    q = (query or "").strip()
    out: List[Dict[str, Any]] = []
    for item in items:
        if match_query(item, q):
            out.append(item)
            if len(out) >= cap:
                break
    return out


def year_month_window(year: int, month: Optional[int] = None) -> Tuple[date, date]:
    y = int(year)
    if month:
        m = int(month)
        last = calendar.monthrange(y, m)[1]
        return date(y, m, 1), date(y, m, last)
    return date(y, 1, 1), date(y, 12, 31)
