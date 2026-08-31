"""AutoZone Pro MCP entrypoint (FastMCP, stdio)."""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import os
import sys
import time
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from .client import AzProClient, client_from_env
from .models import ToolEnvelope
from .shop_profile import (
    bootstrap_from_session,
    format_shop_line,
    load_shop_profile,
    profile_complete,
    prompt_shop_profile,
    save_shop_profile,
    stdin_is_interactive,
    suggest_from_session,
)

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="azpro-mcp")
TOOL_TIMEOUT = float(os.getenv("AZPRO_TIMEOUT", "60"))


def build_instructions() -> str:
    shop = load_shop_profile()
    if profile_complete(shop):
        shop_bit = (
            f"Active shop profile: {format_shop_line(shop)}. "
            "Use get_shop_profile for name/store/address. "
        )
    else:
        shop_bit = (
            "Shop profile is unset. On first use, ask the user for garage name, AutoZone store "
            "number, and address (prefill from get_session_status suggested_shop when logged in) "
            "and call set_shop_profile. Do not invent a shop identity. "
        )
    return (
        "This MCP server connects to the signed-in AutoZone Pro commercial account "
        "(www.autozonepro.com) for whatever garage is using it. "
        + shop_bit
        + "Use it for shop parts lookup, license plate decode, VIN decode, saved vehicles, commercial cost + list "
        "pricing, session/store info, and read-only account credit + invoices/receipts. "
        "ACCOUNT (read-only, never pay bills): get_credit_snapshot for balance, overdue/past-due, "
        "and available credit (one small call). scan_invoices(limit=15, days=90) for a bounded "
        "transaction-history list (id, date, amount, type) — do not dump unbounded history or PDFs. "
        "QUOTE RULE (mandatory): every parts quote shown to the user MUST include BOTH "
        "commercial cost AND list_price side-by-side (per line + parts subtotal + job total). "
        "Never present cost alone. Cost = what the shop pays; list = AZ list/street. "
        "Sort picks by lowest cost, but always display list next to cost. Labor (ALLDATA) "
        "is the same under both columns. Prefer shop-quote skill for full RO format. "
        "Workflow: get_session_status to confirm auth. "
        "VEHICLE BIND (mandatory before parts): plate → lookup_plate; VIN → lookup_vin; "
        "YMME only → set_vehicle_ymme(year,make,model,engine=…) or bind_garage_vehicle. "
        "NEVER search_parts on sticky/previous vehicle. Pass expect_vehicle='2016 Lincoln MKZ' "
        "to search_parts to hard-fail on mismatch. "
        "search_parts(query, include_prices=True, position='Front'|'Rear') for vehicle-bound SKUs "
        "sorted by commercial cost (each hit has cost + list_price); or list_group_products "
        "(part_group_id, position=...): azpg4204=pads, azpg1368=rotors, azpg4304=control arms, "
        "azpg1622=oil filter, 12138=motor oil. get_prices(item_ids) for cost/list/core/store qty. "
        "Oil filter multi-group redirects prefer azpg1622 (not motor oil). Viscosity in query "
        "(e.g. 5W-20) filters motor oil SKUs. "
        "Auth: ~/.config/autozonepro_cookies.json + JWT via /api/v2/session. Keep MCP session warm."
    )


def apply_instructions() -> None:
    text = build_instructions()
    mcp.instructions = text
    inner = getattr(mcp, "_mcp_server", None)
    if inner is not None:
        inner.instructions = text


mcp = FastMCP("azpro", instructions=build_instructions())

_client: Optional[AzProClient] = None


def get_client() -> AzProClient:
    global _client
    if _client is None:
        _client = client_from_env()
    return _client


async def _run(fn, timeout: float = TOOL_TIMEOUT) -> Any:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(_EXECUTOR, fn), timeout=timeout)
    except asyncio.TimeoutError:
        return ToolEnvelope(
            ok=False,
            error_code="timeout",
            message=f"Exceeded {timeout:.0f}s",
            elapsed_ms=int(timeout * 1000),
        ).model_dump()


AZPRO_AUTH_LOST_BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║  ⛔ AUTOZONE PRO AUTH LOST / NOT LOGGED IN                        ║
║                                                                  ║
║  Cookies/JWT expired. DO NOT invent prices or fitment.           ║
║  FIX: user logs into autozonepro.com →                             ║
║    azpro__import_browser_cookies(browser='brave'|'chrome'|…)     ║
║  Then get_session_status until logged_in is true.                ║
╚══════════════════════════════════════════════════════════════════╝
""".strip()


@mcp.tool()
async def get_session_status() -> dict:
    """Report AutoZone Pro login, shop, store, and current vehicle.

    If not logged in, message is a big AUTH_LOST banner — print it to the user.
    """

    def _do():
        t0 = time.time()
        data = get_client().get_session_status()
        data["elapsed_ms"] = data.get("elapsed_ms") or int((time.time() - t0) * 1000)
        logged = bool(data.get("logged_in") or data.get("ok"))
        if logged:
            bootstrap_from_session(data)
        shop = load_shop_profile()
        data["shop_profile"] = shop
        data["shop_profile_needed"] = not profile_complete(shop)
        data["suggested_shop"] = suggest_from_session(data) if logged else {}
        if not logged:
            data["auth_lost"] = True
            data["user_visible_banner"] = AZPRO_AUTH_LOST_BANNER
            return ToolEnvelope(
                ok=False,
                error_code="not_logged_in",
                message=AZPRO_AUTH_LOST_BANNER,
                elapsed_ms=data["elapsed_ms"],
                data=data,
            ).model_dump()
        return ToolEnvelope(
            ok=True,
            error_code="ok",
            message=(
                f"{(data.get('user') or {}).get('username') or 'user'} @ "
                f"store {(data.get('current_store') or {}).get('number') or '?'}"
            ),
            elapsed_ms=data["elapsed_ms"],
            data=data,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def list_vehicles() -> dict:
    """List vehicles saved in the AutoZone Pro garage (includes VIN-saved cars)."""

    def _do():
        data = get_client().list_vehicles()
        return ToolEnvelope(
            ok=bool(data.get("ok")),
            error_code="ok" if data.get("ok") else "no_hits",
            message=f"{data.get('count', 0)} vehicles",
            elapsed_ms=data.get("elapsed_ms") or 0,
            data=data,
        ).model_dump()

    return await _run(_do)




@mcp.tool()
async def lookup_plate(plate: str, state: str = "", bind: bool = True) -> dict:
    """Decode a license plate via AutoZone Pro and bind sticky ACES for parts search.

    ALWAYS use this when the user gives a plate/tag — do NOT use the sticky/current
    garage vehicle. state defaults to the local shop profile, else FL.
    bind=True sets in-process ACES so search_parts / list_group_products fit the decoded car.
    """

    def _do():
        st = (state or "").strip() or load_shop_profile().get("state") or "FL"
        data = get_client().lookup_plate(plate, state=st, bind=bind)
        primary = data.get("primary") or {}
        return ToolEnvelope(
            ok=bool(data.get("ok")),
            error_code="ok" if data.get("ok") else "no_hits",
            message=primary.get("label") or data.get("error") or "no match",
            elapsed_ms=data.get("elapsed_ms") or 0,
            data=data,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def lookup_vin(vin: str) -> dict:
    """Decode a 17-char VIN via AutoZone vehicle decoder (YMME + ACES ids)."""

    def _do():
        data = get_client().lookup_vin(vin)
        primary = data.get("primary") or {}
        return ToolEnvelope(
            ok=bool(data.get("ok")),
            error_code="ok" if data.get("ok") else "no_hits",
            message=primary.get("label") or data.get("error") or "no match",
            elapsed_ms=data.get("elapsed_ms") or 0,
            data=data,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def set_vehicle_ymme(
    year: int,
    make: str,
    model: str,
    engine: str = "",
    prefer_garage: bool = True,
) -> dict:
    """Bind catalog vehicle from year/make/model[/engine] (YMME quotes).

    Prefer plate/VIN when available (full ACES). This tool:
    1) tries matching a saved garage vehicle (full fitment), else
    2) parses year/makeId/modelId from catalog search (partial ACES).
    Partial bind can still price known part numbers; vehicle-fit filters may be soft.
    Call BEFORE search_parts when user gave YMM only.
    """

    def _do():
        data = get_client().set_vehicle_ymme(
            year, make, model, engine=engine, prefer_garage=prefer_garage
        )
        return ToolEnvelope(
            ok=bool(data.get("ok")),
            error_code="ok" if data.get("ok") else "invalid_args",
            message=(
                data.get("vehicle_name")
                or data.get("error")
                or "ymme bind failed"
            )
            + (f" [{data.get('fitment_quality')}]" if data.get("fitment_quality") else ""),
            elapsed_ms=data.get("elapsed_ms") or 0,
            data=data,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def bind_garage_vehicle(
    index: int | None = None,
    query: str = "",
) -> dict:
    """Bind full ACES from AutoZone Pro garage (index 1-based or text query).

    Best fitment quality for YMM-like jobs when the car is already saved.
    """

    def _do():
        data = get_client().bind_garage_vehicle(index=index, query=query)
        return ToolEnvelope(
            ok=bool(data.get("ok")),
            error_code="ok" if data.get("ok") else "no_hits",
            message=data.get("vehicle_name") or data.get("error") or "bind failed",
            elapsed_ms=data.get("elapsed_ms") or 0,
            data=data,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def get_bound_vehicle() -> dict:
    """Return currently bound in-process vehicle name, ACES, and fitment_quality."""

    def _do():
        data = get_client().bound_vehicle_summary()
        return ToolEnvelope(
            ok=bool(data.get("vehicle_name") or data.get("aces")),
            error_code="ok",
            message=data.get("vehicle_name") or "no vehicle bound",
            elapsed_ms=0,
            data=data,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def search_parts(
    query: str,
    limit: int = 15,
    include_prices: bool = True,
    position: str | None = None,
    expect_vehicle: str | None = None,
) -> dict:
    """Search AutoZone Pro catalog by keyword or part number (vehicle-bound).

    Uses in-process bound ACES (from plate/VIN/ymme/garage). Free-text category hits
    are expanded via v2/products so you get real SKUs. include_prices=True attaches
    commercial cost, list_price, core, store qty — cheapest cost first.
    ALWAYS show both cost and list when quoting (shop-quote skill).
    position: optional 'Front' or 'Rear' for pads/rotors.
    expect_vehicle: e.g. '2016 Lincoln MKZ' — if bound vehicle does not match, returns
    error_code no_hits with VEHICLE_MISMATCH note (do not use prices).
    Oil filter queries expand azpg1622 (not motor oil). Viscosity like 5W-20 filters oils.
    Prefer OEM/aftermarket part numbers when known (e.g. FL500S, 85123399).
    """

    def _do():
        t0 = time.time()
        result = get_client().search_parts(
            query,
            limit=limit,
            include_prices=include_prices,
            position=position,
            expect_vehicle=expect_vehicle,
        )
        elapsed = int((time.time() - t0) * 1000)
        mismatch = result.response_type == "VEHICLE_MISMATCH"
        ok = bool(result.parts) and not mismatch
        cheap = result.cheapest
        price_bit = ""
        if cheap and cheap.cost is not None:
            price_bit = f"; cheapest {cheap.brand} {cheap.part_number} cost ${cheap.cost:.2f}"
            if cheap.list_price is not None:
                price_bit += f" / list ${cheap.list_price:.2f}"
        msg = (
            (result.notes[0] if mismatch and result.notes else "")
            or (
                f"{len(result.parts)} parts"
                + (f" in {result.part_group_name}" if result.part_group_name else "")
                + price_bit
                if result.parts
                else (result.response_type or "no products")
            )
        )
        return ToolEnvelope(
            ok=ok,
            error_code="ok" if ok else ("invalid_args" if mismatch else "no_hits"),
            message=msg,
            elapsed_ms=elapsed,
            data=result.model_dump(),
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def list_group_products(
    part_group_id: str,
    position: str | None = None,
    limit: int = 40,
    include_prices: bool = True,
) -> dict:
    """List vehicle-fit products for a part group, sorted by commercial cost.

    Each hit includes cost + list_price when include_prices=True. Quote both.
    Common groups: azpg4204=Brake Pads, azpg1368=Brake Rotor, azpg1356=Brake Caliper,
    azpg4304=Control Arms.
    position: 'Front' or 'Rear' when applicable (or full location facet text).
    """

    def _do():
        t0 = time.time()
        result = get_client().list_group_products(
            part_group_id,
            position=position,
            limit=limit,
            include_prices=include_prices,
        )
        elapsed = int((time.time() - t0) * 1000)
        cheap = result.cheapest
        price_bit = ""
        if cheap and cheap.cost is not None:
            price_bit = f"; cheapest {cheap.brand} {cheap.part_number} cost ${cheap.cost:.2f}"
            if cheap.list_price is not None:
                price_bit += f" / list ${cheap.list_price:.2f}"
        msg = (
            f"{len(result.parts)} in {result.part_group_name or part_group_id}"
            + price_bit
        )
        return ToolEnvelope(
            ok=bool(result.parts),
            error_code="ok" if result.parts else "no_hits",
            message=msg,
            elapsed_ms=elapsed,
            data=result.model_dump(),
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def get_prices(item_ids: list[str]) -> dict:
    """Commercial cost + list + core + store availability for AZ item ids.

    Pass item_id values from search_parts or list_group_products.
    Returns both cost and list_price — always show both when building a quote.
    """

    def _do():
        t0 = time.time()
        priced = get_client().get_prices(list(item_ids or []))
        elapsed = int((time.time() - t0) * 1000)
        # strip heavy raw for tool response unless small
        slim = {}
        for iid, row in priced.items():
            slim[iid] = {
                k: row.get(k)
                for k in (
                    "cost",
                    "list_price",
                    "core",
                    "store_qty",
                    "availability_level",
                    "combined_qty",
                )
            }
        n = len(slim)
        with_both = sum(
            1
            for r in slim.values()
            if r.get("cost") is not None and r.get("list_price") is not None
        )
        return ToolEnvelope(
            ok=bool(slim),
            error_code="ok" if slim else "no_hits",
            message=f"{n} priced ({with_both} with cost+list)",
            elapsed_ms=elapsed,
            data={"prices": slim, "count": n, "with_cost_and_list": with_both},
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def get_credit_snapshot() -> dict:
    """Read-only AutoZone Pro credit: balance, overdue/past-due, available credit.

    One small HTTP call (credit-info), not an invoice dump. Numbers come from AZ Pro —
    never invent balances. past_due is the same amount as overdue.
    """

    def _do():
        data = get_client().get_credit_snapshot()
        ok = bool(data.get("ok"))
        if data.get("logged_in") is False:
            data["auth_lost"] = True
            data["user_visible_banner"] = AZPRO_AUTH_LOST_BANNER
            return ToolEnvelope(
                ok=False,
                error_code="not_logged_in",
                message=AZPRO_AUTH_LOST_BANNER,
                elapsed_ms=data.get("elapsed_ms") or 0,
                data=data,
            ).model_dump()
        bal = data.get("balance")
        od = data.get("overdue")
        av = data.get("available_credit")

        def _m(v):
            return f"${v:.2f}" if isinstance(v, (int, float)) else "?"

        return ToolEnvelope(
            ok=ok,
            error_code="ok" if ok else "no_hits",
            message=f"balance {_m(bal)} · overdue {_m(od)} · available {_m(av)}",
            elapsed_ms=data.get("elapsed_ms") or 0,
            data=data,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def scan_invoices(
    limit: int = 15,
    days: int = 90,
    invoice_type: str = "",
) -> dict:
    """Scan AutoZone Pro invoices and receipts (transaction history). Bounded list.

    Returns identifier, date, amount, and type/status. Default last 90 days and
    page size `limit` (max 50). Does not download PDFs or line-item payloads.
    invoice_type optional: INVOICE, RETURN, PAYMENT, ADJUSTMENT, REBATE.
    """

    def _do():
        data = get_client().scan_invoices(
            limit=limit,
            days=days,
            invoice_type=invoice_type or None,
        )
        if data.get("logged_in") is False:
            data["auth_lost"] = True
            data["user_visible_banner"] = AZPRO_AUTH_LOST_BANNER
            return ToolEnvelope(
                ok=False,
                error_code="not_logged_in",
                message=AZPRO_AUTH_LOST_BANNER,
                elapsed_ms=data.get("elapsed_ms") or 0,
                data=data,
            ).model_dump()
        n = int(data.get("count") or 0)
        return ToolEnvelope(
            ok=bool(data.get("ok")),
            error_code="ok" if data.get("ok") else "no_hits",
            message=f"{n} invoices/receipts (limit {data.get('limit')})",
            elapsed_ms=data.get("elapsed_ms") or 0,
            data=data,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def list_categories() -> dict:
    """List top-level catalog categories (Chemicals, Brakes, etc.)."""

    def _do():
        data = get_client().list_categories()
        return ToolEnvelope(
            ok=bool(data.get("ok")),
            error_code="ok" if data.get("ok") else "no_hits",
            message=f"{data.get('count', 0)} categories",
            elapsed_ms=data.get("elapsed_ms") or 0,
            data=data,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def get_shop_profile() -> dict:
    """Return the local garage profile (name, AutoZone store number, address).

    Stored only on this machine. Empty until first-run setup or set_shop_profile.
    """

    def _do():
        data = load_shop_profile()
        ok = profile_complete(data)
        return ToolEnvelope(
            ok=ok,
            error_code="ok" if ok else "setup_required",
            message=format_shop_line(data) or "shop profile not set",
            elapsed_ms=0,
            data={**data, "shop_profile_needed": not ok},
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def set_shop_profile(
    garage_name: str,
    store_number: str = "",
    address: str = "",
    city: str = "",
    state: str = "",
    zip: str = "",
    phone: str = "",
) -> dict:
    """Merge local garage identity. Empty arguments do not clear saved fields.

    Not uploaded; not an AutoZone login. git pull never touches this file.
    garage_name required only when no profile exists yet.
    """

    def _do():
        if not (garage_name or "").strip() and not profile_complete():
            return ToolEnvelope(
                ok=False,
                error_code="invalid_args",
                message="garage_name required",
                elapsed_ms=0,
            ).model_dump()
        saved = save_shop_profile(
            {
                "garage_name": garage_name,
                "store_number": store_number,
                "address": address,
                "city": city,
                "state": state,
                "zip": zip,
                "phone": phone,
            }
        )
        apply_instructions()
        return ToolEnvelope(
            ok=True,
            error_code="ok",
            message=format_shop_line(saved),
            elapsed_ms=0,
            data=saved,
        ).model_dump()

    return await _run(_do)


@mcp.tool()
async def import_browser_cookies(browser: str) -> dict:
    """Import AutoZone Pro session cookies from a local browser profile (on-device only).

    Ask the user which browser they use for autozonepro.com (chrome, chromium, brave,
    firefox, edge, opera, vivaldi). Only run after they consent. Writes
    ~/.config/autozonepro_cookies.json and reloads the client. Does not upload cookies.
    """

    def _do():
        import subprocess
        import sys
        from pathlib import Path

        t0 = time.time()
        # package at src/azpro_mcp_server → repo root is parents[2]
        root = Path(__file__).resolve().parents[2]
        script = root / "scripts" / "export_browser_cookies.py"
        out = Path.home() / ".config" / "autozonepro_cookies.json"
        cmd = [sys.executable, str(script), "--browser", browser, "--out", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        ok = proc.returncode == 0
        # force client reload so next tool uses new cookies
        global _client
        _client = None
        if ok:
            get_client()
        return ToolEnvelope(
            ok=ok,
            error_code="ok" if ok else "cookie_import_failed",
            message=(proc.stdout or proc.stderr or "").strip()[:300],
            elapsed_ms=int((time.time() - t0) * 1000),
            data={
                "browser": browser,
                "out": str(out),
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
            },
        ).model_dump()

    return await _run(_do)


def _maybe_first_run_setup(*, force: bool = False) -> None:
    existing = load_shop_profile()
    if profile_complete(existing) and not force:
        return
    suggested = dict(existing)
    status = None
    try:
        status = get_client().get_session_status()
        az = suggest_from_session(status)
        for k, v in az.items():
            if v and not (suggested.get(k) or "").strip():
                suggested[k] = v
    except Exception:
        status = None
    if force or stdin_is_interactive():
        prompt_shop_profile(suggested)
        apply_instructions()
        return
    if status:
        bootstrap_from_session(status)
    if not profile_complete():
        print(
            "[azpro] Shop profile not set. Ask the user for garage name, AutoZone store number, "
            "and address (suggestions may be on get_session_status.suggested_shop), "
            "then call set_shop_profile. Existing ~/.config/autozonepro/shop.json is never "
            "overwritten by git pull.",
            file=sys.stderr,
        )


def run_server() -> None:
    parser = argparse.ArgumentParser(description="AutoZone Pro MCP server")
    parser.add_argument("--transport", default="stdio", choices=["stdio"])
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Prompt for garage name / store number / address, save locally, and exit",
    )
    args = parser.parse_args()
    if args.setup:
        _maybe_first_run_setup(force=True)
        shop = load_shop_profile()
        print(format_shop_line(shop) or "shop profile saved")
        return
    _maybe_first_run_setup(force=False)
    apply_instructions()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
