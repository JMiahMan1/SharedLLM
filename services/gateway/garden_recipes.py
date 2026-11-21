"""Garden-to-recipes bridge for the Gateway (Phase 4).

Matches harvest-ready garden produce against the family's ``Recipes/``
cookbook ingredients, and drafts new family-sized recipes for surplus
crops via Jarvis's LLM. Kept entirely in the Gateway: Nextcloud/Cookbook
credentials never reach ``services/garden``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import aiohttp
from fastapi import HTTPException, Request

from services.gateway import cookbook
from services.gateway.config import GARDEN_SVC, INTERNAL_SECRET
from services.gateway.main import shared_http_client

log = logging.getLogger("garden_recipes")

_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: How many cookbook recipes are read at once when matching produce.
_READ_CONCURRENCY = 8


def _garden_headers(caller: Any) -> dict[str, str]:
    """Internal-secret + acting-user headers required by services/garden."""
    user = (getattr(caller, "user", None) or "household").strip().lower()
    return {
        "X-Internal-Secret": INTERNAL_SECRET,
        "X-User-Id": user,
    }

#: Word-boundary regex for ingredient matching.  ``\b`` boundaries prevent
#: false matches like ``"pea"`` matching ``"peanut"`` or ``"peach"``.
_INGREDIENT_RE = re.compile(r"\b[a-z][a-z0-9 ]{2,}\b", re.IGNORECASE)


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace for comparison."""
    return " ".join(str(text or "").lower().split())


def _ingredient_words(text: str) -> set[str]:
    """Extract meaningful ingredient words from a recipe ingredient line."""
    words = set()
    for m in _INGREDIENT_RE.finditer(_normalize(text)):
        w = m.group(0).strip()
        if len(w) >= 3:
            words.add(w)
    return words


def _word_boundary_match(ingredient_line: str, alias: str) -> bool:
    """True when ``alias`` appears as a whole word in ``ingredient_line``.

    Uses word boundaries so ``"pea"`` does NOT match ``"peanut"`` or
    ``"peach"``, while ``"basil"`` matches ``"- [ ] 1 cup fresh basil leaves"``.
    """
    line = _normalize(ingredient_line)
    alias_norm = _normalize(alias)
    if not alias_norm:
        return False
    # Multi-word alias: every word must appear as a whole word.
    for part in alias_norm.split():
        if not re.search(r"\b" + re.escape(part) + r"\b", line):
            return False
    return True


# ── Recipe matching ──────────────────────────────────────────────────────

async def recipe_matches(run: Any, caller: Any) -> dict:
    """GET matches: ready garden ingredients -> matching cookbook recipes.

    Calls garden's ``GET /harvest/ready`` for ready produce, then reads the
    family cookbook and matches each recipe's ingredients against ready
    plants' ``culinary_aliases`` using word-boundary matching.

    Returns ``{ ready_ingredients: [...], matches: [...] }`` ranked by
    ``match_count`` desc. Degrades to ``matches: []`` when cookbook or
    garden is unavailable.
    """
    # --- Get ready-to-harvest produce from garden service ---
    ready_ingredients: list[dict] = []
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{GARDEN_SVC.rstrip('/')}/harvest/ready",
                headers=_garden_headers(caller),
                timeout=_TIMEOUT,
            )
            if resp.status == 200:
                data = await resp.json(content_type=None)
                for item in (data or {}).get("ready", []):
                    ready_ingredients.append({
                        "plant_id": item.get("plant_id"),
                        "nickname": item.get("nickname"),
                        "common_name": item.get("common_name"),
                        "category": item.get("category"),
                        "species_ref": item.get("species_ref"),
                        "culinary_aliases": item.get("culinary_aliases", []),
                        "in_cooldown": item.get("in_cooldown", False),
                        "pinching_bolting_notes": item.get("pinching_bolting_notes"),
                        "herb_preservation_tips": item.get("herb_preservation_tips"),
                    })
    except Exception as e:
        log.info("[GardenRecipes] garden harvest/ready unavailable: %s", e)

    # --- Read cookbook recipes ---
    try:
        listed = await cookbook.list_recipes(run, caller)
    except Exception as e:
        log.info("[GardenRecipes] cookbook list unavailable: %s", e)
        listed = {"status": "FAILURE", "notes": []}

    if listed.get("status") != "SUCCESS":
        return {
            "ready_ingredients": ready_ingredients,
            "matches": [],
            "note": "Cookbook unavailable",
        }

    recipe_notes = (listed.get("detail") or {}).get("notes", []) or []
    matches: list[dict] = []
    paths = [note.get("path", "") for note in recipe_notes if cookbook.is_recipe_path(note.get("path", ""))]
    if not ready_ingredients or not paths:
        # Nothing is ready to match: reading every recipe would answer nothing.
        return {"ready_ingredients": ready_ingredients, "matches": []}

    gate = asyncio.Semaphore(_READ_CONCURRENCY)

    async def read_one(path: str) -> tuple[str, dict | None]:
        async with gate:
            try:
                return path, await cookbook.read_recipe(run, caller, path)
            except Exception as exc:
                log.warning("[GardenRecipes] could not read recipe %s: %s", path, exc)
                return path, None

    for path, read in await asyncio.gather(*(read_one(path) for path in paths)):
        if read is None or read.get("status") != "SUCCESS":
            continue
        body = read.get("message", "")
        if not body:
            continue

        # Extract ingredient lines from the recipe body.
        ingredient_lines: list[str] = []
        for line in body.split("\n"):
            stripped = line.strip()
            # Match checkbox lines "- [ ] ..." or "- [x] ..." or plain bullets "- ..."
            m = re.match(r"^-\s*\[[ xX]\]\s+(.+?)\s*$", stripped)
            if m:
                ingredient_lines.append(m.group(1))
                continue
            m2 = re.match(r"^-\s+(?!\[)(.+?)\s*$", stripped)
            if m2:
                ingredient_lines.append(m2.group(1))

        if not ingredient_lines:
            continue

        # Score each ready ingredient against this recipe's ingredients.
        matched_ingredients: list[str] = []
        for ri in ready_ingredients:
            for alias in ri.get("culinary_aliases", []):
                for iline in ingredient_lines:
                    if _word_boundary_match(iline, alias):
                        matched_ingredients.append(alias)
                        break
                else:
                    continue
                break

        if not matched_ingredients:
            continue

        # Deduplicate while preserving order.
        seen: set[str] = set()
        matched_unique: list[str] = []
        for m_ing in matched_ingredients:
            if m_ing not in seen:
                seen.add(m_ing)
                matched_unique.append(m_ing)

        matches.append({
            "path": path,
            "title": read.get("detail", {}).get("title") or path.rsplit("/", 1)[-1].removesuffix(".md"),
            "matched_ingredients": matched_unique,
            "match_count": len(matched_unique),
            "tags": read.get("detail", {}).get("tags", []),
            "servings": read.get("detail", {}).get("servings"),
        })

    # Rank by match_count desc.
    matches.sort(key=lambda m: (-m["match_count"], m["title"]))

    return {
        "ready_ingredients": ready_ingredients,
        "matches": matches,
    }


# ── Recipe drafting ──────────────────────────────────────────────────────

async def _household_servings(garden_hdrs: dict[str, str]) -> int:
    """The household size from the garden profile, or a 503 that says why not."""
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{GARDEN_SVC.rstrip('/')}/profile",
                headers=garden_hdrs,
                timeout=_TIMEOUT,
            )
            status = resp.status
            profile = await resp.json(content_type=None) if status == 200 else None
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        log.warning("[GardenRecipes] the garden profile could not be read: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="The garden is unavailable, so the household size is unknown. Say how many servings.",
        ) from None
    if status != 200 or not isinstance(profile, dict):
        log.warning("[GardenRecipes] the garden profile answered HTTP %s", status)
        raise HTTPException(
            status_code=503,
            detail=f"The garden profile could not be read (HTTP {status}). Say how many servings.",
        )
    size = profile.get("family_size")
    if not size:
        raise HTTPException(
            status_code=503,
            detail=profile.get("family_size_detail")
            or "The household size is unknown right now. Say how many servings.",
        )
    return int(size)


async def recipe_draft(
    run: Any,
    caller: Any,
    body: dict,
    request: Request,
) -> dict:
    """POST draft: LLM-drafted family-sized recipe (unsaved preview).

    Accepts ``{ ingredients?, focus_crop?, servings? }``.  Defaults
    servings to household ``garden_profile.family_size`` and ingredients
    to ready garden produce.  Returns markdown in Nextcloud Notes format
    for user review -- NOT saved to the cookbook.
    """
    # Servings: the caller's number, else the household size the garden profile
    # resolves (an explicit override, else Identity's roster). There is no
    # fallback: a recipe for a made-up family is worse than a clear error.
    garden_hdrs = _garden_headers(caller)
    servings = body.get("servings")
    if servings is None:
        servings = await _household_servings(garden_hdrs)
    try:
        servings = int(servings)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="servings must be a whole number") from None
    if servings < 1:
        raise HTTPException(status_code=422, detail="servings must be at least 1")

    # Resolve ingredients: from body or ready garden produce.
    ingredients = body.get("ingredients") or []
    focus_crop = body.get("focus_crop") or ""
    if not ingredients:
        try:
            async with shared_http_client() as client:
                resp = await client.get(
                    f"{GARDEN_SVC.rstrip('/')}/harvest/ready",
                    headers=garden_hdrs,
                    timeout=_TIMEOUT,
                )
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for item in (data or {}).get("ready", []):
                        for alias in item.get("culinary_aliases", []):
                            ingredients.append(alias)
        except Exception:
            pass

    if not ingredients:
        ingredients = [focus_crop] if focus_crop else ["garden produce"]

    ingredients_str = ", ".join(dict.fromkeys(ingredients))  # dedupe, keep order

    # Build the LLM prompt for Nextcloud Notes markdown format.
    prompt = (
        f"Write a family-sized recipe ({servings} servings) using these "
        f"garden-fresh ingredients: {ingredients_str}.\n"
        f"Focus crop: {focus_crop or 'any of the above'}.\n"
        f"Output in standard Nextcloud Notes / Recipes/ markdown format only:\n"
        f"1. A title line starting with '# '\n"
        f"2. A second line 'Category: Recipes'\n"
        f"3. A blank line\n"
        f"4. Optional '#tags' inline labels\n"
        f"5. Checkbox ingredient lines: '- [ ] <quantity> <unit> <name>'\n"
        f"6. Numbered steps: '1. ...', '2. ...', etc.\n"
        f"7. A final 'Serves: {servings}' line\n"
        f"Do not include any introductory or concluding chat text. Just the markdown."
    )

    # Call the gateway's own LLM endpoint.
    gateway_base = (os.getenv("GATEWAY_INTERNAL_URL") or "http://127.0.0.1:11435").rstrip("/")
    llm_headers: dict[str, str] = {"X-Internal-Secret": INTERNAL_SECRET}
    if request is not None:
        auth = request.headers.get("Authorization")
        if auth:
            llm_headers["Authorization"] = auth
    try:
        async with shared_http_client() as client:
            resp = await client.post(
                f"{gateway_base}/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [
                        {"role": "system", "content": "You write family recipes in Nextcloud Notes markdown format."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                },
                headers=llm_headers,
                timeout=_TIMEOUT,
            )
            if resp.status != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"LLM returned HTTP {resp.status}",
                )
            llm_data = await resp.json(content_type=None)
    except HTTPException:
        raise
    except Exception as e:
        log.info("[GardenRecipes] LLM draft failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not draft recipe")

    # Extract the drafted markdown.
    choices = llm_data.get("choices") or []
    markdown = ""
    if choices:
        message = choices[0].get("message") or {}
        markdown = message.get("content") or ""

    if not markdown:
        raise HTTPException(status_code=502, detail="LLM returned empty recipe")

    return {
        "status": "SUCCESS",
        "draft": True,
        "servings": servings,
        "ingredients": ingredients,
        "focus_crop": focus_crop,
        "markdown": markdown,
        "note": "Unsaved preview. Save via /api/recipes/cookbook/save on approval.",
    }
