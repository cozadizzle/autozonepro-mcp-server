"""Local shop identity for any garage using this MCP (not committed, not uploaded)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

SHOP_KEYS = ("garage_name", "store_number", "address", "city", "state", "zip", "phone")

DEFAULT_SHOP_FILE = Path.home() / ".config" / "autozonepro" / "shop.json"


def shop_file_path() -> Path:
    override = os.getenv("AZPRO_SHOP_FILE")
    if override:
        return Path(override)
    return DEFAULT_SHOP_FILE


def _blank() -> Dict[str, str]:
    return {k: "" for k in SHOP_KEYS}


def load_shop_profile() -> Dict[str, str]:
    path = shop_file_path()
    out = _blank()
    try:
        if not path.is_file():
            return out
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return out
        for k in SHOP_KEYS:
            v = raw.get(k)
            if v is None:
                continue
            out[k] = str(v).strip()
    except Exception:
        return out
    return out


def save_shop_profile(data: Dict[str, Any], *, clobber_empty: bool = False) -> Dict[str, str]:
    """Write shop profile. Default is merge: blank/omitted fields do not wipe existing values.

    git pull of this repo never touches this file (it lives under ~/.config).
    """
    path = shop_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = load_shop_profile() if path.is_file() else _blank()
    incoming = data or {}
    for k in SHOP_KEYS:
        if k not in incoming:
            continue
        v = incoming.get(k)
        s = "" if v is None else str(v).strip()
        if s:
            cleaned[k] = s
        elif clobber_empty:
            cleaned[k] = ""
    path.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return cleaned


def bootstrap_from_session(status: Dict[str, Any]) -> Dict[str, str]:
    """Fill empty profile fields from a live AZ session. Never overwrites a filled field."""
    existing = load_shop_profile()
    sug = suggest_from_session(status)
    to_fill = {k: sug.get(k) or "" for k in SHOP_KEYS if not (existing.get(k) or "").strip() and (sug.get(k) or "").strip()}
    if not to_fill:
        return existing
    return save_shop_profile(to_fill)


def profile_complete(data: Optional[Dict[str, str]] = None) -> bool:
    p = data if data is not None else load_shop_profile()
    return bool((p.get("garage_name") or "").strip())


def format_shop_line(data: Optional[Dict[str, str]] = None) -> str:
    p = data if data is not None else load_shop_profile()
    name = (p.get("garage_name") or "").strip()
    if not name:
        return ""
    bits = [name]
    store = (p.get("store_number") or "").strip()
    if store:
        bits.append(f"AZ store {store}")
    loc = ", ".join(x for x in (p.get("city"), p.get("state")) if x)
    if loc:
        bits.append(loc)
    return " · ".join(bits)


def suggest_from_session(status: Dict[str, Any]) -> Dict[str, str]:
    """Prefill from AutoZone Pro header/shops when the user is logged in."""
    out = _blank()
    if not isinstance(status, dict):
        return out
    shop = status.get("shop") if isinstance(status.get("shop"), dict) else {}
    store = status.get("current_store") if isinstance(status.get("current_store"), dict) else {}
    ship = shop.get("shippingAddress") if isinstance(shop.get("shippingAddress"), dict) else {}

    out["garage_name"] = str(shop.get("shopName") or shop.get("name") or "").strip()
    out["store_number"] = str(
        store.get("number") or store.get("storeNumber") or shop.get("storeNumber") or ""
    ).strip()
    out["address"] = str(
        shop.get("address1")
        or ship.get("address1")
        or store.get("address")
        or ""
    ).strip()
    out["city"] = str(shop.get("city") or ship.get("city") or store.get("city") or "").strip()
    out["state"] = str(shop.get("state") or ship.get("state") or store.get("state") or "").strip()
    out["zip"] = str(
        shop.get("postalCode") or ship.get("postalCode") or store.get("zip") or ""
    ).strip()
    return out


def prompt_shop_profile(
    suggested: Optional[Dict[str, str]] = None,
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
) -> Dict[str, str]:
    """Interactive first-run prompt. Prefills from AZ session when provided."""
    s = suggest_from_session(suggested) if suggested and "shop" in (suggested or {}) else dict(
        suggested or {}
    )
    merged = _blank()
    merged.update({k: str(s.get(k) or "").strip() for k in SHOP_KEYS if k in s})

    print_fn("")
    print_fn("AutoZone Pro MCP — shop profile")
    print_fn("Saved only on this machine (~/.config/autozonepro/shop.json). Not uploaded.")
    print_fn("Used so quotes and account tools label YOUR garage. Enter to keep a suggestion.")
    print_fn("")

    labels = {
        "garage_name": "Garage / shop name",
        "store_number": "AutoZone store number",
        "address": "Street address",
        "city": "City",
        "state": "State",
        "zip": "ZIP",
        "phone": "Phone (optional)",
    }
    for key in SHOP_KEYS:
        default = merged.get(key) or ""
        hint = f" [{default}]" if default else ""
        raw = input_fn(f"{labels[key]}{hint}: ")
        val = (raw or "").strip()
        merged[key] = val or default
    return save_shop_profile(merged)


def stdin_is_interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False
