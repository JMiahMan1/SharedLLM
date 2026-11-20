"""3-Tier Unknown / Rare Species Lookup for Jarvis Garden (Phase 3).

Tier 1: offline ``/data/plants-seed.db`` first — instant hit if already known.
Tier 2: optional live open botanical lookup (Wikipedia REST summary, public
HTTPS only, strict timeout, graceful fallback) + *"Ask Jarvis"* to draft a
structured care profile for user review — NEVER auto-saved unreviewed.
Tier 3: user reviews and saves permanently into local ``custom_species``.

Endpoints:
  * ``POST /species/lookup`` — accepts ``{"name": "...", "category": "..."}``;
    returns ``{"draft": true, "source": ..., "species": {...}, "warnings": [...]}``
    for user review (adult/admin only).
  * ``POST /species/custom`` — validates enums, winter_multiplier >= 1.0, and
    low-acid canning safety; saves permanently into ``custom_species``
    (adult/admin only).
  * ``DELETE /species/custom/{key}`` — removes a custom species (adult/admin only).

``resolve_species(session, key)`` unifies resolution across both the read-only
seed and ``custom_species`` so ``GET /species/search``, ``GET /species/{key}``,
``create_plant``, ``/today``, and digests all see custom species seamlessly.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from fastapi import HTTPException
from sqlmodel import Session, select

from services.common import weather_client as _weather  # noqa: F401 (kept for symmetry)
from services import config
from services.garden.models import (
    CustomSpecies,
    CustomSpeciesIn,
    utcnow,
)

log = logging.getLogger("garden.species_lookup")

# --------------------------------------------------------------------------- constants

#: Wikipedia REST summary endpoint (public HTTPS only).
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

#: Strict timeout for any external HTTP call in this module.
_LOOKUP_TIMEOUT = aiohttp.ClientTimeout(total=12.0)
_UA = "Jarvis-OS/1.0 (garden species lookup; self-hosted)"

#: Care-profile schema enums the LLM draft must respect.
_VALID_CATEGORY = frozenset({"houseplant", "herb", "vegetable", "fruit", "flower"})
_VALID_WATERING_METHOD = frozenset(
    {"top_drain", "bottom_water", "soak_and_dry", "evenly_moist", "bark_soak", "air_bath"}
)
_VALID_DRYNESS_CUE = frozenset(
    {"surface_damp", "top_1in_dry", "top_2in_dry", "mostly_dry", "bone_dry"}
)
_VALID_WATER_QUALITY = frozenset({"tap_ok", "filtered_or_rain_preferred"})
_VALID_LIGHT_LEVEL = frozenset({"low", "medium", "bright_indirect", "bright_direct", "full_sun"})
_VALID_HUMIDITY = frozenset({"low_tolerant", "average", "high_humidity"})
_VALID_MISTING = frozenset({"beneficial", "optional", "avoid_misting"})
_VALID_SOIL_MIX = frozenset(
    {"standard_potting", "chunky_aroid", "cactus_succulent", "orchid_bark",
     "african_violet", "moisture_retentive"}
)
_VALID_FROST = frozenset({"tender", "half_hardy", "hardy"})
_VALID_TOXICITY = frozenset({"non_toxic", "mild_irritant", "toxic_pets", "toxic_all"})
_VALID_ACIDITY = frozenset({"high_acid", "low_acid"})
_VALID_SOURCE = frozenset({"manual", "wiki_ai_draft"})

#: Low-acid crops that require pressure canning (never bare water_bath_canning).
KNOWN_LOW_ACID_KEYS = frozenset(
    {"asparagus", "beans", "beets", "carrots", "corn", "peas", "potatoes", "squash", "sweet_potato"}
)


# --------------------------------------------------------------------------- SSRF-safe public HTTP


async def _public(host: str) -> bool:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


async def _fetch_json(url: str) -> dict | None:
    """Fetch JSON from a public HTTPS URL only; None on any failure."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        log.info("[Garden lookup] refused non-https URL: %s", url)
        return None
    if parsed.username or parsed.password:
        log.info("[Garden lookup] refused credentials in URL: %s", url)
        return None
    if not await _public(parsed.hostname):
        log.info("[Garden lookup] refused non-public host: %s", url)
        return None
    try:
        async with aiohttp.ClientSession(timeout=_LOOKUP_TIMEOUT,
                                         headers={"User-Agent": _UA}) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    log.info("[Garden lookup] %s -> HTTP %s", url, resp.status)
                    return None
                return await resp.json(content_type=None)
    except Exception as e:
        log.info("[Garden lookup] %s failed: %s", url, e)
        return None


# --------------------------------------------------------------------------- LLM draft (via gateway)


async def _draft_via_llm(name: str, category: str | None, wiki_summary: str | None,
                         warnings: list[str]) -> dict | None:
    """Ask Jarvis (via the gateway LLM) to draft a structured care profile.

    Returns a species-shaped dict or None when the LLM is unavailable.
    Never raises: callers degrade gracefully to a manual-entry prompt.
    """
    body = {
        "model": None,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Jarvis, a careful household gardening assistant. "
                    "Draft a complete, structured plant care profile for the user to review. "
                    "Return ONLY a JSON object with these exact keys and enum values:\n"
                    "category: houseplant | herb | vegetable | fruit | flower\n"
                    "common_name, scientific_name, family, description (strings)\n"
                    "watering_interval_days (number > 0), winter_multiplier (number >= 1.0)\n"
                    "watering_method: top_drain | bottom_water | soak_and_dry | evenly_moist | bark_soak | air_bath\n"
                    "dryness_cue: surface_damp | top_1in_dry | top_2in_dry | mostly_dry | bone_dry\n"
                    "water_quality: tap_ok | filtered_or_rain_preferred\n"
                    "light_level: low | medium | bright_indirect | bright_direct | full_sun\n"
                    "temp_min_f, temp_max_f (numbers)\n"
                    "cold_draft_sensitive (bool), humidity_band: low_tolerant | average | high_humidity\n"
                    "misting_rule: beneficial | optional | avoid_misting\n"
                    "soil_mix: standard_potting | chunky_aroid | cactus_succulent | orchid_bark | african_violet | moisture_retentive\n"
                    "fertilizing (string), repot_interval_months (number)\n"
                    "grooming_propagation (string), common_pests (string), symptoms (string)\n"
                    "frost_tolerance: tender | half_hardy | hardy\n"
                    "usda_zone_min, usda_zone_max (ints)\n"
                    "days_to_maturity (int), tall_trellis (0 or 1)\n"
                    "toxicity_level: non_toxic | mild_irritant | toxic_pets | toxic_all\n"
                    "toxicity_notes (string)\n"
                    "preservation_methods (string), usda_acidity_class: high_acid | low_acid\n"
                    "Never auto-save: mark every value as a draft for review. "
                    "If unsure about safety, flag it in warnings."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Plant name: {name}\n"
                    f"Category: {category or 'unknown'}\n"
                    f"Wikipedia summary: {wiki_summary or 'none available'}\n"
                ),
            },
        ],
        "temperature": 0.3,
    }
    try:
        from services.common.http import get_client

        client = get_client()
        gateway = config.GATEWAY_URL or "http://gateway:11435"
        async with client.post(f"{gateway.rstrip('/')}/api/llm/generate",
                               json=body,
                               headers={"X-Internal-Secret": config.INTERNAL_SECRET},
                               timeout=_LOOKUP_TIMEOUT) as resp:
            if resp.status != 200:
                log.info("[Garden lookup] LLM draft HTTP %s", resp.status)
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        log.info("[Garden lookup] LLM draft unavailable: %s", e)
        return None

    text = data.get("text") or data.get("content") or ""
    if not text:
        return None
    try:
        # Extract a JSON object from the response (may be wrapped in fences).
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        draft = json.loads(m.group(0))
    except (ValueError, KeyError):
        log.info("[Garden lookup] LLM draft was not valid JSON")
        return None

    # Sanitize enums: drop any value that isn't in our schema.
    if draft.get("category") not in _VALID_CATEGORY:
        draft["category"] = category or "houseplant"
        warnings.append("category normalized to default")
    if draft.get("watering_method") not in _VALID_WATERING_METHOD:
        draft["watering_method"] = None
        warnings.append("watering_method not recognized")
    if draft.get("dryness_cue") not in _VALID_DRYNESS_CUE:
        draft["dryness_cue"] = None
        warnings.append("dryness_cue not recognized")
    if draft.get("water_quality") not in _VALID_WATER_QUALITY:
        draft["water_quality"] = None
        warnings.append("water_quality not recognized")
    if draft.get("light_level") not in _VALID_LIGHT_LEVEL:
        draft["light_level"] = None
        warnings.append("light_level not recognized")
    if draft.get("humidity_band") not in _VALID_HUMIDITY:
        draft["humidity_band"] = None
        warnings.append("humidity_band not recognized")
    if draft.get("misting_rule") not in _VALID_MISTING:
        draft["misting_rule"] = None
        warnings.append("misting_rule not recognized")
    if draft.get("soil_mix") not in _VALID_SOIL_MIX:
        draft["soil_mix"] = None
        warnings.append("soil_mix not recognized")
    if draft.get("frost_tolerance") not in _VALID_FROST:
        draft["frost_tolerance"] = "hardy"
        warnings.append("frost_tolerance defaulted to hardy")
    if draft.get("toxicity_level") not in _VALID_TOXICITY:
        draft["toxicity_level"] = None
        warnings.append("toxicity_level not recognized")
    if draft.get("usda_acidity_class") not in _VALID_ACIDITY:
        draft["usda_acidity_class"] = None
        warnings.append("usda_acidity_class not recognized")
    try:
        wm = float(draft.get("winter_multiplier") or 1.0)
        if wm < 1.0:
            draft["winter_multiplier"] = 1.0
            warnings.append("winter_multiplier must be >= 1.0; set to 1.0")
        else:
            draft["winter_multiplier"] = round(wm, 2)
    except (TypeError, ValueError):
        draft["winter_multiplier"] = 1.0
        warnings.append("winter_multiplier invalid; set to 1.0")
    try:
        bid = float(draft.get("watering_interval_days") or 0)
        if bid <= 0:
            draft["watering_interval_days"] = 7.0
            warnings.append("watering_interval_days invalid; set to 7 days")
        else:
            draft["watering_interval_days"] = round(bid, 1)
    except (TypeError, ValueError):
        draft["watering_interval_days"] = 7.0
        warnings.append("watering_interval_days invalid; set to 7 days")

    draft["source"] = "wiki_ai_draft"
    draft["reviewed_by"] = None
    draft["reviewed_at"] = None
    return draft


# --------------------------------------------------------------------------- Tier 1: seed + custom_species


def _seed_get(key: str) -> dict | None:
    """Look up one species in the read-only botanical seed."""
    from services.garden import plant_seed

    try:
        ps = plant_seed.PlantSeed(
            __import__("os").getenv("PLANT_SEED_PATH") or "/data/plants-seed.db"
        )
        return ps.get(key)
    except Exception as e:
        log.info("[Garden lookup] seed unavailable: %s", e)
        return None


def resolve_species(session: Session, key: str) -> dict | None:
    """Resolve a species key across custom_species and the botanical seed.

    ``custom_species`` (permanent, user-reviewed) takes precedence when the
    same key exists in both; otherwise falls back to ``/data/plants-seed.db``.
    Returns None when unknown everywhere.  Degrades gracefully when the
    ``custom_species`` table does not exist yet.
    """
    try:
        row = session.exec(select(CustomSpecies).where(CustomSpecies.key == key)).first()
    except Exception as e:
        log.info("[Garden lookup] custom_species table not ready: %s", e)
        row = None
    if row is not None:
        return {
            "key": row.key,
            "category": row.category,
            "common_name": row.common_name,
            "scientific_name": row.scientific_name,
            "family": row.family,
            "description": row.description,
            "watering_interval_days": row.watering_interval_days,
            "winter_multiplier": row.winter_multiplier,
            "watering_method": row.watering_method,
            "dryness_cue": row.dryness_cue,
            "water_quality": row.water_quality,
            "light_level": row.light_level,
            "temp_min_f": row.temp_min_f,
            "temp_max_f": row.temp_max_f,
            "cold_draft_sensitive": row.cold_draft_sensitive,
            "humidity_band": row.humidity_band,
            "misting_rule": row.misting_rule,
            "soil_mix": row.soil_mix,
            "fertilizing": row.fertilizing,
            "repot_interval_months": row.repot_interval_months,
            "grooming_propagation": row.grooming_propagation,
            "common_pests": row.common_pests,
            "symptoms": row.symptoms,
            "frost_tolerance": row.frost_tolerance,
            "usda_zone_min": row.usda_zone_min,
            "usda_zone_max": row.usda_zone_max,
            "days_to_maturity": row.days_to_maturity,
            "tall_trellis": row.tall_trellis,
            "toxicity_level": row.toxicity_level,
            "toxicity_notes": row.toxicity_notes,
            "preservation_methods": row.preservation_methods,
            "usda_acidity_class": row.usda_acidity_class,
            "source": row.source,
            "reviewed_by": row.reviewed_by,
            "reviewed_at": row.reviewed_at,
            "custom": True,
        }
    return _seed_get(key)


# --------------------------------------------------------------------------- validate + normalize


def _normalize_key(common_name: str) -> str:
    """Derive a stable key from a common name: lowercase, alnum+underscore."""
    key = re.sub(r"[^a-z0-9]+", "_", common_name.strip().lower()).strip("_")
    return key or "custom_species"


def _validate_custom(body: CustomSpeciesIn, warnings: list[str]) -> dict[str, Any]:
    """Validate a custom-species payload. Returns the normalized data dict."""
    data: dict[str, Any] = {}

    common_name = (body.common_name or "").strip()
    if not common_name:
        raise HTTPException(status_code=400, detail="common_name is required")
    data["common_name"] = common_name

    key = (body.key or _normalize_key(common_name)).strip()
    if not re.fullmatch(r"[a-z0-9_]+", key):
        raise HTTPException(status_code=400, detail="key must be lowercase letters, digits, underscores")
    data["key"] = key

    category = (body.category or "houseplant").strip().lower()
    if category not in _VALID_CATEGORY:
        raise HTTPException(status_code=400, detail=f"category must be one of {sorted(_VALID_CATEGORY)}")
    data["category"] = category

    wm = body.winter_multiplier
    if wm is not None:
        try:
            wm = float(wm)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="winter_multiplier must be a number")
        if wm < 1.0:
            raise HTTPException(status_code=400, detail="winter_multiplier must be >= 1.0")
        data["winter_multiplier"] = round(wm, 2)
    else:
        data["winter_multiplier"] = 1.0

    # Low-acid canning safety: reject low_acid + bare water_bath_canning.
    acidity = body.usda_acidity_class
    methods = (body.preservation_methods or "")
    if acidity == "low_acid" and "water_bath_canning" in methods and "pressure_canning" not in methods:
        raise HTTPException(
            status_code=400,
            detail="Low-acid crops require pressure canning; water_bath_canning alone is unsafe.",
        )
    data["usda_acidity_class"] = acidity

    # Enum validation (warnings only for out-of-enum; caller can still save).
    for field, valid, out in (
        ("watering_method", _VALID_WATERING_METHOD, None),
        ("dryness_cue", _VALID_DRYNESS_CUE, None),
        ("water_quality", _VALID_WATER_QUALITY, None),
        ("light_level", _VALID_LIGHT_LEVEL, None),
        ("humidity_band", _VALID_HUMIDITY, None),
        ("misting_rule", _VALID_MISTING, None),
        ("soil_mix", _VALID_SOIL_MIX, None),
        ("frost_tolerance", _VALID_FROST, "hardy"),
        ("toxicity_level", _VALID_TOXICITY, None),
    ):
        val = getattr(body, field)
        if val is not None and val not in valid:
            warnings.append(f"{field} '{val}' not in recognized enums")
        data[field] = val

    for field in ("scientific_name", "family", "description", "toxicity_notes",
                  "preservation_methods", "fertilizing", "grooming_propagation",
                  "common_pests", "symptoms"):
        data[field] = getattr(body, field)

    for field in ("temp_min_f", "temp_max_f", "repot_interval_months", "usda_zone_min",
                  "usda_zone_max", "days_to_maturity"):
        val = getattr(body, field)
        if val is not None:
            try:
                data[field] = float(val) if field not in ("usda_zone_min", "usda_zone_max",
                                                          "days_to_maturity") else int(val)
            except (TypeError, ValueError):
                warnings.append(f"{field} invalid; ignored")
                data[field] = None
        else:
            data[field] = None

    tall = body.tall_trellis
    data["tall_trellis"] = int(tall) if tall is not None else 0
    data["cold_draft_sensitive"] = bool(body.cold_draft_sensitive) if body.cold_draft_sensitive is not None else False
    data["source"] = body.source or "manual"
    if data["source"] not in _VALID_SOURCE:
        data["source"] = "manual"
    data["reviewed_by"] = body.reviewed_by
    data["reviewed_at"] = body.reviewed_at or utcnow()
    return data


# --------------------------------------------------------------------------- endpoint handlers


async def handle_species_lookup(
    session: Session,
    body: dict[str, Any],
    current_user: str,
    is_admin: bool,
) -> dict[str, Any]:
    """POST /species/lookup: 3-tier unknown species lookup (adult/admin only)."""
    if not is_admin:
        raise HTTPException(status_code=403, detail="Adults and admins only: look up unknown species.")

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    category = body.get("category")

    # Tier 1: seed + custom_species.
    existing = resolve_species(session, _normalize_key(name))
    if existing is None:
        # Try the raw name as-is too (some keys may match directly).
        existing = resolve_species(session, name)
    if existing is not None:
        return {
            "draft": False,
            "source": "seed" if not existing.get("custom") else "custom_species",
            "species": existing,
            "warnings": [],
        }

    warnings: list[str] = []

    # Tier 2a: Wikipedia REST summary (public HTTPS, strict timeout).
    wiki_title = name.replace(" ", "_")
    wiki = await _fetch_json(WIKIPEDIA_SUMMARY_URL.format(title=wiki_title))
    wiki_summary = (wiki or {}).get("extract") if wiki else None
    if wiki is None:
        warnings.append("Wikipedia lookup unavailable — draft will be based on name only.")

    # Tier 2b: ask Jarvis (LLM) to draft a structured care profile.
    draft = await _draft_via_llm(name, category, wiki_summary, warnings)
    if draft is None:
        # Fallback: minimal manual-entry draft.
        draft = {
            "key": _normalize_key(name),
            "category": category or "houseplant",
            "common_name": name,
            "scientific_name": None,
            "family": None,
            "description": None,
            "watering_interval_days": 7.0,
            "winter_multiplier": 1.0,
            "watering_method": None,
            "dryness_cue": None,
            "water_quality": None,
            "light_level": None,
            "temp_min_f": None,
            "temp_max_f": None,
            "cold_draft_sensitive": False,
            "humidity_band": None,
            "misting_rule": None,
            "soil_mix": None,
            "fertilizing": None,
            "repot_interval_months": None,
            "grooming_propagation": None,
            "common_pests": None,
            "symptoms": None,
            "frost_tolerance": "hardy",
            "usda_zone_min": None,
            "usda_zone_max": None,
            "days_to_maturity": None,
            "tall_trellis": 0,
            "toxicity_level": None,
            "toxicity_notes": None,
            "preservation_methods": None,
            "usda_acidity_class": None,
            "source": "wiki_ai_draft",
            "reviewed_by": None,
            "reviewed_at": None,
            "custom": True,
        }
        warnings.append("LLM draft unavailable — basic manual draft shown; review all fields before saving.")

    return {
        "draft": True,
        "source": "wiki_ai_draft",
        "species": draft,
        "warnings": warnings,
    }


async def handle_species_custom(
    session: Session,
    body: CustomSpeciesIn,
    current_user: str,
    is_admin: bool,
) -> dict[str, Any]:
    """POST /species/custom: save a user-reviewed custom species (adult/admin only)."""
    if not is_admin:
        raise HTTPException(status_code=403, detail="Adults and admins only: save custom species.")

    warnings: list[str] = []
    data = _validate_custom(body, warnings)

    key = data["key"]
    existing = session.exec(select(CustomSpecies).where(CustomSpecies.key == key)).first()
    now = utcnow()
    if existing is not None:
        for field, value in data.items():
            if field == "key":
                continue
            setattr(existing, field, value)
        existing.reviewed_at = now
        existing.updated_at = now
        session.add(existing)
    else:
        row = CustomSpecies(**{k: v for k, v in data.items() if k in CustomSpecies.__table__.columns.keys()})
        session.add(row)
    session.commit()
    log.info("[Garden lookup] custom species saved: %s (source=%s)", key, data["source"])
    return {
        "ok": True,
        "key": key,
        "source": data["source"],
        "warnings": warnings,
    }


async def handle_species_custom_delete(
    session: Session,
    key: str,
    current_user: str,
    is_admin: bool,
) -> dict[str, Any]:
    """DELETE /species/custom/{key}: remove a custom species (adult/admin only)."""
    if not is_admin:
        raise HTTPException(status_code=403, detail="Adults and admins only: delete custom species.")
    row = session.exec(select(CustomSpecies).where(CustomSpecies.key == key)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Custom species {key!r} not found")
    session.delete(row)
    session.commit()
    return {"ok": True, "key": key}
