"""HTTP client for AutoZone Pro commercial APIs."""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .account import (
    DEFAULT_SCAN_LIMIT,
    _clamp_limit,
    parse_credit_snapshot,
    parse_transaction_list,
)
from .models import PartHit, PartSearchResult, VehicleSummary

BASE = "https://www.autozonepro.com"
DEFAULT_COOKIES = Path.home() / ".config" / "autozonepro_cookies.json"


class AzProClient:
    """Cookie + JWT session for AutoZone Pro."""

    def __init__(self, cookies: Optional[Dict[str, str]] = None, base_url: str = BASE):
        self.base_url = base_url.rstrip("/")
        self.cookies = cookies or {}
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, application/vnd.oracle.resource+json, */*",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/ui/my-zone",
            }
        )
        self._inject_cookies(self.cookies)
        self._access_token: Optional[str] = None
        self._token_expiry_ms: int = 0
        self._header_cache: Optional[dict] = None
        self.last_vehicles: List[VehicleSummary] = []
        self._vehicle_aces: Optional[dict] = None
        self._store_number: Optional[str] = None
        self._vehicle_name: Optional[str] = None

    def _inject_cookies(self, cookies: Dict[str, str]) -> None:
        for name, value in (cookies or {}).items():
            if not value or str(value).startswith("<"):
                continue
            val = str(value).strip().strip('"')
            for domain in ("www.autozonepro.com", ".autozonepro.com"):
                self._session.cookies.set(name, val, domain=domain, path="/")

    def _cookie(self, name: str) -> Optional[str]:
        for c in self._session.cookies:
            if c.name == name and c.value:
                return c.value
        return self.cookies.get(name)

    def ensure_session(self, force: bool = False) -> dict:
        """Refresh JSESSIONID-backed session and JWT access_token cookie."""
        now = int(time.time() * 1000)
        if (
            not force
            and self._access_token
            and self._token_expiry_ms
            and now < self._token_expiry_ms - 60_000
        ):
            return {"status": "authenticated", "cached": True}

        r = self._session.get(f"{self.base_url}/api/v2/session", timeout=20)
        r.raise_for_status()
        data = r.json() if r.content else {}

        tok = self._cookie("access_token")
        exp = self._cookie("access_token_expiry") or self._cookie("access_token_ttl")
        if tok:
            self._access_token = tok
            self._session.headers["Authorization"] = f"Bearer {tok}"
        if exp and str(exp).isdigit():
            self._token_expiry_ms = int(exp)
        else:
            # JWT exp fallback ~50 min
            self._token_expiry_ms = now + 50 * 60 * 1000
        return data

    def _get(self, path: str, **kwargs) -> requests.Response:
        self.ensure_session()
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        return self._session.get(url, timeout=kwargs.pop("timeout", 20), **kwargs)

    def _post(self, path: str, json_body: Optional[dict] = None, **kwargs) -> requests.Response:
        self.ensure_session()
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers.setdefault("Content-Type", "application/json")
        return self._session.post(
            url, json=json_body, headers=headers, timeout=kwargs.pop("timeout", 25), **kwargs
        )

    def get_header(self) -> dict:
        self.ensure_session()
        r = self._get("/ecomm/b2b/v1/sites/header")
        if r.status_code >= 400:
            return {"error": f"HTTP {r.status_code}", "body": r.text[:300]}
        self._header_cache = r.json()
        veh = (self._header_cache or {}).get("currentVehicle") or {}
        aces = veh.get("currentVehicleAcesId") or {}
        if aces:
            self._vehicle_aces = aces
        store = (self._header_cache or {}).get("currentStore") or {}
        if store.get("storeNumber"):
            self._store_number = str(store.get("storeNumber"))
        self._vehicle_name = veh.get("vehicleName") or self._vehicle_name
        return self._header_cache

    def vehicle_params(self) -> Dict[str, str]:
        """ACES ids for vehicle-bound catalog calls (from sticky header vehicle)."""
        if not self._vehicle_aces:
            self.get_header()
        aces = self._vehicle_aces or {}
        out: Dict[str, str] = {}
        for src, dst in (
            ("year", "year"),
            ("makeId", "makeId"),
            ("modelId", "modelId"),
            ("vehicleTypeId", "vehicleTypeId"),
            ("engineBaseId", "engineBaseId"),
            ("subModelId", "subModelId"),
            ("driveTypeId", "driveTypeId"),
            ("brakeSystemId", "brakeSystemId"),
            ("frontBrakeTypeId", "frontBrakeTypeId"),
        ):
            v = aces.get(src)
            if v is not None and str(v) != "":
                out[dst] = str(v)
        return out

    def get_session_status(self) -> dict:
        t0 = time.time()
        try:
            sess = self.ensure_session(force=True)
        except Exception as e:
            return {
                "ok": False,
                "logged_in": False,
                "error": str(e),
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        header = self.get_header()
        user = (header or {}).get("userInfo") or {}
        store = (header or {}).get("currentStore") or {}
        vehicle = (header or {}).get("currentVehicle") or {}
        shops = []
        try:
            sr = self._get("/ecomm/b2b/v1/shops")
            if sr.status_code < 400:
                shops = (sr.json() or {}).get("shops") or []
        except Exception:
            pass
        return {
            "ok": sess.get("status") == "authenticated" or bool(self._access_token),
            "logged_in": sess.get("status") == "authenticated" or bool(self._access_token),
            "session": sess,
            "user": {
                "first_name": user.get("firstName"),
                "last_name": user.get("lastName"),
                "username": user.get("userName"),
                "email": user.get("emailAddress"),
                "pin": user.get("currentPin") or user.get("primaryPin"),
            },
            "shop": shops[0] if shops else None,
            "current_store": {
                "number": store.get("storeNumber"),
                "address": store.get("storeAddress"),
                "city": store.get("storeCity"),
                "state": store.get("storeState"),
                "zip": store.get("storeZipCode"),
            },
            "current_vehicle": vehicle.get("vehicleName")
            or (vehicle.get("currentVehicleAcesId") and vehicle),
            "current_vehicle_detail": vehicle,
            "cart_size": header.get("cartSize"),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    def list_vehicles(self) -> dict:
        t0 = time.time()
        self.ensure_session()
        r = self._get("/ecomm/b2b/v2/profiles/vehicles")
        if r.status_code >= 400:
            r = self._get("/ecomm/b2b/v1/profiles/vehicles")
        if r.status_code >= 400:
            return {
                "ok": False,
                "count": 0,
                "vehicles": [],
                "error": f"HTTP {r.status_code}",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        data = r.json() or {}
        out: List[VehicleSummary] = []
        for v in data.get("vehicles") or []:
            ids = v.get("vehicleIds") or {}
            out.append(
                VehicleSummary(
                    year=str(v.get("year") or ids.get("year") or ""),
                    make=v.get("makeName") or v.get("make"),
                    model=v.get("modelName") or v.get("model"),
                    engine=v.get("engineBaseName") or v.get("engine"),
                    submodel=v.get("subModelName") or v.get("subModel"),
                    vin=v.get("vehicleLookupValue") if v.get("vehicleLookupType") == "VIN" else None,
                    nickname=v.get("vehicleNickName") or v.get("vehicleName") or v.get("vehicleDesription"),
                    atg_vehicle_id=str(v.get("atgVehicleId") or "") or None,
                    is_current=bool(v.get("currentVehicle")),
                    make_id=str(ids.get("makeId") or "") or None,
                    model_id=str(ids.get("modelId") or "") or None,
                    engine_base_id=str(ids.get("engineBaseId") or "") or None,
                    submodel_id=str(ids.get("subModelId") or "") or None,
                    raw=v,
                )
            )
        self.last_vehicles = out
        return {
            "ok": bool(out),
            "count": len(out),
            "vehicles": [v.model_dump() for v in out],
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    def lookup_vin(self, vin: str) -> dict:
        t0 = time.time()
        v = "".join(ch for ch in (vin or "").upper() if ch.isalnum())
        if len(v) != 17:
            return {
                "ok": False,
                "vin": v,
                "error": f"invalid VIN length {len(v)} (need 17)",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        self.ensure_session()
        r = self._get("/vehicle/decoder/v3/vin", params={"vin": v})
        if r.status_code >= 400:
            r = self._get("/vehicle/decoder/v1/vin", params={"vin": v})
        if r.status_code >= 400:
            return {
                "ok": False,
                "vin": v,
                "error": f"HTTP {r.status_code}: {r.text[:200]}",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        rows = r.json() or []
        if not isinstance(rows, list):
            rows = [rows]
        vehicles = []
        for row in rows:
            label = (
                f"{row.get('year')} {row.get('make')} {row.get('model')} "
                f"{row.get('subModel') or ''} {row.get('engineBase') or row.get('name') or ''}"
            ).strip()
            vehicles.append(
                {
                    "label": label,
                    "year": str(row.get("year") or ""),
                    "make": row.get("make"),
                    "model": row.get("model"),
                    "submodel": row.get("subModel"),
                    "engine": row.get("engineBase") or row.get("azEngineLiters"),
                    "vin": row.get("vin") or v,
                    "make_id": str(row.get("makeId") or "") or None,
                    "model_id": str(row.get("modelId") or "") or None,
                    "engine_base_id": str(row.get("engineBaseId") or "") or None,
                    "submodel_id": str(row.get("subModelId") or "") or None,
                    "az_vehicle_id": str(row.get("azVehicleId") or "") or None,
                    "raw": row,
                }
            )
        primary = vehicles[0] if vehicles else None
        if primary and primary.get("raw"):
            try:
                self.bind_aces_from_decoder_row(primary["raw"], vehicle_name=primary.get("label"))
            except Exception:
                pass
        return {
            "ok": bool(vehicles),
            "vin": v,
            "count": len(vehicles),
            "vehicles": vehicles,
            "primary": primary,
            "bound": bool(primary),
            "vehicle_name": self._vehicle_name,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }


    def lookup_plate(self, plate: str, state: str = "FL", *, bind: bool = True) -> dict:
        """Decode license plate via AZ Pro vehicle decoder and optionally bind ACES sticky.

        GET /vehicle/decoder/v3/licensePlate?licensePlate=...&state=...
        NEVER use sticky vehicle when user gave a plate — call this first.
        """
        t0 = time.time()
        p = "".join(ch for ch in (plate or "").upper() if ch.isalnum())
        s = "".join(ch for ch in (state or "FL").upper() if ch.isalpha())[:2]
        if not p or not s:
            return {
                "ok": False,
                "plate": p,
                "state": s,
                "error": "plate and state required",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        self.ensure_session()
        r = self._get(
            "/vehicle/decoder/v3/licensePlate",
            params={"licensePlate": p, "state": s},
        )
        if r.status_code >= 400:
            return {
                "ok": False,
                "plate": p,
                "state": s,
                "error": f"HTTP {r.status_code}: {r.text[:200]}",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        rows = r.json() or []
        if not isinstance(rows, list):
            rows = [rows]
        vehicles = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = (
                f"{row.get('year')} {row.get('make')} {row.get('model')} "
                f"{row.get('subModel') or ''} {row.get('engineBase') or row.get('name') or ''}"
            ).strip()
            vehicles.append(
                {
                    "label": label,
                    "year": str(row.get("year") or ""),
                    "make": row.get("make"),
                    "model": row.get("model"),
                    "submodel": row.get("subModel"),
                    "engine": row.get("engineBase") or row.get("azEngineLiters"),
                    "vin": row.get("vin"),
                    "make_id": str(row.get("makeId") or "") or None,
                    "model_id": str(row.get("modelId") or "") or None,
                    "engine_base_id": str(row.get("engineBaseId") or "") or None,
                    "submodel_id": str(row.get("subModelId") or "") or None,
                    "drive_type_id": str(row.get("driveTypeId") or "") or None,
                    "az_vehicle_id": str(row.get("azVehicleId") or "") or None,
                    "plate": p,
                    "state": s,
                    "raw": row,
                }
            )
        primary = vehicles[0] if vehicles else None
        if bind and primary and primary.get("raw"):
            self.bind_aces_from_decoder_row(primary["raw"], vehicle_name=primary.get("label"))
        return {
            "ok": bool(vehicles),
            "plate": p,
            "state": s,
            "count": len(vehicles),
            "vehicles": vehicles,
            "primary": primary,
            "bound": bool(bind and primary),
            "vehicle_name": self._vehicle_name,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    def bind_aces_from_decoder_row(self, row: dict, vehicle_name: Optional[str] = None) -> dict:
        """Set in-process sticky ACES from a VIN/plate decoder row (for catalog fitment)."""
        if not row:
            return {"ok": False, "error": "empty row"}
        aces = {}
        mapping = {
            "year": "year",
            "makeId": "makeId",
            "modelId": "modelId",
            "engineBaseId": "engineBaseId",
            "subModelId": "subModelId",
            "vehicleTypeId": "vehicleTypeId",
            "driveTypeId": "driveTypeId",
            "engineBoreStrokeId": "engineBoreStrokeId",
            "engineBlockId": "engineBlockId",
            "aspirationId": "aspirationId",
            "engineVinId": "engineVinId",
            "cylinderHeadTypeId": "cylinderHeadTypeId",
            "fuelTypeId": "fuelTypeId",
            "bodyTypeId": "bodyTypeId",
            "engineDesignationId": "engineDesignationId",
            "powerOutputId": "powerOutputId",
            "valvesId": "valvesId",
            "frontBrakeTypeId": "frontBrakeTypeId",
            "brakeSystemId": "brakeSystemId",
        }
        for src, dst in mapping.items():
            if row.get(src) is not None and str(row.get(src)) != "":
                aces[dst] = str(row.get(src))
        self._vehicle_aces = aces
        if vehicle_name:
            self._vehicle_name = vehicle_name
        else:
            self._vehicle_name = (
                f"{row.get('year')} {row.get('make')} {row.get('model')} "
                f"{row.get('subModel') or ''} {row.get('engineBase') or ''}"
            ).strip()
        return {
            "ok": True,
            "vehicle_name": self._vehicle_name,
            "aces": aces,
            "fitment_quality": self._fitment_quality(aces),
        }

    @staticmethod
    def _fitment_quality(aces: Optional[dict]) -> str:
        """full = has engineBaseId (plate/VIN/garage); partial = year/make/model only."""
        a = aces or {}
        if a.get("engineBaseId") and a.get("makeId") and a.get("modelId") and a.get("year"):
            return "full"
        if a.get("makeId") and a.get("modelId") and a.get("year"):
            return "partial"
        return "none"

    def bind_aces_map(self, aces: dict, vehicle_name: Optional[str] = None) -> dict:
        """Bind arbitrary ACES id map (e.g. garage vehicleIds)."""
        if not aces:
            return {"ok": False, "error": "empty aces"}
        cleaned = {str(k): str(v) for k, v in aces.items() if v is not None and str(v) != ""}
        self._vehicle_aces = cleaned
        if vehicle_name:
            self._vehicle_name = vehicle_name
        return {
            "ok": True,
            "vehicle_name": self._vehicle_name,
            "aces": cleaned,
            "fitment_quality": self._fitment_quality(cleaned),
        }

    def bound_vehicle_summary(self) -> dict:
        return {
            "vehicle_name": self._vehicle_name,
            "aces": dict(self._vehicle_aces or {}),
            "fitment_quality": self._fitment_quality(self._vehicle_aces),
            "store_number": self._store_number,
        }

    def vehicle_matches_expect(self, expect: str) -> bool:
        """True if bound/sticky name looks like expected YMME tokens."""
        exp = (expect or "").strip().lower()
        if not exp:
            return True
        name = (self._vehicle_name or "").lower()
        if not name:
            return False
        # Require year if present, plus make or model token
        tokens = [t for t in exp.replace(",", " ").split() if t]
        if not tokens:
            return True
        year = next((t for t in tokens if t.isdigit() and len(t) == 4), None)
        if year and year not in name:
            return False
        # At least one non-year token must appear
        others = [t for t in tokens if t != year]
        if not others:
            return True
        return any(t in name for t in others)

    def bind_garage_vehicle(
        self,
        index: Optional[int] = None,
        query: str = "",
    ) -> dict:
        """Bind full ACES from a saved garage vehicle (index 1-based or text match)."""
        t0 = time.time()
        garage = self.list_vehicles()
        vehicles = garage.get("vehicles") or []
        if not vehicles:
            return {
                "ok": False,
                "error": "garage empty",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        chosen = None
        if index is not None:
            if index < 1 or index > len(vehicles):
                return {
                    "ok": False,
                    "error": f"index {index} out of range 1..{len(vehicles)}",
                    "count": len(vehicles),
                    "elapsed_ms": int((time.time() - t0) * 1000),
                }
            chosen = vehicles[index - 1]
        elif query.strip():
            q = query.strip().lower()
            scored = []
            for v in vehicles:
                blob = " ".join(
                    str(x or "")
                    for x in (
                        v.get("year"),
                        v.get("make"),
                        v.get("model"),
                        v.get("engine"),
                        v.get("submodel"),
                        v.get("nickname"),
                    )
                ).lower()
                score = sum(1 for tok in q.split() if tok and tok in blob)
                if score:
                    scored.append((score, v))
            scored.sort(key=lambda x: x[0], reverse=True)
            if scored:
                chosen = scored[0][1]
        else:
            # Prefer is_current
            chosen = next((v for v in vehicles if v.get("is_current")), vehicles[0])

        if not chosen:
            return {
                "ok": False,
                "error": "no garage vehicle matched",
                "count": len(vehicles),
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        raw = chosen.get("raw") or {}
        ids = raw.get("vehicleIds") or {}
        # Prefer full vehicleIds map (engineBaseId etc.)
        aces_src = dict(ids)
        if not aces_src.get("year"):
            aces_src["year"] = chosen.get("year") or raw.get("year")
        name = (
            chosen.get("nickname")
            or f"{chosen.get('year')} {chosen.get('make')} {chosen.get('model')} "
            f"{chosen.get('engine') or ''}"
        ).strip()
        bound = self.bind_aces_map(aces_src, vehicle_name=name)
        return {
            "ok": bool(bound.get("ok")),
            "source": "garage",
            "vehicle_name": self._vehicle_name,
            "fitment_quality": bound.get("fitment_quality"),
            "garage_index": (vehicles.index(chosen) + 1) if chosen in vehicles else None,
            "aces": self._vehicle_aces,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    def set_vehicle_ymme(
        self,
        year: int | str,
        make: str,
        model: str,
        engine: str = "",
        prefer_garage: bool = True,
    ) -> dict:
        """Bind vehicle from year/make/model[/engine].

        Strategy:
        1) Prefer matching saved garage vehicle (full ACES / best fitment).
        2) Else parse year/makeId/modelId via catalog search and bind partial ACES.
           Fitment is partial without engineBaseId — plate/VIN still preferred.
        """
        t0 = time.time()
        y = str(year).strip()
        mk = (make or "").strip()
        md = (model or "").strip()
        eng = (engine or "").strip()
        if not (y and mk and md):
            return {
                "ok": False,
                "error": "year, make, and model required",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }

        q = f"{y} {mk} {md}" + (f" {eng}" if eng else "")
        if prefer_garage:
            g = self.bind_garage_vehicle(query=q)
            if g.get("ok") and g.get("fitment_quality") == "full":
                # Verify year matches
                if y in (g.get("vehicle_name") or "") or y in str(
                    (g.get("aces") or {}).get("year") or ""
                ):
                    g["ymme_query"] = q
                    g["note"] = "Bound from garage (full ACES)"
                    return g

        # Catalog search extracts year/makeId/modelId/makeName/modelName
        self.ensure_session()
        r = self._post(
            "/catalog/lookup/v4/search",
            json_body={"searchText": q, "recordsPerPage": 5, "offset": 0},
        )
        if r.status_code >= 400:
            return {
                "ok": False,
                "error": f"ymme search HTTP {r.status_code}: {r.text[:200]}",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        data = r.json() or {}
        year_id = data.get("year")
        make_id = data.get("makeId")
        model_id = data.get("modelId")
        make_name = data.get("makeName") or mk
        model_name = data.get("modelName") or md
        if not (year_id and make_id and model_id):
            return {
                "ok": False,
                "error": (
                    f"could not parse YMM from catalog for {q!r}; "
                    "use plate/VIN or garage vehicle"
                ),
                "search_response_type": data.get("responseType"),
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        aces = {
            "year": str(year_id),
            "makeId": str(make_id),
            "modelId": str(model_id),
            "vehicleTypeId": "6",  # Car & Truck default
        }
        name = f"{year_id} {make_name} {model_name}"
        if eng:
            name = f"{name} {eng}".strip()
        bound = self.bind_aces_map(aces, vehicle_name=name)
        return {
            "ok": True,
            "source": "ymme_catalog_parse",
            "ymme_query": q,
            "vehicle_name": self._vehicle_name,
            "fitment_quality": "partial",
            "aces": self._vehicle_aces,
            "make_name": make_name,
            "model_name": model_name,
            "warning": (
                "Partial ACES (no engineBaseId). Fitment may show MAY_NOT_FIT/DOES_NOT_FIT "
                "for some SKUs. Prefer plate/VIN or garage vehicle with full ACES for "
                "fitment-critical parts. Part-number lookups still OK."
            ),
            "elapsed_ms": int((time.time() - t0) * 1000),
            **{k: bound.get(k) for k in ("aces",) if k in bound},
        }


    def _sku_to_hit(self, sku: dict) -> PartHit:
        fit = sku.get("vehicleFitment") or {}
        attrs = {
            str(a.get("label")): str(a.get("value"))
            for a in (sku.get("productAttributes") or [])
            if a.get("label") is not None
        }
        return PartHit(
            item_id=str(sku.get("itemId") or "") or None,
            part_number=sku.get("partNumber"),
            brand=sku.get("brandName"),
            description=sku.get("itemDescription"),
            part_group=sku.get("partGroupName"),
            part_group_id=sku.get("partGroupId"),
            line_code=sku.get("lineCode"),
            image_url=sku.get("productImageUrl"),
            product_url=(
                f"{self.base_url}{sku['productDetailsPageUrl']}"
                if sku.get("productDetailsPageUrl")
                else None
            ),
            vehicle_fitment=fit.get("vehicleFit") or fit.get("vehicleFitmentLabel"),
            score=sku.get("score"),
            attributes=attrs,
        )

    def get_prices(self, item_ids: List[str]) -> Dict[str, dict]:
        """Commercial cost/list/core + store availability for item ids.

        GET /ecomm/b2b/v1/catalog/skus?ids=...
        Returns map item_id -> {cost, list_price, core, store_qty, availability_level, raw}.
        """
        self.ensure_session()
        ids = [str(i) for i in (item_ids or []) if i and str(i) not in ("", "-1")]
        out: Dict[str, dict] = {}
        for i in range(0, len(ids), 25):
            batch = ids[i : i + 25]
            r = self._get(
                "/ecomm/b2b/v1/catalog/skus",
                params={"ids": ",".join(batch)},
                timeout=30,
            )
            if r.status_code >= 400:
                print(f"[azpro] get_prices HTTP {r.status_code}: {r.text[:200]}")
                continue
            data = r.json() or {}
            rows = data.get("skus") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                iid = str(row.get("skuId") or row.get("itemId") or "")
                if not iid:
                    continue
                unf = ((row.get("pricing") or {}).get("unformatted") or {})
                avail = row.get("availability") or {}

                def _f(key: str) -> Optional[float]:
                    v = unf.get(key)
                    if v is None or v == "":
                        return None
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None

                out[iid] = {
                    "cost": _f("cost"),
                    "list_price": _f("list"),
                    "core": _f("core"),
                    "store_qty": avail.get("storeQuantity"),
                    "availability_level": avail.get("availabilityLevel"),
                    "combined_qty": avail.get("combinedQuantity"),
                    "raw": row,
                }
        return out

    def _attach_prices(self, parts: List[PartHit]) -> List[PartHit]:
        ids = [p.item_id for p in parts if p.item_id]
        priced = self.get_prices(ids) if ids else {}
        for p in parts:
            if not p.item_id or p.item_id not in priced:
                continue
            pr = priced[p.item_id]
            p.cost = pr.get("cost")
            p.list_price = pr.get("list_price")
            p.core = pr.get("core")
            p.store_qty = pr.get("store_qty")
            p.availability_level = pr.get("availability_level")
        return parts

    @staticmethod
    def _viscosity_from_query(query: str) -> Optional[str]:
        """Extract viscosity like 5W-20 / 0W20 from free text."""
        import re

        m = re.search(r"\b(\d{1,2})\s*W\s*-?\s*(\d{2})\b", (query or ""), re.I)
        if not m:
            return None
        return f"{m.group(1)}W-{m.group(2)}"

    @staticmethod
    def _pick_part_group(query: str, part_groups: List[str]) -> str:
        """Choose best part group from multi-group category redirects."""
        qlow = (query or "").lower()
        pick = part_groups[0]
        if any(k in qlow for k in ("oil filter", "oilfilter")) or (
            "filter" in qlow and "oil" in qlow and "cabin" not in qlow and "air filter" not in qlow
        ):
            for pg in part_groups:
                if "1622" in pg or pg == "azpg1622":
                    return pg
        if (
            "motor oil" in qlow
            or "engine oil" in qlow
            or AzProClient._viscosity_from_query(qlow)
            or (qlow.strip() in ("oil", "synthetic oil", "full synthetic"))
        ):
            for pg in part_groups:
                if pg == "12138" or "motor" in pg.lower():
                    return pg
            # Prefer numeric motor-oil group over filter when both present
            for pg in part_groups:
                if pg.isdigit():
                    return pg
        if any(k in qlow for k in ("pad", "pads", "brake pad")):
            for pg in part_groups:
                if "4204" in pg or pg == "azpg4204":
                    return pg
        if any(k in qlow for k in ("rotor", "disc", "drum")):
            for pg in part_groups:
                if "1368" in pg or pg == "azpg1368":
                    return pg
        if "control arm" in qlow:
            for pg in part_groups:
                if "4304" in pg or pg == "azpg4304":
                    return pg
        return pick

    def list_group_products(
        self,
        part_group_id: str,
        position: Optional[str] = None,
        limit: int = 40,
        include_prices: bool = True,
        fits_only: bool = True,
        attribute_contains: Optional[Dict[str, str]] = None,
    ) -> PartSearchResult:
        """Vehicle-bound products for a part group (azpg4204=pads, azpg1368=rotors).

        position: 'Front' or 'Rear' when facet exists (position_name_s).
        attribute_contains: e.g. {"Weight": "5W-20"} to keep matching SKUs only.
        """
        t0 = time.time()
        notes: List[str] = []
        pg = (part_group_id or "").strip()
        if not pg:
            return PartSearchResult(query=pg, notes=["empty part_group_id"], total=0)
        self.ensure_session()
        veh = self.vehicle_params()
        # Fetch a wider page when filtering by viscosity so 5W-20 is not buried
        fetch_n = max(1, min(int(limit), 50))
        if attribute_contains:
            fetch_n = max(fetch_n, min(50, int(limit) * 3))
        params: Dict[str, Any] = {
            "partGroupId": pg,
            "recordsPerPage": fetch_n,
            "offset": 0,
            **veh,
        }
        if position:
            # seoUrl form: facet=position_name_s:Front
            params["facet"] = f"position_name_s:{position.strip().title()}"
        r = self._get("/catalog/lookup/v2/products", params=params, timeout=30)
        if r.status_code >= 400:
            notes.append(f"v2/products HTTP {r.status_code}: {r.text[:200]}")
            return PartSearchResult(
                query=pg,
                notes=notes,
                total=0,
                part_group_id=pg,
                vehicle_name=self._vehicle_name,
                store_number=self._store_number,
            )
        data = r.json() or {}
        skus = data.get("skuRecords") or []
        parts: List[PartHit] = []
        for sku in skus:
            hit = self._sku_to_hit(sku)
            if fits_only and hit.vehicle_fitment and str(hit.vehicle_fitment).upper() not in (
                "FITS",
                "VEHICLE_SPECIFIC",
            ):
                # vehicleFit is usually "FITS"; skip clear mismatches
                if str(hit.vehicle_fitment).upper() in ("DOES_NOT_FIT", "NO", "FALSE"):
                    continue
            if attribute_contains:
                attrs = hit.attributes or {}
                ok_attr = True
                for ak, av in attribute_contains.items():
                    if not av:
                        continue
                    # Match Weight / description / any attr
                    blob = " ".join(
                        [str(attrs.get(ak) or ""), str(attrs.get("Weight") or ""), hit.description or ""]
                    ).upper()
                    if str(av).upper().replace(" ", "") not in blob.replace(" ", ""):
                        ok_attr = False
                        break
                if not ok_attr:
                    continue
            parts.append(hit)
        if attribute_contains and not parts:
            notes.append(f"no SKUs matched attributes {attribute_contains} in first page")
        if include_prices and parts:
            self._attach_prices(parts)
            parts.sort(key=lambda p: (p.cost is None, p.cost if p.cost is not None else 1e12))
        cheapest = parts[0] if parts and parts[0].cost is not None else (parts[0] if parts else None)
        result = PartSearchResult(
            query=pg,
            response_type="PRODUCTS",
            total=int(data.get("totalNumberOfRecords") or len(parts)),
            part_group_id=data.get("partGroupId") or pg,
            part_group_name=data.get("partGroupName"),
            parts=parts[: max(1, min(int(limit), 50))],
            notes=notes,
            vehicle_name=self._vehicle_name,
            store_number=self._store_number,
            cheapest=cheapest,
        )
        print(
            f"[azpro] list_group_products pg={pg!r} pos={position!r} "
            f"n={len(result.parts)} total={result.total} "
            f"elapsed_ms={int((time.time() - t0) * 1000)}"
        )
        return result

    def search_parts(
        self,
        query: str,
        limit: int = 10,
        include_prices: bool = True,
        position: Optional[str] = None,
        expect_vehicle: Optional[str] = None,
    ) -> PartSearchResult:
        """Keyword / part-number search with vehicle context and optional commercial prices.

        Free-text often returns CATEGORY_RESULTS with partGroupIds — we expand the best
        group via v2/products so you get real SKUs + cost.
        expect_vehicle: if set and bound vehicle name does not match, return empty + note.
        """
        t0 = time.time()
        q = (query or "").strip()
        notes: List[str] = []
        if not q:
            return PartSearchResult(query=q, notes=["empty query"], total=0)

        self.ensure_session()
        if expect_vehicle and not self.vehicle_matches_expect(expect_vehicle):
            notes.append(
                f"VEHICLE_MISMATCH: bound={self._vehicle_name!r} expect={expect_vehicle!r}. "
                "Call set_vehicle_ymme / lookup_plate / lookup_vin / bind_garage_vehicle first."
            )
            return PartSearchResult(
                query=q,
                notes=notes,
                total=0,
                vehicle_name=self._vehicle_name,
                store_number=self._store_number,
                response_type="VEHICLE_MISMATCH",
            )

        veh = self.vehicle_params()
        body: Dict[str, Any] = {
            "searchText": q,
            "recordsPerPage": max(1, min(int(limit), 40)),
            "offset": 0,
            **veh,
        }
        r = self._post("/catalog/lookup/v4/search", json_body=body)
        if r.status_code >= 400:
            notes.append(f"search HTTP {r.status_code}: {r.text[:200]}")
            return PartSearchResult(
                query=q,
                notes=notes,
                total=0,
                vehicle_name=self._vehicle_name,
                store_number=self._store_number,
            )
        data = r.json() or {}
        result = PartSearchResult(
            query=q,
            response_type=data.get("responseType"),
            redirect_url=data.get("redirectUrl"),
            vehicle_name=self._vehicle_name,
            store_number=self._store_number,
        )
        psr = data.get("productSearchResponse") or {}
        skus = psr.get("skuRecords") or []
        result.part_group_id = psr.get("partGroupId")
        result.part_group_name = psr.get("partGroupName")
        result.total = int(psr.get("totalNumberOfRecords") or len(skus) or 0)

        import re

        part_groups: List[str] = []
        # CATEGORY_RESULTS: expand part groups (redirect or categoryPartTypesSearchResult)
        if data.get("responseType") == "CATEGORY_RESULTS" or not skus:
            redir = data.get("redirectUrl") or ""
            if redir:
                notes.append(f"category redirect: {redir}")
            m = re.search(r"partGroupIds=([^&]+)", redir)
            if m:
                part_groups = [g for g in m.group(1).split("!") if g]
            cats = (data.get("categoryPartTypesSearchResult") or {}).get("categories") or []
            for cat in cats:
                for rec in cat.get("partTypeRecords") or []:
                    pgid = rec.get("partGroupId")
                    if pgid and pgid not in part_groups:
                        part_groups.append(str(pgid))
            if part_groups:
                pick = self._pick_part_group(q, part_groups)
                visc = self._viscosity_from_query(q)
                attr = {"Weight": visc} if visc else None
                # Oil filter queries should never expand motor-oil-only first page
                expanded = self.list_group_products(
                    pick,
                    position=position,
                    limit=limit,
                    include_prices=include_prices,
                    fits_only=True,
                    attribute_contains=attr,
                )
                notes.append(f"expanded partGroupId={pick}")
                if visc:
                    notes.append(f"viscosity_filter={visc}")
                notes.extend(expanded.notes or [])
                expanded.query = q
                expanded.response_type = expanded.response_type or "PRODUCTS"
                expanded.redirect_url = result.redirect_url
                expanded.notes = notes
                print(
                    f"[azpro] search_parts q={q!r} type=PRODUCTS(via group) "
                    f"n={len(expanded.parts)} total={expanded.total} "
                    f"elapsed_ms={int((time.time() - t0) * 1000)}"
                )
                return expanded

        parts: List[PartHit] = []
        visc = self._viscosity_from_query(q)
        for sku in skus[: max(1, min(int(limit) * (3 if visc else 1), 40))]:
            hit = self._sku_to_hit(sku)
            if visc:
                blob = " ".join(
                    [
                        str((hit.attributes or {}).get("Weight") or ""),
                        hit.description or "",
                    ]
                ).upper().replace(" ", "")
                if visc.upper().replace(" ", "") not in blob:
                    continue
            parts.append(hit)
            if len(parts) >= max(1, min(int(limit), 40)):
                break
        if include_prices and parts:
            self._attach_prices(parts)
            parts.sort(key=lambda p: (p.cost is None, p.cost if p.cost is not None else 1e12))
        result.parts = parts
        result.notes = notes
        result.total = result.total or len(parts)
        result.cheapest = (
            parts[0] if parts and parts[0].cost is not None else (parts[0] if parts else None)
        )
        print(
            f"[azpro] search_parts q={q!r} type={result.response_type} "
            f"n={len(parts)} total={result.total} "
            f"elapsed_ms={int((time.time() - t0) * 1000)}"
        )
        return result

    def list_categories(self, version: str = "v1") -> dict:
        t0 = time.time()
        self.ensure_session()
        path = f"/catalog/lookup/{version}/categories"
        r = self._get(path)
        if r.status_code >= 400:
            return {
                "ok": False,
                "error": f"HTTP {r.status_code}",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        data = r.json()
        return {
            "ok": True,
            "count": len(data) if isinstance(data, list) else 1,
            "categories": data,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    def _account_pin(self) -> Optional[str]:
        """Shop PIN / account number for credit and transaction APIs."""
        header = self._header_cache or self.get_header()
        user = (header or {}).get("userInfo") or {}
        pin = user.get("currentPin") or user.get("primaryPin")
        if pin:
            return str(pin)
        shops = (header or {}).get("shopInfos") or []
        if shops:
            acc = shops[0].get("accountNumber") or shops[0].get("pin")
            if acc:
                return str(acc)
        return None

    def get_credit_snapshot(self) -> dict:
        """One-shot credit: balance, overdue/past-due, available credit (open-to-buy).

        GET /ecomm/b2b/v1/payments/customer/credit-info?pin=...
        Does not pull invoice history.
        """
        t0 = time.time()
        try:
            self.ensure_session()
        except Exception as e:
            return {
                "ok": False,
                "logged_in": False,
                "error": str(e),
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        pin = self._account_pin()
        if not pin:
            return {
                "ok": False,
                "error": "no account PIN on session header",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        r = self._get(
            "/ecomm/b2b/v1/payments/customer/credit-info",
            params={"pin": pin},
        )
        if r.status_code >= 400:
            return {
                "ok": False,
                "error": f"HTTP {r.status_code}: {r.text[:200]}",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        parsed = parse_credit_snapshot(r.json() or {})
        parsed["logged_in"] = True
        parsed["elapsed_ms"] = int((time.time() - t0) * 1000)
        return parsed

    def scan_invoices(
        self,
        limit: int = DEFAULT_SCAN_LIMIT,
        days: int = 90,
        invoice_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Bounded invoice/receipt scan (transaction history). No PDFs or line items.

        GET /ecomm/b2b/v1/transactions with pageSize=limit (capped).
        Dates are YYYY-MM-DD. Default window is the last `days` (max 365).
        invoice_type: INVOICE, RETURN, PAYMENT, ADJUSTMENT, REBATE (optional).
        """
        t0 = time.time()
        cap = _clamp_limit(limit)
        try:
            days_n = int(days)
        except (TypeError, ValueError):
            days_n = 90
        days_n = max(1, min(days_n, 365))
        try:
            self.ensure_session()
        except Exception as e:
            return {
                "ok": False,
                "logged_in": False,
                "items": [],
                "count": 0,
                "error": str(e),
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        pin = self._account_pin()
        if not pin:
            return {
                "ok": False,
                "items": [],
                "count": 0,
                "error": "no account PIN on session header",
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        today = date.today()
        end = end_date.strip() if isinstance(end_date, str) and end_date.strip() else today.isoformat()
        start = (
            start_date.strip()
            if isinstance(start_date, str) and start_date.strip()
            else (today - timedelta(days=days_n)).isoformat()
        )
        params: Dict[str, Any] = {
            "pin": pin,
            "startDate": start,
            "endDate": end,
            "pageSize": cap,
            "pageBeginId": "0",
            "pageEndId": "0",
        }
        itype = (invoice_type or "").strip().upper()
        if itype and itype not in ("ALL", "*"):
            params["invoiceType"] = itype
        r = self._get(
            "/ecomm/b2b/v1/transactions",
            params=params,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if r.status_code >= 400:
            return {
                "ok": False,
                "items": [],
                "count": 0,
                "error": f"HTTP {r.status_code}: {r.text[:200]}",
                "start_date": start,
                "end_date": end,
                "limit": cap,
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        parsed = parse_transaction_list(r.json() or {}, limit=cap)
        parsed["logged_in"] = True
        parsed["start_date"] = start
        parsed["end_date"] = end
        parsed["elapsed_ms"] = int((time.time() - t0) * 1000)
        return parsed


def client_from_env() -> AzProClient:
    cookies = None
    cookies_file = os.getenv("AZPRO_COOKIES_FILE") or str(DEFAULT_COOKIES)
    try:
        p = Path(cookies_file)
        if p.is_file():
            cookies = json.loads(p.read_text(encoding="utf-8"))
            print(f"[azpro] loaded {len(cookies)} cookies from {p}")
    except Exception as e:
        print(f"[azpro] cookie load failed: {e}")
    raw = os.getenv("AZPRO_COOKIES")
    if cookies is None and raw:
        try:
            cookies = json.loads(raw)
        except Exception:
            cookies = {}
            for part in raw.split(";"):
                if "=" in part:
                    k, v = [x.strip() for x in part.split("=", 1)]
                    cookies[k] = v
    return AzProClient(cookies=cookies or {})
